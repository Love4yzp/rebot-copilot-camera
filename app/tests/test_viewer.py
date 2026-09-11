"""Viewer isolation and actual loopback MeshCat handshake; no arm/server start."""

from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from reBotArm_control_py.viewer import ViewerSession
from backend import assets
from backend.api import viewer


def test_no_viewer_is_a_graceful_disconnect():
    app = FastAPI()
    app.include_router(viewer.router)
    client = TestClient(app)
    assert client.get("/viewer/").status_code == 200
    assert client.get("/viewer/main.min.js").status_code == 200
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/viewer/ws"):
            pytest.fail("missing viewer accepted a socket")


def test_meshcat_worker_streams_scene_through_read_only_proxy():
    session = ViewerSession(urdf_path=str(assets.urdf_path()), joint_names=assets.arm_joint_names())
    app = FastAPI()
    app.include_router(viewer.router)
    app.state.runtime = SimpleNamespace(viewer=session)
    # A controller/watchdog is deliberately not available to this endpoint.
    try:
        session.start()
        assert session.url.startswith("ws://127.0.0.1:")
        session.publish({"joint1": 0.1})
        client = TestClient(app)
        with client.websocket_connect("/viewer/ws") as socket:
            socket.send_text('{"command":"move","joint1":2}')
            assert socket.receive_bytes(), "MeshCat did not provide scene data"
        assert session.error is None
    finally:
        session.close()


def test_viewer_cannot_import_the_control_boundary():
    import ast
    from pathlib import Path

    source = Path(viewer.__file__).read_text()
    forbidden = {"intend", "perturb", "goto", "play", "note_client"}
    assert (
        not {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
        & forbidden
    )
