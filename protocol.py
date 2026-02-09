"""
Validación del protocolo de mensajes rover (id, cmd, params).
"""

import math
from typing import Any


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
