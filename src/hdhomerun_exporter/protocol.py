"""HDHomeRun TCP control protocol (libhdhomerun "getset"), port 65001.

Frame: u16 big-endian packet type, u16 big-endian payload length, the
payload (a run of TLVs), then a CRC32 of header+payload as a u32
LITTLE-endian. TLV lengths are one byte up to 127, else two bytes:
0x80 | (len & 0x7f), then len >> 7.

A get is a GETSET_REQ carrying one NAME tag. The reply is a GETSET_RPY
carrying NAME + VALUE, or only an ERROR tag (e.g. "ERROR: unknown getset
variable") -- the device does not echo the name on an error.
"""

from __future__ import annotations

import socket
import struct
import time
import zlib

DEFAULT_PORT = 65001
TYPE_GETSET_REQ = 0x0004
TYPE_GETSET_RPY = 0x0005
TAG_NAME = 0x03
TAG_VALUE = 0x04
TAG_ERROR = 0x05
# Largest reply payload accepted. /tunerN/streaminfo on a busy multiplex is
# the biggest value seen (~130 bytes); this only guards against garbage.
MAX_PAYLOAD = 3072


class ProtocolError(Exception):
    """The reply was malformed or corrupt, or the connection ended mid-reply."""


class GetSetError(Exception):
    """The device answered with an error tag (e.g. an unknown getset variable)."""


def encode_length(n: int) -> bytes:
    if not 0 <= n <= 0x7FFF:
        raise ValueError(f"TLV length out of range: {n}")
    if n <= 0x7F:
        return bytes([n])
    return bytes([0x80 | (n & 0x7F), n >> 7])


def encode_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(value)) + value


def encode_frame(ptype: int, payload: bytes) -> bytes:
    body = struct.pack(">HH", ptype, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body))


def encode_get(name: str) -> bytes:
    return encode_frame(TYPE_GETSET_REQ, encode_tlv(TAG_NAME, name.encode("ascii") + b"\0"))


def decode_frame(frame: bytes) -> tuple[int, dict[int, bytes]]:
    """Validate length and CRC, return (packet type, {tag: raw value})."""
    if len(frame) < 8:
        raise ProtocolError(f"frame too short: {len(frame)} bytes")
    ptype, plen = struct.unpack(">HH", frame[:4])
    if len(frame) != plen + 8:
        raise ProtocolError(f"frame is {len(frame)} bytes, header says {plen + 8}")
    (crc,) = struct.unpack("<I", frame[-4:])
    if zlib.crc32(frame[:-4]) != crc:
        raise ProtocolError("CRC mismatch")
    payload = frame[4:-4]
    tags: dict[int, bytes] = {}
    i = 0
    while i < len(payload):
        if i + 2 > len(payload):
            raise ProtocolError("truncated TLV header")
        tag, length = payload[i], payload[i + 1]
        i += 2
        if length & 0x80:
            if i >= len(payload):
                raise ProtocolError("truncated TLV length")
            length = (length & 0x7F) | (payload[i] << 7)
            i += 1
        if i + length > len(payload):
            raise ProtocolError("truncated TLV value")
        tags[tag] = payload[i : i + length]
        i += length
    return ptype, tags


def _text(raw: bytes) -> str:
    return raw.rstrip(b"\0").decode("utf-8", errors="replace")


def decode_getset_reply(frame: bytes) -> str:
    ptype, tags = decode_frame(frame)
    if ptype != TYPE_GETSET_RPY:
        raise ProtocolError(f"unexpected packet type 0x{ptype:04x}")
    if TAG_ERROR in tags:
        raise GetSetError(_text(tags[TAG_ERROR]))
    if TAG_VALUE not in tags:
        raise ProtocolError("reply carries neither a value nor an error")
    return _text(tags[TAG_VALUE])


class Client:
    """One TCP connection to one device; every operation shares one absolute deadline.

    The deadline is a time.monotonic() value. Any connect/send/recv that would run
    past it raises TimeoutError, so a scrape of a stalled device is bounded as a whole,
    not per call.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT, *, deadline: float) -> None:
        self.host = host
        self.port = port
        self.deadline = deadline
        self._sock: socket.socket | None = None

    def _remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"{self.host}:{self.port}: deadline exceeded")
        return left

    def __enter__(self) -> Client:
        self._sock = socket.create_connection((self.host, self.port), timeout=self._remaining())
        return self

    def __exit__(self, *exc: object) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def get(self, name: str) -> str:
        if self._sock is None:
            raise RuntimeError("use Client as a context manager")
        self._sock.settimeout(self._remaining())
        self._sock.sendall(encode_get(name))
        header = self._recv_exact(4)
        _, plen = struct.unpack(">HH", header)
        if plen > MAX_PAYLOAD:
            raise ProtocolError(f"reply payload too large: {plen} bytes")
        return decode_getset_reply(header + self._recv_exact(plen + 4))

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            self._sock.settimeout(self._remaining())
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ProtocolError(f"connection closed after {len(buf)} of {n} bytes")
            buf += chunk
        return bytes(buf)
