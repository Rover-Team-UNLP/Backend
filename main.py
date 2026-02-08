"""
Servidor FastAPI como puente WebSocket entre el frontend y la ESP32.
- GET /ws: clientes (navegador). Reciben esp_status y ack/error.
- GET /ws/esp: dispositivo (ESP32). Recibe los comandos que envía el front.
- POST /video/upload: recibe frames MJPEG de la ESP.
- GET /video/stream: sirve MJPEG stream al frontend.
- POST /chat: chat con IA para controlar el rover.
"""

import asyncio
import json
import logging
import math
import os
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI

# Cargar variables de entorno desde .env.local
load_dotenv(".env.local")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Rover WebSocket Bridge")

# CORS - permitir requests desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cliente OpenAI
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Contador de IDs para comandos enviados por la IA
_ai_command_id = 10000

# Tools para OpenAI
ROVER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_forward",
            "description": "Mueve el rover hacia adelante. Usa esto cuando el usuario quiera avanzar, ir hacia adelante, o moverse en esa dirección.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_backward",
            "description": "Mueve el rover hacia atrás. Usa esto cuando el usuario quiera retroceder, ir hacia atrás, o moverse en esa dirección.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_left",
            "description": "Gira el rover hacia la izquierda. Usa esto cuando el usuario quiera girar a la izquierda.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_right",
            "description": "Gira el rover hacia la derecha. Usa esto cuando el usuario quiera girar a la derecha.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_video",
            "description": "Muestra el video de la cámara del rover. Usa esto cuando el usuario quiera ver qué está viendo el rover, ver la cámara, o ver el video.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hide_video",
            "description": "Oculta el video de la cámara. Usa esto cuando el usuario ya no necesite ver el video o quiera cerrar la cámara.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

SYSTEM_PROMPT = """Eres un asistente que controla un rover/robot. Responde siempre en español y sé breve (1-2 oraciones).

Tienes herramientas para controlar el rover:

Movimiento:
- move_forward: avanzar
- move_backward: retroceder  
- move_left: girar izquierda
- move_right: girar derecha

Video:
- show_video: mostrar la cámara del rover
- hide_video: ocultar la cámara

Cuando el usuario pida moverse, usa la herramienta correspondiente y confirma brevemente.
Si el usuario quiere ver qué está viendo el rover o ver la cámara, usa show_video.
Si el usuario da instrucciones complejas (ej: "avanza y luego gira"), ejecuta los movimientos uno por uno."""

# Estado: una conexión ESP, varias conexiones front
esp_ws: WebSocket | None = None
esp_host: str | None = None
clients: set[WebSocket] = set()
_lock = asyncio.Lock()

# Estado de video: último frame recibido de la ESP
latest_frame: bytes | None = None
frame_timestamp: float = 0
frame_event = asyncio.Event()
video_clients: int = 0


