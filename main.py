"""
Servidor FastAPI como puente WebSocket entre el frontend y la ESP32.
- GET /ws: clientes (navegador). Reciben esp_status y ack/error.
- GET /ws/esp: dispositivo (ESP32). Recibe los comandos que envía el front.
- POST /video/upload: recibe frames MJPEG de la ESP.
- GET /video/stream: sirve MJPEG stream al frontend.
- POST /chat: chat con IA para controlar el rover.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config  # noqa: F401 - carga .env.local
from routers import chat, video, websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="Rover WebSocket Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(websocket.router)
app.include_router(video.router)
app.include_router(chat.router)


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
