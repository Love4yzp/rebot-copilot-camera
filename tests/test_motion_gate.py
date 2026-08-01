"""The motion gate, and the invariant that keeps it from rotting.

Two different things are tested here. The first is that the gate behaves --
409, with the reason attached. The second matters more over time: that nobody
can add an endpoint which moves the arm and forget to gate it. That one is
enforced by walking the route table rather than by listing endpoints, so the
test fails on the *next* ungated endpoint rather than on a list someone forgot
to update.
"""

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.api.gate import require_arm_available
from backend.app import app as real_app
from backend.safety import SafetyLatch

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Mutating endpoints that legitimately do NOT move the arm, each with the
#: reason it is exempt. Anything not listed here and not gated fails the
#: invariant test below -- which is the point: adding a motion endpoint should
#: force a deliberate choice, not inherit a default.
NON_MOTION_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/estop"): "engaging the stop must never be blocked by the stop",
    ("POST", "/api/estop/clear"): "the escape hatch cannot be gated on what it escapes",
    # Routine editing is pure data. Editing while stopped is often exactly what
    # the operator is doing *because* the arm is stopped.
    ("POST", "/api/routines"): "creates a record, moves nothing",
    ("PATCH", "/api/routines/{rid}"): "renames a record, moves nothing",
    ("DELETE", "/api/routines/{rid}"): "deletes a record, moves nothing",
    ("POST", "/api/routines/{rid}/waypoints"): "edits stored poses, moves nothing",
    ("PATCH", "/api/routines/{rid}/waypoints/{index}"): "edits stored poses, moves nothing",
    ("DELETE", "/api/routines/{rid}/waypoints/{index}"): "edits stored poses, moves nothing",
    ("POST", "/api/routines/{rid}/waypoints/reorder"): "edits stored poses, moves nothing",
    ("POST", "/api/playback/stop"): "stopping must work while stopped",
    (
        "POST",
        "/api/routines/{rid}/waypoints/capture",
    ): "reads the current pose and writes a record; useful precisely while stopped",
    ("POST", "/api/shutter/test"): "fires the shutter, moves no joints",
    (
        "POST",
        "/api/plugins/probe",
    ): "self-tests accessories (ping, not shoot); finding out why one is dark "
    "is a reasonable thing to do while the arm is stopped",
    # Agent lease management. Taking or giving back control moves nothing, and
    # a person must be able to revoke an agent's lease while the arm is stopped.
    ("POST", "/api/agent/acquire"): "takes a lease, moves nothing",
    ("POST", "/api/agent/release"): "gives a lease back; must work while stopped",
    ("POST", "/api/agent/control/stop"): "stopping must work while stopped",
}


def _route_is_gated(route: APIRoute) -> bool:
    """True if ``require_arm_available`` appears anywhere in the dependency tree."""
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        if dep.call is require_arm_available:
            return True
        stack.extend(dep.dependencies)
    return False


def _walk_api_routes(routes) -> list[tuple[str, APIRoute]]:
    """Yield ``(effective_path, route)`` for every APIRoute reachable from ``routes``.

    Routers registered with ``include_router`` are **not** flattened into
    ``app.routes`` on FastAPI 0.141: they appear as a single opaque
    ``_IncludedRouter``, so a naive walk sees none of their endpoints. That is
    a nasty shape for a test like this one, because missing routes makes it
    pass while checking nothing. ``test_walker_sees_every_route_openapi_does``
    below is the guard against that going unnoticed again.
    """
    found: list[tuple[str, APIRoute]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append((route.path, route))
        elif hasattr(route, "effective_candidates"):  # fastapi's _IncludedRouter
            for ctx in route.effective_candidates():
                inner = ctx.original_route
                if isinstance(inner, APIRoute):
                    found.append((ctx.path, inner))
                else:
                    found.extend(_walk_api_routes([inner]))
        elif hasattr(route, "routes"):
            found.extend(_walk_api_routes(route.routes))
    return found


def _mutating_routes() -> list[tuple[str, str, APIRoute]]:
    return [
        (method, path, route)
        for path, route in _walk_api_routes(real_app.routes)
        for method in sorted(set(route.methods or []) & MUTATING_METHODS)
    ]


def test_walker_sees_every_route_openapi_does():
    """Guard against the route walk silently going blind.

    The walk reaches into FastAPI internals, so a version bump could quietly
    stop finding endpoints — and an invariant test that inspects nothing passes
    every time. OpenAPI is the public, version-stable view of the same route
    table, so any disagreement means the walk is broken, not the app.
    """
    from_openapi = {
        (method.upper(), path)
        for path, operations in real_app.openapi()["paths"].items()
        for method in operations
    }
    from_walk = {
        (method, path)
        for path, route in _walk_api_routes(real_app.routes)
        for method in (route.methods or [])
    }
    assert from_walk == from_openapi, (
        "route walk disagrees with OpenAPI — the gate coverage test below is "
        f"inspecting the wrong set. missing={from_openapi - from_walk} "
        f"extra={from_walk - from_openapi}"
    )


def test_every_mutating_route_is_gated_or_explicitly_exempt():
    ungated = [
        f"{method} {path}"
        for method, path, route in _mutating_routes()
        if not _route_is_gated(route) and (method, path) not in NON_MOTION_ROUTES
    ]
    assert not ungated, (
        "These endpoints mutate state but carry neither the motion gate nor an "
        "entry in NON_MOTION_ROUTES. Add `dependencies=[Depends(require_arm_available)]` "
        f"if they move the arm, or declare why they don't: {ungated}"
    )


def test_exemption_list_has_no_stale_entries():
    """A stale exemption is a hole that looks like a decision."""
    live = {(method, path) for method, path, _ in _mutating_routes()}
    stale = [entry for entry in NON_MOTION_ROUTES if entry not in live]
    assert not stale, f"NON_MOTION_ROUTES names routes that no longer exist: {stale}"


# ── gate behaviour, on a synthetic route ─────────────────────────────────────


def _gated_app() -> FastAPI:
    gated = FastAPI()
    gated.state.latch = SafetyLatch()

    @gated.post("/move", dependencies=[Depends(require_arm_available)])
    def move() -> dict:
        return {"moved": True}

    @gated.get("/read")
    def read() -> dict:
        return {"read": True}

    return gated


def test_gate_allows_motion_when_clear():
    client = TestClient(_gated_app())
    assert client.post("/move").status_code == 200


def test_gate_returns_409_with_the_reason_when_latched():
    gated = _gated_app()
    client = TestClient(gated)
    gated.state.latch.engage("joint4 lost CAN feedback", source="watchdog")

    r = client.post("/move")
    assert r.status_code == 409

    detail = r.json()["detail"]
    assert detail["error"] == "estop_latched"
    assert detail["reason"] == "joint4 lost CAN feedback"
    assert detail["source"] == "watchdog"


def test_gate_does_not_block_reads():
    """An operator diagnosing a stop needs the read endpoints to keep working."""
    gated = _gated_app()
    client = TestClient(gated)
    gated.state.latch.engage("stop", source="ui")

    assert client.get("/read").status_code == 200


def test_gate_reopens_after_clear():
    gated = _gated_app()
    client = TestClient(gated)
    gated.state.latch.engage("stop", source="ui")
    assert client.post("/move").status_code == 409

    gated.state.latch.clear()
    assert client.post("/move").status_code == 200
