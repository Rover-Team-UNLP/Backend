"""
Estado compartido: puente WebSocket (ESP, clientes) y video streaming.
"""

import asyncio
from fastapi import WebSocket

# Estado del puente: una conexión ESP, varias conexiones front
esp_ws: WebSocket | None = None
esp_host: str | None = None
clients: set[WebSocket] = set()
_lock = asyncio.Lock()

# Estado de video: último frame recibido de la ESP
latest_frame: bytes | None = None
frame_timestamp: float = 0
frame_event = asyncio.Event()
video_clients: int = 0
