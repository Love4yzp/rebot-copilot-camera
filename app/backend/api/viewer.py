"""Same-origin, read-only MeshCat viewport. It never counts as a control client."""

from pathlib import Path
import asyncio
from anyio import CancelScope
from fastapi import APIRouter, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from reBotArm_control_py.viewer import viewer_assets

router = APIRouter(tags=["viewer"])
PAGE = Path(__file__).parent.parent / "viewer.html"


@router.get("/viewer/", response_class=HTMLResponse)
def page():
    return PAGE.read_text(encoding="utf-8")


@router.get("/viewer/main.min.js")
def script():
    return FileResponse(viewer_assets() / "main.min.js", media_type="application/javascript")


@router.websocket("/viewer/ws")
async def scene(websocket: WebSocket):
    from websockets.asyncio.client import connect

    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None or runtime.viewer.url is None or runtime.viewer.error:
        await websocket.close(code=1013)
        return
    await websocket.accept()

    async def discard_input():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    async def forward():
        async with connect(runtime.viewer.url, max_size=32 * 1024 * 1024, proxy=None) as upstream:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)

    tasks = [asyncio.create_task(forward()), asyncio.create_task(discard_input())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (Exception, asyncio.CancelledError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await websocket.close()
            except RuntimeError:
                pass
