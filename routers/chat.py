"""
Chat con IA (OpenAI) y transcripción (Whisper). Endpoints: POST /chat, POST /transcribe.
"""

import io
import json
import logging
from typing import Any

from fastapi import APIRouter, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import state
from config import get_openai_client

logger = logging.getLogger(__name__)

router = APIRouter()

# Contador de IDs para comandos enviados por la IA
_ai_command_id = 10000

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


async def send_rover_command(cmd: int) -> dict[str, Any]:
    """Envía un comando al rover via WebSocket."""
    global _ai_command_id
    _ai_command_id += 1
    msg_id = _ai_command_id

    async with state._lock:
        current_esp = state.esp_ws

    if current_esp is None:
        return {"success": False, "error": "ESP no conectada"}

    command = {"id": msg_id, "cmd": cmd, "intensity": 1}
    try:
        await current_esp.send_text(json.dumps(command))
        logger.info("IA envió comando: id=%d cmd=%d", msg_id, cmd)
        return {"success": True, "id": msg_id, "cmd": cmd}
    except Exception as e:
        logger.warning("Error enviando comando desde IA: %s", e)
        return {"success": False, "error": str(e)}


async def execute_tool(tool_name: str) -> dict[str, Any]:
    """Ejecuta una herramienta del rover."""
    cmd_map = {
        "move_forward": 0,
        "move_backward": 1,
        "move_left": 2,
        "move_right": 3,
    }

    if tool_name in cmd_map:
        return await send_rover_command(cmd_map[tool_name])

    if tool_name == "show_video":
        return {"success": True, "video_control": "show"}

    if tool_name == "hide_video":
        return {"success": True, "video_control": "hide"}

    return {"success": False, "error": f"Herramienta desconocida: {tool_name}"}


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """
    Endpoint de chat con IA usando streaming (SSE).
    La IA puede usar herramientas para controlar el rover.
    """
    openai_client = get_openai_client()

    async def generate_response():
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        for msg in request.history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": request.message})

        try:
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
            tool_results: list[dict[str, Any]] = []  # resultados ya ejecutados (una sola vez)

            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                if delta.content:
                    collected_content += delta.content
                    yield f"data: {json.dumps({'type': 'content', 'content': delta.content})}\n\n"

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index is not None:
                            while len(tool_calls) <= tc.index:
                                tool_calls.append({"id": "", "name": "", "arguments": ""})

                            if tc.id:
                                tool_calls[tc.index]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls[tc.index]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls[tc.index]["arguments"] += tc.function.arguments

            # Ejecutar cada tool call UNA sola vez y guardar el resultado
            if tool_calls:
                for tc in tool_calls:
                    if tc["name"]:
                        yield f"data: {json.dumps({'type': 'tool_start', 'tool': tc['name']})}\n\n"
                        result = await execute_tool(tc["name"])
                        tool_results.append(result)
                        yield f"data: {json.dumps({'type': 'tool_result', 'tool': tc['name'], 'result': result})}\n\n"
                        if "video_control" in result:
                            yield f"data: {json.dumps({'type': 'video_control', 'action': result['video_control']})}\n\n"

                # Construir tool_messages con los resultados ya obtenidos (no volver a ejecutar)
                tool_messages = []
                result_idx = 0
                for tc in tool_calls:
                    if tc["name"]:
                        result = tool_results[result_idx]
                        result_idx += 1
                        tool_messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }],
                        })
                        tool_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result),
                        })

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
        },
    )


@router.post("/transcribe")
async def transcribe_audio(audio: UploadFile) -> dict[str, str]:
    """
    Transcribe audio usando Whisper.
    Recibe un archivo de audio y retorna el texto transcrito.
    """
    openai_client = get_openai_client()
    try:
        logger.info("Recibiendo audio para transcripción: %s (%s)", audio.filename, audio.content_type)

        content = await audio.read()
        audio_file = io.BytesIO(content)
        audio_file.name = audio.filename or "audio.webm"

        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es",
        )

        logger.info(
            "Transcripción exitosa: %s",
            transcription.text[:50] + "..." if len(transcription.text) > 50 else transcription.text,
        )

        return {"text": transcription.text}

    except Exception as e:
        logger.error("Error en transcripción: %s", e)
        return {"text": "", "error": str(e)}
