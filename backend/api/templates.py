"""Sequence templates: structural recipes with pose slots.

A template snapshots a sequence's *structure* — station count, hold durations,
marker recipes, transition parameters — with each hold's pose replaced by a
``slot:N`` placeholder. No joint angles: a template's value is the structure,
and angles taught in one studio are wrong in another.

Instantiating is copy-and-detach: the new sequence and the template owe each
other nothing from there on.
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from ..sequences import (
    Block,
    HoldBlock,
    PoseNotFound,
    PoseStore,
    SeqTemplate,
    Sequence,
    SequenceNotFound,
    SequenceStore,
    TemplateNotFound,
    TemplateStore,
    normalize,
)

router = APIRouter(prefix="/api/templates", tags=["templates"])

_SLOT = re.compile(r"^slot:(\d+)$")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _store(request: Request) -> TemplateStore:
    return request.app.state.template_store


def _sequences(request: Request) -> SequenceStore:
    return request.app.state.sequence_store


def _poses(request: Request) -> PoseStore:
    return request.app.state.pose_store


def _load(request: Request, tid: str) -> SeqTemplate:
    try:
        return _store(request).get(tid)
    except TemplateNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no template {tid!r}") from None


def _fresh_copy(block: Block) -> Block:
    """A detached copy: new block id, new marker ids. Ids are identity — a copy
    that kept them would collide with its source in the fired-marker sets."""
    copy = block.model_copy(deep=True)
    copy.id = _new_id()
    copy.markers = [m.model_copy(update={"id": _new_id()}) for m in block.markers]
    return copy


class CreateTemplate(BaseModel):
    sequence_id: str = ""
    name: str | None = None


class InstantiateTemplate(BaseModel):
    name: str = ""
    pose_ids: list[str] = []


@router.get("", response_model=list[SeqTemplate])
def list_templates(request: Request) -> list[SeqTemplate]:
    return _store(request).list()


@router.post("", response_model=SeqTemplate, status_code=status.HTTP_201_CREATED)
def create_template(body: CreateTemplate, request: Request) -> SeqTemplate:
    """Snapshot a sequence as a structural recipe (pose slots, no joints)."""
    try:
        sequence = _sequences(request).get(body.sequence_id)
    except SequenceNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"no sequence {body.sequence_id!r}"
        ) from None

    holds = [b for b in sequence.blocks if isinstance(b, HoldBlock)]
    if not holds:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "a sequence with no stations cannot be a template"
        )

    # Each hold block is its own slot — two stations at the same pose are
    # still two stations.
    slots = {hold.id: index for index, hold in enumerate(holds)}
    recipe: list[Block] = []
    for block in sequence.blocks:
        copy = _fresh_copy(block)
        if isinstance(copy, HoldBlock):
            copy.pose_id = f"slot:{slots.get(block.id, 0) + 1}"
        recipe.append(copy)

    name = (body.name or "").strip() or sequence.name
    return _store(request).save(
        SeqTemplate(name=name, station_count=len(holds), recipe=normalize(recipe))
    )


@router.delete("/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(tid: str, request: Request) -> None:
    try:
        _store(request).delete(tid)
    except TemplateNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no template {tid!r}") from None


@router.post("/{tid}/instantiate", response_model=Sequence, status_code=status.HTTP_201_CREATED)
def instantiate_template(tid: str, body: InstantiateTemplate, request: Request) -> Sequence:
    """Copy the recipe with each slot bound to a library pose."""
    template = _load(request, tid)

    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name must be at least 1 character")
    if len(body.pose_ids) != template.station_count:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"pose_ids must list {template.station_count} poses, one per slot",
        )
    poses = _poses(request)
    for pose_id in body.pose_ids:
        try:
            poses.get(pose_id)
        except PoseNotFound:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unknown pose {pose_id!r}"
            ) from None

    # Copy and detach: the new sequence and the template owe each other
    # nothing from here on. A hold whose pose_id is not a slot placeholder
    # (a hand-written recipe) is left as it is.
    blocks: list[Block] = []
    for block in template.recipe:
        copy = _fresh_copy(block)
        if isinstance(copy, HoldBlock):
            slot = _SLOT.match(copy.pose_id)
            if slot is not None:
                copy.pose_id = body.pose_ids[int(slot.group(1)) - 1]
        blocks.append(copy)

    return _sequences(request).save(Sequence(name=name, blocks=normalize(blocks)))
