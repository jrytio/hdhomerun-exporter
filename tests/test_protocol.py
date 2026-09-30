import time

import pytest
from fakes import FakeDevice, encode_reply
from fixtures import (
    DEVICE_VALUES,
    REQ_SYS_VERSION,
    RPY_SYS_VERSION,
    RPY_TUNER1_DEBUG,
    RPY_UNKNOWN_VARIABLE,
    TUNER_DEBUG_IDLE,
)

from hdhomerun_exporter.protocol import (
    Client,
    GetSetError,
    ProtocolError,
    decode_getset_reply,
    encode_get,
    encode_length,
)


def test_encode_get_matches_captured_request():
    assert encode_get("/sys/version") == REQ_SYS_VERSION


def test_decode_captured_value_reply():
    assert decode_getset_reply(RPY_SYS_VERSION) == "20260313"


def test_decode_captured_two_byte_length_reply():
    assert decode_getset_reply(RPY_TUNER1_DEBUG) == TUNER_DEBUG_IDLE


def test_decode_captured_error_reply_raises():
    with pytest.raises(GetSetError, match="unknown getset variable"):
        decode_getset_reply(RPY_UNKNOWN_VARIABLE)


def test_decode_rejects_bad_crc():
    corrupt = RPY_SYS_VERSION[:-1] + bytes([RPY_SYS_VERSION[-1] ^ 0xFF])
    with pytest.raises(ProtocolError, match="CRC"):
        decode_getset_reply(corrupt)


def test_decode_rejects_wrong_packet_type():
    request = encode_get("/sys/version")  # a GETSET_REQ is not a reply
    with pytest.raises(ProtocolError, match="packet type"):
        decode_getset_reply(request)


def test_decode_rejects_length_mismatch():
    with pytest.raises(ProtocolError, match="header says"):
        decode_getset_reply(RPY_SYS_VERSION[:-5] + RPY_SYS_VERSION[-4:])


def test_encode_length_forms():
    assert encode_length(127) == b"\x7f"
    assert encode_length(130) == b"\x82\x01"
    with pytest.raises(ValueError):
        encode_length(0x8000)


def test_fake_reply_encoder_matches_the_device():
    # The fake device is only trustworthy if it speaks byte-for-byte like the real one.
    assert encode_reply("/sys/version", {"/sys/version": "20260313"}) == RPY_SYS_VERSION
    assert encode_reply("/sys/uptime", {}) == RPY_UNKNOWN_VARIABLE


def test_client_gets_several_values_on_one_connection():
    device = FakeDevice(DEVICE_VALUES)
    try:
        with Client("127.0.0.1", device.port, deadline=time.monotonic() + 2) as client:
            assert client.get("/sys/version") == "20260313"
            assert client.get("/tuner1/debug") == TUNER_DEBUG_IDLE
            with pytest.raises(GetSetError):
                client.get("/tuner2/debug")
            assert client.get("/sys/hwmodel") == "HDHR4-2US"  # still usable after an error
    finally:
        device.close()


def test_client_times_out_on_a_stalled_device():
    device = FakeDevice(DEVICE_VALUES, mode="stall")
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with Client("127.0.0.1", device.port, deadline=time.monotonic() + 0.5) as client:
                client.get("/sys/version")
        assert time.monotonic() - start < 1.5
    finally:
        device.close()


def test_client_raises_on_a_reply_cut_short():
    device = FakeDevice(DEVICE_VALUES, mode="truncate")
    try:
        with pytest.raises(ProtocolError, match="connection closed"):
            with Client("127.0.0.1", device.port, deadline=time.monotonic() + 2) as client:
                client.get("/sys/version")
    finally:
        device.close()
