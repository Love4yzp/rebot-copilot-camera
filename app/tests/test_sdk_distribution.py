"""A fresh checkout must reproduce the SDK used by this application."""

import re
import subprocess
from pathlib import Path
import pytest

APP = Path(__file__).resolve().parents[1]
SDK = APP / "vendor/reBotArm_control_py"
BASELINE = "d54040596faa94bdc4f8ad93f3f06b33dfe3a1bf"


def test_sdk_patch_reproduces_the_working_sdk(tmp_path):
    patch = APP / "vendor-patches/rebot-sdk.patch"
    files = re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(), re.MULTILINE)
    assert files
    for name in files:
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        original = subprocess.run(
            ["git", "-C", str(SDK), "show", f"{BASELINE}:{name}"], capture_output=True, check=False
        )
        if original.returncode == 0:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.stdout)
    subprocess.run(
        ["git", "apply", "--ignore-space-change", str(patch)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    for name in files:
        assert (tmp_path / name).read_text() == (SDK / name).read_text(), name


def test_unsupported_action_and_old_sequence_documents_are_refused():
    from pydantic import ValidationError
    from backend.sequences import EventMarker, Sequence

    with pytest.raises(ValidationError):
        EventMarker(kind="shutter", at=0)
    with pytest.raises(ValidationError):
        Sequence(schema_version=2, name="old plugin sequence")


def test_removed_routes_do_not_reappear():
    from backend.app import app

    assert not any(
        path.startswith(("/api/agent", "/api/shutter", "/api/plugins"))
        for path in app.openapi()["paths"]
    )
