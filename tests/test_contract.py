"""Golden contract tests: one set of case files, two implementations.

The case files in ``contract/cases/`` are the handoff surface between the two
people who can drift apart: the one editing the FastAPI backend and the one
editing the dev mock (``frontend/mock/``). Every case runs twice — once here
against the TestClient, once in Node against the mock's ``handleApi`` (and
against the TS ``normalize`` for the pure-logic cases) — and the two canonical
transcripts are diffed entry by entry. A shape or semantic drift on either
side fails this suite, in CI, before it reaches a user.

The canonicalization rules below are the contract's portability rules; keep
them in sync with their TS mirror in ``frontend/contract/mock-driver.ts``:

- a null-valued key is the same as an absent key (FastAPI serializes optional
  fields as null; the mock omits them)
- any 12-hex run inside a string is an id: replaced by ``<id:N>`` in
  first-appearance order, so "the same id in two places" is still checked —
  including ids embedded in messages (``no pose 'abc123…'``)
- a number ≥ 1e9 is a unix timestamp: replaced by ``<ts>``
- ``VOLATILE_KEYS`` name values neither side can control (measured rate,
  firmware banner): replaced by ``<volatile>``

Dict keys are traversed in sorted order so the id numbering does not depend on
either side's insertion order.

Deliberately out of scope (the case files never ask for them):

- websocket frames, ``/api/health``, ``/api/logs`` — mock stand-ins whose
  content differs by design
- FastAPI's 422 validation envelope — the mock has no validator to mirror it
  against, so invalid-shape requests are not contract cases
- mid-run playback progress: the mock applies control-loop effects inside the
  request handler while the real loop applies them on the next tick, so a run
  is only ever observed at rest points (started, aborted, stopped)

A case may set ``"seed": true`` to run against the first-boot demo library:
both sides plant the same demo (``seed_demo_if_empty`` on the backend,
``createState({seed: true})`` on the mock) so the seeded poses, sequence and
template are compared field by field like everything else.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from backend.actions import ActionRegistry, InlineRunner, ShutterProvider
from backend.app import app
from backend.arm import SimArm
from backend.core import Broadcaster, Controller
from backend.safety import SafetyLatch
from backend.sequences import Block, PoseStore, SequenceStore, TemplateStore, normalize
from backend.sequences.seed_demo import seed_demo_if_empty
from backend.shutter import SimShutter
from backend.tuning import TuningStore

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "contract" / "cases"
RUNNER = ROOT / "frontend" / "contract" / "run-mock.mjs"

#: The full hardware joint set, so URDF limit/collision validation sees the
#: same names an operator's poses carry.
JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "gripper")

VOLATILE_KEYS = frozenset({"rate_hz", "firmware_version", "uptime_s"})
ID_RE = re.compile(r"[0-9a-f]{12}")
EPOCH = 1e9


class FakeClock:
    """Starts at a realistic epoch so engaged_at/created_at scrub uniformly."""

    def __init__(self, now: float = 1_752_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def canon(value, ids: dict[str, int]):
    if isinstance(value, list):
        return [canon(v, ids) for v in value]
    if isinstance(value, dict):
        out = {}
        for key in sorted(value):
            field = value[key]
            if field is None:
                continue  # null ≈ absent
            out[key] = "<volatile>" if key in VOLATILE_KEYS else canon(field, ids)
        return out
    if isinstance(value, str):

        def repl(match: re.Match) -> str:
            raw = match.group(0)
            if raw not in ids:
                ids[raw] = len(ids) + 1
            return f"<id:{ids[raw]}>"

        return ID_RE.sub(repl, value)
    # bool before int/float: True == 1 in Python, and a bool is not a timestamp.
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value >= EPOCH:
        return "<ts>"
    return value


def substitute(value, variables: dict[str, object]):
    if isinstance(value, str):
        def repl(match: re.Match) -> str:
            name = match.group(1)
            if name not in variables:
                raise KeyError(f"unset variable {name!r}")
            return str(variables[name])

        return re.sub(r"\$\{(\w+)\}", repl, value)
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


@pytest.fixture
def rig(tmp_path: Path):
    """The same wiring as the API test rigs, with the full seven-joint set."""
    clock = FakeClock()
    arm = SimArm(JOINTS, clock=clock, tau=0.05)
    arm.connect()

    app.state.latch = SafetyLatch(clock=clock)
    app.state.pose_store = PoseStore(tmp_path / "poses")
    app.state.sequence_store = SequenceStore(tmp_path / "sequences")
    app.state.template_store = TemplateStore(tmp_path / "templates")
    app.state.broadcaster = Broadcaster()
    app.state.tuning_store = TuningStore(tmp_path / "tuning.yaml")
    shutter = SimShutter()
    runner = InlineRunner()
    app.state.plugins = ActionRegistry(runner)
    app.state.plugins.register(ShutterProvider(shutter))
    app.state.controller = Controller(
        arm=arm,
        shutter=shutter,
        latch=app.state.latch,
        broadcaster=app.state.broadcaster,
        clock=clock,
        actions=runner,
        tuning=app.state.tuning_store.load(),
    )
    return TestClient(app), app.state.controller


def run_case_on_backend(rig, case: dict) -> list[dict]:
    client, controller = rig
    ids: dict[str, int] = {}

    if case["kind"] == "normalize":
        blocks = TypeAdapter(list[Block]).validate_python(case["blocks"])
        out = normalize(blocks)
        return [{"blocks": canon([b.model_dump(mode="json") for b in out], ids)}]

    if case.get("seed"):
        # The mock side passes seed:true to createState; this is the backend's
        # same half — the first-boot demo into the empty stores.
        seed_demo_if_empty(
            app.state.pose_store,
            app.state.sequence_store,
            app.state.template_store,
        )

    variables: dict[str, object] = {}
    entries = []
    for step in case.get("steps", []):
        path = substitute(step["path"], variables)
        kwargs = {}
        if "body" in step:
            kwargs["json"] = substitute(step["body"], variables)
        response = client.request(step["method"], path, **kwargs)
        # The control loop is always running on the real machine: latch
        # effects (freezing, aborting the run) land on the next tick, not in
        # the request handler. The mock applies them synchronously, so tick
        # once here before the transcript looks again.
        controller.tick()

        entry: dict = {"status": response.status_code}
        if response.content:
            body = canon(response.json(), ids)
            for key in step.get("ignore", []):
                body.pop(key, None)
            entry["body"] = body
        entries.append(entry)

        if response.content:
            raw = response.json()
            for var, field in step.get("save", {}).items():
                variables[var] = raw[field]
    return entries


@pytest.fixture(scope="session")
def mock_transcript() -> dict[str, list[dict]]:
    """The mock's half of every case, computed once in Node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH — the mock half of the contract needs it")
    if not (ROOT / "frontend" / "node_modules" / "esbuild").exists():
        pytest.skip("frontend/node_modules missing — run `npm install` in frontend/")
    proc = subprocess.run(  # noqa: PLW1510 — returncode is asserted below
        [node, str(RUNNER), str(CASES_DIR)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"mock runner failed:\n{proc.stderr}"
    return {t["name"]: t["entries"] for t in json.loads(proc.stdout)}


def case_files() -> list[Path]:
    return sorted(CASES_DIR.glob("*.json"))


@pytest.mark.parametrize("case_file", case_files(), ids=lambda p: p.stem)
def test_golden_contract(rig, mock_transcript, case_file: Path):
    case = json.loads(case_file.read_text())
    backend_entries = run_case_on_backend(rig, case)
    mock_entries = mock_transcript[case["name"]]
    if backend_entries != mock_entries:
        backend_json = json.dumps(backend_entries, ensure_ascii=False, indent=2, sort_keys=True)
        mock_json = json.dumps(mock_entries, ensure_ascii=False, indent=2, sort_keys=True)
        pytest.fail(
            f"contract drift in case {case['name']!r} "
            f"(backend run on TestClient, mock run on frontend/mock):\n"
            f"--- backend ---\n{backend_json}\n--- mock ---\n{mock_json}"
        )
