import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.safety import SafetyLatch


@pytest.fixture
def client() -> TestClient:
    # Fresh latch per test: the app holds process-wide state on purpose, so
    # tests must not inherit a latched stop from each other.
    app.state.latch = SafetyLatch()
    return TestClient(app)


def test_starts_unlatched(client: TestClient):
    body = client.get("/api/estop").json()
    assert body["latched"] is False
    assert body["reason"] is None


def test_engage_then_status_reports_reason_and_source(client: TestClient):
    r = client.post("/api/estop", json={"reason": "cable snagged", "source": "ui"})
    assert r.status_code == 200
    assert r.json()["latched"] is True
    assert r.json()["changed"] is True

    body = client.get("/api/estop").json()
    assert body["reason"] == "cable snagged"
    assert body["source"] == "ui"
    assert body["engaged_at"] is not None


def test_engage_works_with_no_body(client: TestClient):
    """The UI's big red button should not have to compose a payload."""
    r = client.post("/api/estop")
    assert r.status_code == 200
    assert r.json()["latched"] is True
    assert r.json()["reason"]


def test_reengage_is_200_and_keeps_the_first_reason(client: TestClient):
    """An emergency stop that argues with you is a broken emergency stop."""
    client.post("/api/estop", json={"reason": "first", "source": "watchdog"})
    r = client.post("/api/estop", json={"reason": "second", "source": "ui"})

    assert r.status_code == 200
    assert r.json()["changed"] is False
    assert r.json()["reason"] == "first"
    assert r.json()["source"] == "watchdog"


def test_clear_releases_and_is_idempotent(client: TestClient):
    client.post("/api/estop", json={"reason": "stop"})

    r = client.post("/api/estop/clear")
    assert r.status_code == 200
    assert r.json()["latched"] is False
    assert r.json()["changed"] is True

    r = client.post("/api/estop/clear")
    assert r.status_code == 200
    assert r.json()["changed"] is False


def test_clear_is_reachable_while_latched(client: TestClient):
    """Gating the escape hatch on the thing it escapes would wedge the system."""
    client.post("/api/estop", json={"reason": "stop"})
    assert client.post("/api/estop/clear").status_code == 200


def test_empty_reason_is_rejected(client: TestClient):
    r = client.post("/api/estop", json={"reason": "", "source": "api"})
    assert r.status_code == 422
    assert client.get("/api/estop").json()["latched"] is False


def test_health_surfaces_latch_state(client: TestClient):
    assert client.get("/api/health").json()["estop"]["latched"] is False

    client.post("/api/estop", json={"reason": "joint2 stalled", "source": "watchdog"})

    estop = client.get("/api/health").json()["estop"]
    assert estop["latched"] is True
    assert estop["reason"] == "joint2 stalled"
    assert estop["source"] == "watchdog"
