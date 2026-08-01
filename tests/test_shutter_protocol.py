"""The shutter line protocol and its serial client.

Everything runs against an in-memory pipe. Partial lines, coalesced lines, late
replies and a port that vanishes are all reachable without a board, which is
the point of injecting the transport.
"""

import pytest

from backend.shutter import Esp32Shutter, ShutterError, ShutterNotConnected, ShutterTimeout
from backend.shutter.protocol import LineReader, Ready, Response, decode, encode


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now += dt


class FakeTransport:
    """An in-memory serial port.

    ``responder`` is called with each request line and returns the raw bytes the
    board would send back — which lets a test emit half a line, three lines, or
    nothing at all.
    """

    def __init__(self, responder=None) -> None:
        self.is_open = True
        self.written: list[bytes] = []
        self.inbox = bytearray()
        self._responder = responder
        self.fail_on_write = False
        self.fail_on_read = False

    def write(self, data: bytes) -> None:
        if self.fail_on_write:
            raise OSError("device disappeared")
        self.written.append(data)
        if self._responder is not None:
            reply = self._responder(data.decode().strip())
            if reply:
                self.inbox.extend(reply)

    def read(self, size: int = 1024) -> bytes:
        if self.fail_on_read:
            raise OSError("device disappeared")
        chunk, self.inbox = bytes(self.inbox[:size]), bytearray(self.inbox[size:])
        return chunk

    def close(self) -> None:
        self.is_open = False

    def push(self, data: bytes) -> None:
        self.inbox.extend(data)


