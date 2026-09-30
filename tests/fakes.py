"""An in-process fake HDHomeRun that answers getset requests from a dict."""

from __future__ import annotations

import socket
import struct
import threading
import time

from hdhomerun_exporter.protocol import (
    TAG_ERROR,
    TAG_NAME,
    TAG_VALUE,
    TYPE_GETSET_RPY,
    decode_frame,
    encode_frame,
    encode_tlv,
)


class FakeError(str):
    """A value that the fake device answers with an ERROR tag carrying this text."""


def encode_reply(name: str, values: dict[str, str]) -> bytes:
    """What the real device sends: NAME + VALUE, or ERROR alone for an unknown name."""
    if isinstance(values.get(name), FakeError):
        payload = encode_tlv(TAG_ERROR, values[name].encode() + b"\0")
    elif name in values:
        payload = encode_tlv(TAG_NAME, name.encode() + b"\0") + encode_tlv(
            TAG_VALUE, values[name].encode() + b"\0"
        )
    else:
        payload = encode_tlv(TAG_ERROR, b"ERROR: unknown getset variable\0")
    return encode_frame(TYPE_GETSET_RPY, payload)


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


class FakeDevice:
    """Modes: 'normal'; 'stall' (read the request, never answer);
    'truncate' (send half a reply, then close)."""

    def __init__(self, values: dict[str, str], mode: str = "normal", delay: float = 0.0) -> None:
        self.values = values
        self.mode = mode
        self.delay = delay  # seconds to wait before each reply
        self.requests: list[str] = []
        self._server = socket.create_server(("127.0.0.1", 0))
        self.port = self._server.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    header = _recv_exact(conn, 4)
                    if header is None:
                        return
                    (_, plen) = struct.unpack(">HH", header)
                    rest = _recv_exact(conn, plen + 4)
                    if rest is None:
                        return
                    _, tags = decode_frame(header + rest)
                    name = tags[TAG_NAME].rstrip(b"\0").decode()
                    self.requests.append(name)
                    if self.mode == "stall":
                        conn.recv(1)  # blocks until the client gives up and closes
                        return
                    time.sleep(self.delay)
                    reply = encode_reply(name, self.values)
                    if self.mode == "truncate":
                        conn.sendall(reply[: len(reply) // 2])
                        return
                    conn.sendall(reply)
                except OSError:
                    return

    def close(self) -> None:
        self._server.close()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