def validate_rover_message(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida que el mensaje cumpla el protocolo de la ESP: id 1-65535, cmd 0-3, params array <= 10 números."""
    if not isinstance(data.get("id"), (int, float)) or not (1 <= int(data["id"]) <= 65535):
        return False, "id inválido (debe ser 1-65535)"
    if not isinstance(data.get("cmd"), (int, float)) or not (0 <= int(data["cmd"]) <= 3):
        return False, "cmd inválido (debe ser 0-3)"
    params = data.get("params")
    if not isinstance(params, list):
        return False, "params debe ser un array"
    if len(params) > 10:
        return False, "params tiene más de 10 elementos"
    for i, p in enumerate(params):
        if not isinstance(p, (int, float)) or (isinstance(p, float) and math.isnan(p)):
            return False, f"params[{i}] debe ser un número"
    return True, None


async def broadcast_to_clients(message: dict[str, Any]) -> None:
    """Envía un mensaje a todos los clientes conectados."""
    text = json.dumps(message)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(text)
        except Exception as e:
            logger.warning("Error enviando a cliente: %s", e)
            dead.add(ws)
    for ws in dead:
        clients.discard(ws)


@app.websocket("/ws")
async def websocket_client(websocket: WebSocket) -> None:
    """Endpoint para el frontend (navegador)."""
    await websocket.accept()
    client_host = websocket.client.host if websocket.client else "unknown"
    async with _lock:
        clients.add(websocket)
        client_count = len(clients)
    logger.info("Cliente conectado desde %s (total: %d)", client_host, client_count)
    # Enviar estado actual de la ESP
    await websocket.send_text(
        json.dumps({"type": "esp_status", "connected": esp_ws is not None})
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("JSON inválido desde %s: %s", client_host, raw[:100])
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "JSON inválido"})
                )
                continue
            # Log del comando recibido
            logger.info(
                "CMD recibido: id=%s cmd=%s params=%s",
                data.get("id"),
                data.get("cmd"),
                data.get("params"),
            )
            ok, err = validate_rover_message(data)
            if not ok:
                logger.warning(
                    "Validación fallida desde %s: %s | data=%s", client_host, err, data
                )
                msg_id = data.get("id")
                payload = {"type": "error", "message": err or "Mensaje inválido"}
                if msg_id is not None:
                    payload["id"] = int(msg_id)
                await websocket.send_text(json.dumps(payload))
                continue
            msg_id = int(data["id"])
            async with _lock:
                current_esp = esp_ws
            if current_esp is None:
                logger.warning("Intento de enviar comando sin ESP conectada: id=%d", msg_id)
                await broadcast_to_clients(
                    {"type": "error", "message": "ESP no conectada", "id": msg_id}
                )
                continue
            try:
                await current_esp.send_text(raw)
                logger.info(
                    "Reenviado a ESP: id=%d cmd=%d params=%s",
                    msg_id,
                    int(data["cmd"]),
                    data.get("params"),
                )
                await broadcast_to_clients({"type": "ack", "id": msg_id})
            except Exception as e:
                logger.warning("Error reenviando a ESP: %s", e)
                await broadcast_to_clients(
                    {"type": "error", "message": "Error al enviar a la ESP", "id": msg_id}
                )
    except WebSocketDisconnect:
        pass
    finally:
        async with _lock:
            clients.discard(websocket)
            client_count = len(clients)
        logger.info("Cliente desconectado desde %s (total: %d)", client_host, client_count)


@app.websocket("/ws/esp")
async def websocket_esp(websocket: WebSocket) -> None:
    """Endpoint para la ESP32. Solo una conexión activa (la última)."""
    global esp_ws, esp_host
    await websocket.accept()
    new_host = websocket.client.host if websocket.client else "unknown"
    async with _lock:
        old = esp_ws
        esp_ws = websocket
        esp_host = new_host
    if old is not None:
        logger.info("Cerrando conexión ESP anterior")
        try:
            await old.close()
        except Exception:
            pass
    logger.info("ESP conectada desde %s", new_host)
    await broadcast_to_clients({"type": "esp_status", "connected": True})
    try:
        while True:
            msg = await websocket.receive_text()
            # La ESP no envía mensajes por ahora; si en el futuro envía ack/error, reenviar al front
            logger.debug("Mensaje de ESP (ignorado): %s", msg[:100] if msg else "")
    except WebSocketDisconnect:
        pass
    finally:
        disconnected_host = esp_host
        async with _lock:
            if esp_ws is websocket:
                esp_ws = None
                esp_host = None
        logger.info("ESP desconectada (era %s)", disconnected_host)
        await broadcast_to_clients({"type": "esp_status", "connected": False})


# ============================================================
# Video streaming: ESP envía frames, frontend los consume
# ============================================================

@app.post("/video/upload")
async def video_upload(request: Request) -> dict[str, str]:
    """
    Recibe stream MJPEG de la ESP (multipart/x-mixed-replace).
    La ESP mantiene la conexión abierta y envía frames continuamente.
    """
    global latest_frame, frame_timestamp
    
    client_host = request.client.host if request.client else "unknown"
    logger.info("ESP video conectada desde %s", client_host)
    
    boundary = b"--frame"
    buffer = b""
    frame_count = 0
    
    try:
        async for chunk in request.stream():
            buffer += chunk
            
            # Buscar frames completos en el buffer
            while True:
                # Buscar inicio de frame
                start_idx = buffer.find(boundary)
                if start_idx == -1:
                    break
                
                # Buscar el siguiente boundary (fin del frame actual)
                next_idx = buffer.find(boundary, start_idx + len(boundary))
                if next_idx == -1:
                    # Frame incompleto, esperar más datos
                    break
                
                # Extraer el frame
                frame_data = buffer[start_idx:next_idx]
                buffer = buffer[next_idx:]
                
                # Buscar el contenido JPEG (después de los headers)
                jpeg_start = frame_data.find(b"\r\n\r\n")
                if jpeg_start != -1:
                    jpeg_data = frame_data[jpeg_start + 4:].rstrip(b"\r\n")
                    if jpeg_data and len(jpeg_data) > 100:  # Validar que sea un frame real
                        latest_frame = jpeg_data
                        frame_timestamp = time.time()
                        frame_event.set()
                        frame_count += 1
                        if frame_count % 100 == 0:
                            logger.info("Video: %d frames recibidos", frame_count)
    except Exception as e:
        logger.warning("Error en video upload: %s", e)
    finally:
        logger.info("ESP video desconectada desde %s (frames: %d)", client_host, frame_count)
    
    return {"status": "ok", "frames": str(frame_count)}


async def generate_mjpeg_stream():
    """Genera stream MJPEG para el frontend."""
    global video_clients
    video_clients += 1
    logger.info("Cliente video conectado (total: %d)", video_clients)
    
    last_timestamp = 0
    
    try:
        while True:
            # Esperar nuevo frame o timeout
            try:
                await asyncio.wait_for(frame_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                # Sin frames, enviar frame vacío para mantener conexión
                continue
            
            frame_event.clear()
            
            if latest_frame is None:
                continue
            
            # Solo enviar si es un frame nuevo
            if frame_timestamp <= last_timestamp:
                continue
            
            last_timestamp = frame_timestamp
            
            # Enviar frame como MJPEG
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(latest_frame)).encode() + b"\r\n"
                b"\r\n" + latest_frame + b"\r\n"
            )
    except asyncio.CancelledError:
        pass
    finally:
        video_clients -= 1
        logger.info("Cliente video desconectado (total: %d)", video_clients)


@app.get("/video/stream")
async def video_stream():
    """Sirve MJPEG stream al frontend."""
    return StreamingResponse(
        generate_mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/video/status")
async def video_status() -> dict[str, Any]:
    """Estado del video streaming."""
    return {
        "has_frame": latest_frame is not None,
        "frame_size": len(latest_frame) if latest_frame else 0,
        "frame_age_ms": int((time.time() - frame_timestamp) * 1000) if frame_timestamp else None,
        "viewers": video_clients,
    }


# ============================================================
# Chat con IA para controlar el rover
# ============================================================

async def send_rover_command(cmd: int) -> dict[str, Any]:
    """Envía un comando al rover via WebSocket."""
    global _ai_command_id
    _ai_command_id += 1
    msg_id = _ai_command_id
    
    async with _lock:
        current_esp = esp_ws
    
    if current_esp is None:
        return {"success": False, "error": "ESP no conectada"}
    
    command = {"id": msg_id, "cmd": cmd, "params": []}
    try:
        await current_esp.send_text(json.dumps(command))
        logger.info("IA envió comando: id=%d cmd=%d", msg_id, cmd)
        return {"success": True, "id": msg_id, "cmd": cmd}
    except Exception as e:
        logger.warning("Error enviando comando desde IA: %s", e)
        return {"success": False, "error": str(e)}


async def execute_tool(tool_name: str) -> dict[str, Any]:
    """Ejecuta una herramienta del rover."""
    # Herramientas de movimiento
    cmd_map = {
        "move_forward": 0,
        "move_backward": 1,
        "move_left": 2,
        "move_right": 3,
    }
    
    if tool_name in cmd_map:
        return await send_rover_command(cmd_map[tool_name])
    
    # Herramientas de video
    if tool_name == "show_video":
        return {"success": True, "video_control": "show"}
    
    if tool_name == "hide_video":
        return {"success": True, "video_control": "hide"}
    
    return {"success": False, "error": f"Herramienta desconocida: {tool_name}"}


from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Endpoint de chat con IA usando streaming (SSE).
    La IA puede usar herramientas para controlar el rover.
    """
    
    async def generate_response():
        # Construir mensajes para OpenAI
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        # Agregar historial (últimos 10 mensajes para no exceder contexto)
        for msg in request.history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        
        # Agregar mensaje actual del usuario
        messages.append({"role": "user", "content": request.message})
        
        try:
            # Llamar a OpenAI con streaming
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=ROVER_TOOLS,
                tool_choice="auto",
                stream=True,
                max_tokens=150,
            )
            
            collected_content = ""
            tool_calls = []
            current_tool_call = None
            
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                
                # Procesar contenido de texto
                if delta.content:
                    collected_content += delta.content
                    yield f"data: {json.dumps({'type': 'content', 'content': delta.content})}\n\n"
                
                # Procesar tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index is not None:
                            # Nuevo tool call o continuación
                            while len(tool_calls) <= tc.index:
                                tool_calls.append({"id": "", "name": "", "arguments": ""})
                            
                            if tc.id:
                                tool_calls[tc.index]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[tc.index]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[tc.index]["arguments"] += tc.function.arguments
            
            # Ejecutar tool calls si los hay
            if tool_calls:
                for tc in tool_calls:
                    if tc["name"]:
                        # Notificar que se está ejecutando
                        yield f"data: {json.dumps({'type': 'tool_start', 'tool': tc['name']})}\n\n"
                        
                        # Ejecutar la herramienta
                        result = await execute_tool(tc["name"])
                        
                        # Notificar resultado
                        yield f"data: {json.dumps({'type': 'tool_result', 'tool': tc['name'], 'result': result})}\n\n"
                        
                        # Si es una herramienta de video, enviar evento especial
                        if "video_control" in result:
                            yield f"data: {json.dumps({'type': 'video_control', 'action': result['video_control']})}\n\n"
                
                # Obtener respuesta final después de ejecutar tools
                tool_messages = []
                for tc in tool_calls:
                    if tc["name"]:
                        result = await execute_tool(tc["name"])
                        tool_messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]}
                            }]
                        })
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result)
                        })
                
                # Segunda llamada para obtener respuesta natural
                messages_with_tools = messages + tool_messages
                final_response = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages_with_tools,
                    stream=True,
                    max_tokens=100,
                )
                
                for chunk in final_response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        yield f"data: {json.dumps({'type': 'content', 'content': delta.content})}\n\n"
            
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            
        except Exception as e:
            logger.error("Error en chat: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile) -> dict[str, str]:
    """
    Transcribe audio usando Whisper.
    Recibe un archivo de audio y retorna el texto transcrito.
    """
    try:
        logger.info("Recibiendo audio para transcripción: %s (%s)", audio.filename, audio.content_type)
        
        # Leer el contenido del archivo
        content = await audio.read()
        
        # Crear un archivo temporal para enviar a OpenAI
        # OpenAI requiere un archivo con nombre y extensión
        import io
        audio_file = io.BytesIO(content)
        audio_file.name = audio.filename or "audio.webm"
        
        # Llamar a la API de Whisper
        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es",  # Optimizado para español
        )
        
        logger.info("Transcripción exitosa: %s", transcription.text[:50] + "..." if len(transcription.text) > 50 else transcription.text)
        
        return {"text": transcription.text}
        
    except Exception as e:
        logger.error("Error en transcripción: %s", e)
        return {"text": "", "error": str(e)}


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "Rover WebSocket Bridge",
        "ws_client": "ws://<host>:8080/ws",
        "ws_esp": "ws://<host>:8080/ws/esp",
        "video_upload": "POST http://<host>:8080/video/upload",
        "video_stream": "GET http://<host>:8080/video/stream",
        "chat": "POST http://<host>:8080/chat",
        "transcribe": "POST http://<host>:8080/transcribe",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