def make(responder=None, **kwargs):
    clock = FakeClock()
    transport = FakeTransport(responder)
    shutter = Esp32Shutter(
        open_transport=lambda: transport,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return shutter, transport, clock


def always_ok(line: str) -> bytes:
    request_id = line.split()[0].lstrip("#")
    return f"#{request_id} OK\n".encode()


# ── encoding and decoding ────────────────────────────────────────────────────


def test_encode_shapes_the_request():
    assert encode(7, "SHOOT") == b"#7 SHOOT\n"


@pytest.mark.parametrize("command", ["", "SHOOT\nFOCUS", "BAD\r"])
def test_encode_rejects_commands_that_would_forge_extra_lines(command: str):
    with pytest.raises(ValueError):
        encode(1, command)


def test_decode_ok_and_err():
    assert decode("#3 OK") == Response(3, True, "")
    assert decode("#3 ERR camera not connected") == Response(3, False, "camera not connected")


def test_decode_ready_banner():
    assert decode("READY 1.0.0") == Ready("1.0.0")


@pytest.mark.parametrize("line", ["", "   ", "hello", "#x OK", "OK", "# 3 OK"])
def test_unrecognised_lines_are_dropped_not_raised(line: str):
    """The firmware may log to the same port; a stray debug print must not take
    down the shutter link mid-shoot."""
    assert decode(line) is None


# ── line reassembly ──────────────────────────────────────────────────────────


def test_reader_joins_a_split_line():
    reader = LineReader()
    assert reader.feed(b"#1 O") == []
    assert reader.feed(b"K\n") == ["#1 OK"]


def test_reader_splits_coalesced_lines():
    reader = LineReader()
    assert reader.feed(b"#1 OK\n#2 OK\nREADY 1.0\n") == ["#1 OK", "#2 OK", "READY 1.0"]


def test_reader_keeps_an_incomplete_tail():
    reader = LineReader()
    assert reader.feed(b"#1 OK\n#2 O") == ["#1 OK"]
    assert reader.pending == b"#2 O"


def test_reader_survives_binary_noise():
    reader = LineReader()
    assert reader.feed(b"\xff\xfe#1 OK\n")[0].endswith("#1 OK")


# ── the exchange ─────────────────────────────────────────────────────────────


def test_shoot_round_trips():
    shutter, transport, _ = make(always_ok)
    shutter.shoot()
    assert transport.written == [b"#1 SHOOT\n"]


def test_request_ids_increment():
    shutter, transport, _ = make(always_ok)
    shutter.ping()
    shutter.focus()
    shutter.shoot()
    assert transport.written == [b"#1 PING\n", b"#2 FOCUS\n", b"#3 SHOOT\n"]


def test_err_becomes_a_shutter_error_carrying_the_reason():
    def responder(line: str) -> bytes:
        rid = line.split()[0].lstrip("#")
        return f"#{rid} ERR camera not connected\n".encode()

    shutter, _, _ = make(responder)
    with pytest.raises(ShutterError, match="camera not connected"):
        shutter.shoot()


def test_silence_becomes_a_timeout():
    shutter, _, _ = make(responder=None, timeout_s=1.0)
    with pytest.raises(ShutterTimeout):
        shutter.ping()


def test_a_stale_reply_is_discarded_rather_than_counted_as_success():
    """The reason ids exist.

    A reply the host already gave up on must not be matched against the next
    request — that shows up as an intermittently missing frame and is close to
    undiagnosable on site.
    """
    shutter, transport, _ = make(responder=None, timeout_s=1.0)

    with pytest.raises(ShutterTimeout):
        shutter.ping()  # request #1, never answered

    transport.push(b"#1 OK\n")  # the late reply finally lands
    with pytest.raises(ShutterTimeout):
        shutter.focus()  # request #2 must not consume it

    assert shutter.stale_replies == 1


def test_a_reply_after_the_stale_one_still_matches():
    shutter, transport, _ = make(responder=None, timeout_s=1.0)
    with pytest.raises(ShutterTimeout):
        shutter.ping()

    transport.push(b"#1 OK\n#2 OK\n")
    shutter.focus()  # #2 — the stale #1 is skipped, then #2 matches

    assert shutter.stale_replies == 1


def test_a_board_reset_mid_command_is_reported_not_ignored():
    """READY means the board rebooted and lost its BLE pairing, so the command
    in flight is void rather than merely late."""
    shutter, transport, _ = make(responder=lambda line: b"READY 2.1.0\n")

    with pytest.raises(ShutterError, match="board reset"):
        shutter.shoot()
    assert shutter.firmware_version == "2.1.0"


def test_firmware_log_lines_do_not_break_the_exchange():
    def responder(line: str) -> bytes:
        rid = line.split()[0].lstrip("#")
        return b"scanning for camera...\n" + f"#{rid} OK\n".encode()

    shutter, _, _ = make(responder)
    shutter.shoot()  # must not raise


# ── connection handling ──────────────────────────────────────────────────────


def test_connects_lazily_on_first_command():
    shutter, transport, _ = make(always_ok)
    assert shutter.is_connected is False

    shutter.ping()
    assert shutter.is_connected is True


def test_a_failed_open_is_reported_as_not_connected():
    def explode():
        raise OSError("no such device")

    shutter = Esp32Shutter(open_transport=explode)
    with pytest.raises(ShutterNotConnected, match="cannot open"):
        shutter.ping()


def test_a_write_failure_closes_the_port_so_the_next_call_reopens():
    opened = []

    def opener():
        transport = FakeTransport(always_ok)
        opened.append(transport)
        return transport

    clock = FakeClock()
    shutter = Esp32Shutter(open_transport=opener, clock=clock, sleep=clock.sleep)

    shutter.ping()
    opened[0].fail_on_write = True
    with pytest.raises(ShutterNotConnected):
        shutter.ping()

    assert shutter.is_connected is False
    shutter.ping()  # reconnects
    assert len(opened) == 2


def test_a_read_failure_also_forces_a_reconnect():
    shutter, transport, _ = make(always_ok)
    shutter.ping()
    transport.fail_on_read = True
    transport.inbox.clear()

    with pytest.raises(ShutterNotConnected):
        shutter.ping()
    assert shutter.is_connected is False


def test_close_is_idempotent():
    shutter, _, _ = make(always_ok)
    shutter.ping()
    shutter.close()
    shutter.close()
    assert shutter.is_connected is False


def test_shoot_gets_a_longer_timeout_than_ping():
    """A camera waking over BLE takes longer than a link check."""
    slow = {"pings": 0}

    def responder(line: str) -> bytes:
        slow["pings"] += 1
        return b""

    shutter, _, clock = make(responder, timeout_s=1.0)

    with pytest.raises(ShutterTimeout):
        shutter.ping()
    after_ping = clock.now

    with pytest.raises(ShutterTimeout):
        shutter.shoot()

    assert clock.now - after_ping > after_ping
