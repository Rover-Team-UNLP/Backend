# Backend – Rover WebSocket Bridge

Servidor que hace de **puente WebSocket** entre el frontend (navegador) y la ESP32: recibe los comandos del navegador y se los reenvía a la ESP.

## Qué hace

- Acepta conexiones del **navegador** en `/ws` y de la **ESP32** en `/ws/esp`.
- Recibe del navegador mensajes con comandos (adelante, atrás, izquierda, derecha).
- Reenvía esos mismos mensajes a la ESP (si está conectada).
- Notifica al navegador: si la ESP está conectada o no, y si cada comando se envió bien (ack) o falló (error).

Para probar sin ESP física se puede usar el **simulador ESP** (ver más abajo).

## Cómo usarlo

**Requisitos:** Python 3.

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

El servidor queda escuchando en el **puerto 8080**.

## URLs WebSocket

- **Frontend (navegador):** `ws://<host>:8080/ws`
- **ESP32:** `ws://<host>:8080/ws/esp`

En el firmware de la ESP hay que configurar `WEBSOCKET_URI` en `web_socket.c` con la IP del servidor, por ejemplo `ws://192.168.1.100:8080/ws/esp`.

## Simulador ESP (probar sin ESP física)

Para probar que el front y el backend funcionan bien sin tener una ESP32 conectada:

1. Arrancar el servidor (`uvicorn main:app --host 0.0.0.0 --port 8080`).
2. Arrancar el front (desde la carpeta `front/`: `pnpm dev`).
3. En otra terminal, desde `backend/`:

   ```bash
   python esp_simulator.py
   ```

El simulador se conecta a `/ws/esp` como si fuera la ESP. En el front debería aparecer "ESP conectada" y, al usar las flechas o W/A/S/D, en la terminal del simulador verás los comandos que recibiría la ESP (por ejemplo `[#1] id=1 cmd=0 (MOVE_FORWARD) intensity=100`).

Opciones del simulador:

- Otra URL: `python esp_simulator.py --uri ws://192.168.1.100:8080/ws/esp`
- No reconectar al desconectarse: `python esp_simulator.py --no-reconnect`

## Protocolo (resumen)

- El front envía JSON: `{ "id": number, "cmd": 0|1|2|3, "intensity": number }` (cmd: 0=adelante, 1=atrás, 2=izq, 3=der; intensity: valor numérico de intensidad).
- El servidor reenvía ese JSON a la ESP y responde al front con `{ "type": "ack", "id": ... }` o `{ "type": "error", "message": "...", "id": ... }`.
- El servidor avisa al front cuando la ESP se conecta o desconecta: `{ "type": "esp_status", "connected": true|false }`.
