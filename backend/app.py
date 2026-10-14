"""Servidor web (FastAPI) que expõe a estação Bluetti AC50P no navegador.

Executa localmente, na máquina que tem o adaptador Bluetooth usado para
falar com a AC50P (o Web Bluetooth do navegador não é usado; o front-end
HTML/JS só conversa com esta API via HTTP/WebSocket).
"""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bluetti_client import BluettiManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bluetti_app")

app = FastAPI(title="Bluetti AC50P Web")
manager = BluettiManager()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

websocket_clients: set[WebSocket] = set()


def status_payload() -> dict:
    payload = manager.status.to_dict()
    payload["poll_interval"] = manager.poll_interval
    return payload


async def broadcast_status(status) -> None:
    payload = status_payload()
    dead = []
    for ws in websocket_clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        websocket_clients.discard(ws)


manager.add_listener(broadcast_status)


class ConnectRequest(BaseModel):
    address: str


class ControlRequest(BaseModel):
    channel: str  # "ac" | "dc"
    on: bool


class ConfigRequest(BaseModel):
    poll_interval: float


@app.get("/api/scan")
async def scan():
    try:
        result = await manager.scan()
    except Exception as exc:
        logger.exception("Falha ao escanear")
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@app.post("/api/connect")
async def connect(req: ConnectRequest):
    try:
        await manager.connect(req.address)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return status_payload()


@app.post("/api/disconnect")
async def disconnect():
    await manager.disconnect()
    return status_payload()


@app.get("/api/status")
async def status():
    return status_payload()


@app.get("/api/config")
async def get_config():
    return {"poll_interval": manager.poll_interval}


@app.post("/api/config")
async def set_config(req: ConfigRequest):
    applied = await manager.set_poll_interval(req.poll_interval)
    return {"poll_interval": applied}


@app.post("/api/control")
async def control(req: ControlRequest):
    if req.channel not in ("ac", "dc"):
        raise HTTPException(status_code=400, detail="channel deve ser 'ac' ou 'dc'")
    try:
        await manager.set_output(req.channel, req.on)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.websocket("/ws")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.add(websocket)
    try:
        await websocket.send_json(manager.status.to_dict())
        while True:
            # Mantém a conexão viva; não esperamos mensagens do cliente.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        websocket_clients.discard(websocket)


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
