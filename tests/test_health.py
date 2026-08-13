from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)


def test_health_reports_ok_and_version():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_health_reports_the_rs_arm_not_the_dm_arm():
    """The upstream library defaults to the B601-DM arm's URDF.

    That default loads without error, so a mix-up would otherwise only show up
    as subtly wrong torques. Assert on identity, not just on presence.
    """
    arm = client.get("/api/health").json()["arm"]
    assert "00-arm-rs_asm-v3" in arm["urdf"]
    assert arm["end_effector_frame"] == "gripper_end"
    assert arm["joints"] == [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5",
        "joint6",
    ]
