"""
Validación del protocolo de mensajes rover (id, cmd, intensity).
"""

import math
from typing import Any


def validate_rover_message(data: dict[str, Any]) -> tuple[bool, str | None]:
    """Valida que el mensaje cumpla el protocolo de la ESP: id 1-65535, cmd 0-3, intensity número."""
    if not isinstance(data.get("id"), (int, float)) or not (1 <= int(data["id"]) <= 65535):
        return False, "id inválido (debe ser 1-65535)"
    if not isinstance(data.get("cmd"), (int, float)) or not (0 <= int(data["cmd"]) <= 3):
        return False, "cmd inválido (debe ser 0-3)"
    intensity = data.get("intensity")
    if not isinstance(intensity, (int, float)) or (isinstance(intensity, float) and math.isnan(intensity)):
        return False, "intensity debe ser un número"
    return True, None
