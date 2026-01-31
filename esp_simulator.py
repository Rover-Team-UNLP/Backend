#!/usr/bin/env python3
"""
Simulador ESP32 WebSocket para Mac.
Replica el flujo de web_socket.c + json_parser: conexión a /ws/esp, validación
idéntica a parse_json, buffer de 10 slots (push/take_cmd), salida por consola.
No envía nada por WS (la ESP no envía acks/errores).
"""

import argparse
import asyncio
import json
import logging
import math
import sys
from typing import Any

import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CMD_BUFFER_LEN = 10
CMD_PARAMS_LEN = 10
CMD_NAMES = {
    0: "MOVE_FORWARD",
    1: "MOVE_BACKWARDS",
    2: "MOVE_LEFT",
    3: "MOVE_RIGHT",
}
RECONNECT_DELAY_S = 10
DEFAULT_URI = "ws://localhost:8080/ws/esp"


def parse_and_validate(raw_json: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Misma lógica que parse_json en la ESP: cmd (número), id (número), params (array, max 10 números).
    Devuelve (ok, cmd_dict, error_msg).
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return False, None, f"JSON inválido: {e}"

    if not isinstance(data, dict):
        return False, None, "El mensaje debe ser un objeto JSON"

    cmd = data.get("cmd")
    if cmd is None:
        return False, None, "Falta campo 'cmd'"
    if not isinstance(cmd, (int, float)):
        return False, None, "'cmd' debe ser un número"
    cmd_int = int(cmd)
    if cmd_int != cmd or math.isnan(cmd):
        return False, None, "'cmd' debe ser entero"

    id_val = data.get("id")
    if id_val is None:
        return False, None, "Falta campo 'id'"
    if not isinstance(id_val, (int, float)):
        return False, None, "'id' debe ser un número"
    id_int = int(id_val)
    if id_int != id_val or id_int < 1 or id_int > 65535:
        return False, None, "'id' debe ser entero entre 1 y 65535"

    params = data.get("params")
    if params is None:
        return False, None, "Falta campo 'params'"
    if not isinstance(params, list):
        return False, None, "'params' debe ser un array"
    if len(params) > CMD_PARAMS_LEN:
        return False, None, f"'params' tiene más de {CMD_PARAMS_LEN} elementos"

    params_ok = []
    for i, p in enumerate(params):
        if not isinstance(p, (int, float)) or (isinstance(p, float) and math.isnan(p)):
            return False, None, f"params[{i}] debe ser un número"
        params_ok.append(float(p) if isinstance(p, (int, float)) else p)

    if cmd_int not in CMD_NAMES:
        logger.warning("cmd=%d no está en rover_cmd_type_t (0-3); la ESP lo aceptaría igual.", cmd_int)

    return True, {
        "id": id_int,
        "cmd": cmd_int,
        "params": params_ok,
        "total_params": len(params_ok),
    }, None


class CmdBuffer:
    """Buffer de 10 slots como en json_parser.c (push / take_cmd)."""

    __slots__ = ("buffer", "newest_id", "count")

    def __init__(self) -> None:
        self.buffer: list[dict[str, Any] | None] = [None] * CMD_BUFFER_LEN
        self.newest_id = 0
        self.count = 0

    def push(self, cmd: dict[str, Any]) -> None:
        idx = (cmd["id"] - 1) % CMD_BUFFER_LEN
        self.buffer[idx] = cmd.copy()
        self.newest_id = cmd["id"]
        if self.count < CMD_BUFFER_LEN:
            self.count += 1

    def take_cmd(self, cmd_id: int) -> tuple[bool, dict[str, Any] | None, str | None]:
        if cmd_id == 0:
            return False, None, "STATUS_NOT_VALID_ID"
        idx = (cmd_id - 1) % CMD_BUFFER_LEN
        slot = self.buffer[idx]
        if slot is None or slot.get("id") != cmd_id:
            return False, None, "STATUS_ID_NOT_FOUND"
        return True, slot.copy(), None


async def run_simulator(uri: str, reconnect: bool) -> None:
    buffer = CmdBuffer()
    total_commands = 0

    while True:
        try:
            logger.info("Conectando a %s ...", uri)
            async with websockets.connect(uri) as ws:
                logger.info("Conectado como ESP. Esperando comandos del front...")
                async for raw in ws:
                    if not raw or not isinstance(raw, str):
                        continue
                    ok, cmd_dict, err = parse_and_validate(raw)
                    if not ok:
                        logger.warning("Parse error: %s", err)
                        continue
                    assert cmd_dict is not None
                    buffer.push(cmd_dict)
                    ok_take, cmd_out, err_take = buffer.take_cmd(cmd_dict["id"])
                    if not ok_take:
                        logger.warning("take_cmd error: %s", err_take)
                        continue
                    assert cmd_out is not None
                    total_commands += 1
                    name = CMD_NAMES.get(cmd_out["cmd"], f"cmd_{cmd_out['cmd']}")
                    logger.info(
                        "[#%d] id=%s cmd=%s (%s) params=%s",
                        total_commands,
                        cmd_out["id"],
                        cmd_out["cmd"],
                        name,
                        cmd_out["params"],
                    )
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Conexión cerrada: %s", e)
        except OSError as e:
            logger.warning("Error de conexión: %s", e)
        if not reconnect:
            break
        logger.info("Reconectando en %s s...", RECONNECT_DELAY_S)
        await asyncio.sleep(RECONNECT_DELAY_S)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador ESP WebSocket para probar front + backend en Mac.")
    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
        help=f"URI del endpoint /ws/esp (default: {DEFAULT_URI})",
    )
    parser.add_argument(
        "--no-reconnect",
        action="store_true",
        help="No reconectar tras desconexión (por defecto reconecta a los 10 s)",
    )
    args = parser.parse_args()
    asyncio.run(run_simulator(args.uri, reconnect=not args.no_reconnect))


if __name__ == "__main__":
    main()
    sys.exit(0)
