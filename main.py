"""
Servidor FastAPI como puente WebSocket entre el frontend y la ESP32.
- GET /ws: clientes (navegador). Reciben esp_status y ack/error.
- GET /ws/esp: dispositivo (ESP32). Recibe los comandos que envía el front.
"""

import asyncio
import json
import logging
import math
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Rover WebSocket Bridge")

# Estado: una conexión ESP, varias conexiones front
esp_ws: WebSocket | None = None
clients: set[WebSocket] = set()
_lock = asyncio.Lock()


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
    async with _lock:
        clients.add(websocket)
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
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "JSON inválido"})
                )
                continue
            ok, err = validate_rover_message(data)
            if not ok:
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
                await broadcast_to_clients(
                    {"type": "error", "message": "ESP no conectada", "id": msg_id}
                )
                continue
            try:
                await current_esp.send_text(raw)
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


@app.websocket("/ws/esp")
async def websocket_esp(websocket: WebSocket) -> None:
    """Endpoint para la ESP32. Solo una conexión activa (la última)."""
    await websocket.accept()
    global esp_ws
    async with _lock:
        old = esp_ws
        esp_ws = websocket
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass
    logger.info("ESP conectada")
    await broadcast_to_clients({"type": "esp_status", "connected": True})
    try:
        while True:
            await websocket.receive_text()
            # La ESP no envía mensajes por ahora; si en el futuro envía ack/error, reenviar al front
    except WebSocketDisconnect:
        pass
    finally:
        async with _lock:
            if esp_ws is websocket:
                esp_ws = None
        logger.info("ESP desconectada")
        await broadcast_to_clients({"type": "esp_status", "connected": False})


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "Rover WebSocket Bridge",
        "ws_client": "ws://<host>:8080/ws",
        "ws_esp": "ws://<host>:8080/ws/esp",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
