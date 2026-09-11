"""Guards for constants mirrored between the TS frontend and the Python backend.

A handful of values must exist in both languages — the approach timing the
preview plans with and the executor drives with, the wait marker's kind, the
joint list the mock models — and until now a comment said "change both
together". Comments do not fail builds. These tests read the TS source
directly; when a mirrored constant drifts, the test names the constant
instead of the drift surfacing half a product later.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.sequences.models import WAIT_KIND

ROOT = Path(__file__).resolve().parents[1]
MODEL_TS = ROOT / "frontend" / "src" / "timeline" / "model.ts"
MARKERS_TS = ROOT / "frontend" / "src" / "timeline" / "markers.ts"
STATE_TS = ROOT / "frontend" / "mock" / "state.ts"


def _ts_const(text: str, name: str) -> str:
    """The raw literal after ``export const NAME =``, quotes stripped."""
    match = re.search(rf"export const {name}\s*=\s*([^;]+);", text)
    assert match, f"{name} not found in its TS mirror"
    return match.group(1).strip().strip("\"'")


def test_wait_kind_matches_models():
    text = MARKERS_TS.read_text(encoding="utf-8")
    assert _ts_const(text, "WAIT_KIND") == WAIT_KIND
