"""
Endpoints WebSocket: /ws (clientes) y /ws/esp (dispositivo ESP32).
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import state
from ..protocol import validate_rover_message

logger = logging.getLogger(__name__)

router = APIRouter()


async def broadcast_to_clients(message: dict[str, Any]) -> None:
    """Envía un mensaje a todos los clientes conectados."""
    text = json.dumps(message)
    dead = set()
    for ws in state.clients:
        try:
            await ws.send_text(text)
        except Exception as e:
            logger.warning("Error enviando a cliente: %s", e)
            dead.add(ws)
    for ws in dead:
        state.clients.discard(ws)


@router.websocket("/ws")
async def websocket_client(websocket: WebSocket) -> None:
    """Endpoint para el frontend (navegador)."""
    await websocket.accept()
    client_host = websocket.client.host if websocket.client else "unknown"
    async with state._lock:
        state.clients.add(websocket)
        client_count = len(state.clients)
    logger.info("Cliente conectado desde %s (total: %d)", client_host, client_count)
    await websocket.send_text(
        json.dumps({"type": "esp_status", "connected": state.esp_ws is not None})
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
            async with state._lock:
                current_esp = state.esp_ws
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
        async with state._lock:
            state.clients.discard(websocket)
            client_count = len(state.clients)
        logger.info("Cliente desconectado desde %s (total: %d)", client_host, client_count)


@router.websocket("/ws/esp")
async def websocket_esp(websocket: WebSocket) -> None:
    """Endpoint para la ESP32. Solo una conexión activa (la última)."""
    await websocket.accept()
    new_host = websocket.client.host if websocket.client else "unknown"
    async with state._lock:
        old = state.esp_ws
        state.esp_ws = websocket
        state.esp_host = new_host
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
            logger.debug("Mensaje de ESP (ignorado): %s", msg[:100] if msg else "")
    except WebSocketDisconnect:
        pass
    finally:
        disconnected_host = state.esp_host
        async with state._lock:
            if state.esp_ws is websocket:
                state.esp_ws = None
                state.esp_host = None
        logger.info("ESP desconectada (era %s)", disconnected_host)
        await broadcast_to_clients({"type": "esp_status", "connected": False})
