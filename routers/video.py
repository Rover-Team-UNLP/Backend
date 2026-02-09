"""
Endpoints de video: upload (ESP), stream y status (frontend).
"""

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video", tags=["video"])


@router.post("/upload")
async def video_upload(request: Request) -> dict[str, str]:
    """
    Recibe stream MJPEG de la ESP (multipart/x-mixed-replace).
    La ESP mantiene la conexión abierta y envía frames continuamente.
    """
    client_host = request.client.host if request.client else "unknown"
    logger.info("ESP video conectada desde %s", client_host)

    boundary = b"--frame"
    buffer = b""
    frame_count = 0

    try:
        async for chunk in request.stream():
            buffer += chunk

            while True:
                start_idx = buffer.find(boundary)
                if start_idx == -1:
                    break

                next_idx = buffer.find(boundary, start_idx + len(boundary))
                if next_idx == -1:
                    break

                frame_data = buffer[start_idx:next_idx]
                buffer = buffer[next_idx:]

                jpeg_start = frame_data.find(b"\r\n\r\n")
                if jpeg_start != -1:
                    jpeg_data = frame_data[jpeg_start + 4 :].rstrip(b"\r\n")
                    if jpeg_data and len(jpeg_data) > 100:
                        state.latest_frame = jpeg_data
                        state.frame_timestamp = time.time()
                        state.frame_event.set()
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
    state.video_clients += 1
    logger.info("Cliente video conectado (total: %d)", state.video_clients)

    last_timestamp = 0

    try:
        while True:
            try:
                await asyncio.wait_for(state.frame_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

            state.frame_event.clear()

            if state.latest_frame is None:
                continue

            if state.frame_timestamp <= last_timestamp:
                continue

            last_timestamp = state.frame_timestamp

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(state.latest_frame)).encode() + b"\r\n"
                b"\r\n" + state.latest_frame + b"\r\n"
            )
    except asyncio.CancelledError:
        pass
    finally:
        state.video_clients -= 1
        logger.info("Cliente video desconectado (total: %d)", state.video_clients)


@router.get("/stream")
async def video_stream():
    """Sirve MJPEG stream al frontend."""
    return StreamingResponse(
        generate_mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/status")
async def video_status() -> dict[str, Any]:
    """Estado del video streaming."""
    return {
        "has_frame": state.latest_frame is not None,
        "frame_size": len(state.latest_frame) if state.latest_frame else 0,
        "frame_age_ms": int((time.time() - state.frame_timestamp) * 1000)
        if state.frame_timestamp
        else None,
        "viewers": state.video_clients,
    }
