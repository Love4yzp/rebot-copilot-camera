"""Pose, sequence and template persistence: one JSON file per document.

Same pattern the RoutineStore earned its keep with: a file per document means a
corrupt or half-written file costs one document rather than the whole library,
documents can be copied between machines with ``scp``, and a human can read and
fix one in an editor at 2am on a shoot.

Writes go through a temp file plus ``os.replace``, so a crash mid-write leaves
either the old document or the new one, never a truncated file that parses as
valid JSON with half the blocks missing.

Lists come back in creation order — that is the order the mock serves and the
library tabs render.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from .models import Pose, SeqTemplate, Sequence, SequenceSummary

log = logging.getLogger(__name__)

#: Ids come from uuid4 hex, but ids also land in filesystem paths, so anything
#: that could escape the store directory is rejected outright.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PoseNotFound(KeyError):
    def __init__(self, pose_id: str) -> None:
        super().__init__(pose_id)
        self.pose_id = pose_id


class SequenceNotFound(KeyError):
    def __init__(self, sid: str) -> None:
        super().__init__(sid)
        self.sid = sid


class TemplateNotFound(KeyError):
    def __init__(self, tid: str) -> None:
        super().__init__(tid)
        self.tid = tid


def _write_atomic(path: Path, payload: str) -> None:
    """Persist atomically: temp file in the same directory, then os.replace.

    Same directory because os.replace is only atomic within a filesystem, and
    /tmp is frequently a different one.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


_Model = TypeVar("_Model", bound=BaseModel)
_NotFound = TypeVar("_NotFound", bound=KeyError)


class _JsonStore(Generic[_Model, _NotFound]):
    """The shared file-per-document mechanics. Subclasses fix the model, the
    exception, and what a listing contains."""

    _model: type[_Model]
    _not_found: type[_NotFound]

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, doc_id: str) -> Path:
        if not _SAFE_ID.match(doc_id):
            raise self._not_found(doc_id)
        return self.root / f"{doc_id}.json"

    def exists(self, doc_id: str) -> bool:
        return self._path(doc_id).exists()

    def get(self, doc_id: str) -> _Model:
        path = self._path(doc_id)
        if not path.exists():
            raise self._not_found(doc_id)
        return self._model.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_all(self) -> list[_Model]:
        """Every readable document, creation order first.

        A single unreadable file is logged and skipped rather than raising. The
        alternative is that one bad file makes the whole library inaccessible
        from the UI, which is exactly when the operator most needs to reach the
        other documents.
        """
        docs: list[_Model] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                docs.append(self._model.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                log.exception("skipping unreadable file: %s", path)
                continue
        return sorted(docs, key=lambda d: d.created_at)  # type: ignore[attr-defined]

    def save(self, doc: _Model) -> _Model:
        """Persist atomically. Returns the document for call chaining."""
        _write_atomic(self._path(doc.id), doc.model_dump_json(indent=2))  # type: ignore[attr-defined]
        return doc

    def delete(self, doc_id: str) -> None:
        path = self._path(doc_id)
        if not path.exists():
            raise self._not_found(doc_id)
        path.unlink()


class PoseStore(_JsonStore[Pose, PoseNotFound]):
    _model = Pose
    _not_found = PoseNotFound

    def list(self) -> list[Pose]:
        return self._load_all()


class SequenceStore(_JsonStore[Sequence, SequenceNotFound]):
    _model = Sequence
    _not_found = SequenceNotFound

    def list(self) -> list[SequenceSummary]:
        return [SequenceSummary.of(s) for s in self._load_all()]

    def list_full(self) -> list[Sequence]:
        """The documents themselves — the pose-links scan needs the blocks."""
        return self._load_all()


class TemplateStore(_JsonStore[SeqTemplate, TemplateNotFound]):
    _model = SeqTemplate
    _not_found = TemplateNotFound

    def list(self) -> list[SeqTemplate]:
        return self._load_all()
