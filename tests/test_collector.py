import threading
import time

import pytest
from fakes import FakeDevice, FakeError
from fixtures import DEVICE_VALUES, TUNER_DEBUG_IDLE
from prometheus_client import CollectorRegistry

from hdhomerun_exporter.collector import HDHomeRunCollector, Target, parse_targets


def scrape(*targets: Target, timeout: float = 1.0) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(HDHomeRunCollector(list(targets), timeout, cache_seconds=0))
    return registry


def device_labels(target: Target) -> dict[str, str]:
    return {"device_id": target.device_id, "target": target.address}


def tuner_labels(target: Target, tuner: int) -> dict[str, str]:
    return {**device_labels(target), "tuner": str(tuner)}


@pytest.fixture
def device():
    d = FakeDevice(DEVICE_VALUES)
    yield d
    d.close()


def test_full_scrape(device):
    t = Target("127.0.0.1", device.port, "104705FA")
    r = scrape(t)
    dev = device_labels(t)
    assert r.get_sample_value("hdhomerun_up", dev) == 1
    assert r.get_sample_value("hdhomerun_tuners", dev) == 2
    assert (
        r.get_sample_value(
            "hdhomerun_info",
            {**dev, "hwmodel": "HDHR4-2US", "model": "hdhomerun4_atsc", "firmware": "20260313"},
        )
        == 1
    )
    assert r.get_sample_value("hdhomerun_ethernet_link_info", {**dev, "link": "100f"}) == 1

    t0, t1 = tuner_labels(t, 0), tuner_labels(t, 1)
    assert r.get_sample_value("hdhomerun_tuner_in_use", t0) == 1
    assert r.get_sample_value("hdhomerun_tuner_locked", t0) == 1
    assert r.get_sample_value("hdhomerun_tuner_signal_quality_percent", t0) == 98
    assert (
        r.get_sample_value("hdhomerun_tuner_bitrate_bits_per_second", {**t0, "stage": "network"})
        == 9367360
    )
    assert r.get_sample_value("hdhomerun_tuner_network_packets_per_second", t0) == 802
    assert r.get_sample_value("hdhomerun_tuner_errors_total", {**t0, "kind": "crc"}) == 0
    channel = {
        **t0,
        "vchannel": "5.1",
        "name": "DEMO",
        "frequency_hz": "575000000",
        "modulation": "8vsb",
        "client": "10.42.21.20",
    }
    assert r.get_sample_value("hdhomerun_tuner_channel_info", channel) == 1

    assert r.get_sample_value("hdhomerun_tuner_in_use", t1) == 0
    assert r.get_sample_value("hdhomerun_tuner_locked", t1) == 0
    # An idle tuner has no channel_info series at all.
    samples = [
        s
        for m in r.collect()
        if m.name == "hdhomerun_tuner_channel_info"
        for s in m.samples
        if s.labels["tuner"] == "1"
    ]
    assert samples == []


def test_errors_are_a_counter_per_kind():
    debug = DEVICE_VALUES["/tuner0/debug"].replace("te=0", "te=7").replace("err=0", "err=2")
    d = FakeDevice({**DEVICE_VALUES, "/tuner0/debug": debug})
    try:
        t = Target("127.0.0.1", d.port, "x")
        r = scrape(t)
        t0 = tuner_labels(t, 0)
        assert r.get_sample_value("hdhomerun_tuner_errors_total", {**t0, "kind": "transport"}) == 7
        assert r.get_sample_value("hdhomerun_tuner_errors_total", {**t0, "kind": "network"}) == 2
        (family,) = [m for m in r.collect() if m.name == "hdhomerun_tuner_errors"]
        assert family.type == "counter"
    finally:
        d.close()


def test_stop_reason_is_not_exported_as_errors():
    # An idle tuner keeps the reason code of its last stream stop (seen live: 4 and 9).
    values = {**DEVICE_VALUES, "/tuner1/debug": TUNER_DEBUG_IDLE.replace("stop=0", "stop=9")}
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        errors = {
            s.labels["kind"]: s.value
            for m in scrape(t).collect()
            if m.name == "hdhomerun_tuner_errors"
            for s in m.samples
            if s.labels["tuner"] == "1" and s.name == "hdhomerun_tuner_errors_total"
        }
        assert errors == {"transport": 0, "crc": 0, "resync": 0, "overflow": 0, "network": 0}
    finally:
        d.close()


def test_idle_tuner_skips_the_channel_gets(device):
    scrape(Target("127.0.0.1", device.port, "x")).get_sample_value("hdhomerun_up", {})
    assert "/tuner1/vchannel" not in device.requests
    assert "/tuner0/vchannel" in device.requests


def test_refused_connection_is_down_with_no_other_series():
    import socket

    s = socket.create_server(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    t = Target("127.0.0.1", port, "dead")
    r = scrape(t)
    assert r.get_sample_value("hdhomerun_up", device_labels(t)) == 0
    assert r.get_sample_value("hdhomerun_tuners", device_labels(t)) is None


def test_stalled_device_is_down_within_budget():
    stalled = FakeDevice(DEVICE_VALUES, mode="stall")
    try:
        t = Target("127.0.0.1", stalled.port, "stalled")
        start = time.monotonic()
        r = scrape(t, timeout=0.5)
        assert r.get_sample_value("hdhomerun_up", device_labels(t)) == 0
        assert time.monotonic() - start < 1.5
    finally:
        stalled.close()


def test_dead_device_does_not_affect_a_live_one(device):
    stalled = FakeDevice(DEVICE_VALUES, mode="stall")
    try:
        live = Target("127.0.0.1", device.port, "live")
        dead = Target("127.0.0.1", stalled.port, "dead")
        start = time.monotonic()
        r = scrape(live, dead, timeout=0.5)
        assert r.get_sample_value("hdhomerun_up", device_labels(live)) == 1
        assert r.get_sample_value("hdhomerun_up", device_labels(dead)) == 0
        assert time.monotonic() - start < 1.5  # polled concurrently, not back to back
    finally:
        stalled.close()


def test_malformed_debug_marks_device_down():
    values = {**DEVICE_VALUES, "/tuner0/debug": "garbage"}
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        assert scrape(t).get_sample_value("hdhomerun_up", device_labels(t)) == 0
    finally:
        d.close()


def test_tuned_but_unlocked_tuner_has_no_channel_info():
    values = {**DEVICE_VALUES, "/tuner1/debug": TUNER_DEBUG_IDLE.replace("ch=none", "ch=auto:5")}
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        r = scrape(t)
        t1 = tuner_labels(t, 1)
        assert r.get_sample_value("hdhomerun_up", device_labels(t)) == 1
        assert r.get_sample_value("hdhomerun_tuner_in_use", t1) == 1
        assert r.get_sample_value("hdhomerun_tuner_locked", t1) == 0
        channel_series = [
            s
            for m in r.collect()
            if m.name == "hdhomerun_tuner_channel_info"
            for s in m.samples
            if s.labels["tuner"] == "1"
        ]
        assert channel_series == []
    finally:
        d.close()


def test_locked_tuner_with_missing_optional_values():
    # Locked, but no client streaming yet and the variables the firmware may not have.
    values = {
        k: v
        for k, v in DEVICE_VALUES.items()
        if k not in ("/tuner0/vchannel", "/tuner0/target", "/tuner0/streaminfo")
    }
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        r = scrape(t)
        assert r.get_sample_value("hdhomerun_up", device_labels(t)) == 1
        channel = {
            **tuner_labels(t, 0),
            "vchannel": "",
            "name": "",
            "frequency_hz": "575000000",
            "modulation": "8vsb",
            "client": "",
        }
        assert r.get_sample_value("hdhomerun_tuner_channel_info", channel) == 1
    finally:
        d.close()


def test_vchannel_falls_back_to_streaminfo():
    values = {**DEVICE_VALUES, "/tuner0/vchannel": "none"}
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        r = scrape(t)
        channel = {
            **tuner_labels(t, 0),
            "vchannel": "5.1",
            "name": "DEMO",
            "frequency_hz": "575000000",
            "modulation": "8vsb",
            "client": "10.42.21.20",
        }
        assert r.get_sample_value("hdhomerun_tuner_channel_info", channel) == 1
    finally:
        d.close()


def test_other_device_error_during_tuner_enumeration_marks_device_down():
    # Only "unknown getset variable" means "past the last tuner"; any other error is a
    # failed read, not a silently shorter tuner list.
    values = {**DEVICE_VALUES, "/tuner1/debug": FakeError("ERROR: resource locked")}
    d = FakeDevice(values)
    try:
        t = Target("127.0.0.1", d.port, "x")
        assert scrape(t).get_sample_value("hdhomerun_up", device_labels(t)) == 0
    finally:
        d.close()


def test_parse_targets():
    assert parse_targets("10.42.21.53=104705FA") == [Target("10.42.21.53", 65001, "104705FA")]
    assert parse_targets(" 10.0.0.1:1234 , 10.0.0.2 ") == [
        Target("10.0.0.1", 1234, "10.0.0.1"),
        Target("10.0.0.2", 65001, "10.0.0.2"),
    ]


@pytest.mark.parametrize(
    "spec, message",
    [
        ("", "no targets"),
        (" , ", "no targets"),
        ("10.0.0.1:notaport", "bad port"),
        ("10.0.0.1:70000", "out of range"),
        (":65001", "missing host"),
        # Hostnames are refused: DNS resolution is not bounded by the scrape deadline.
        ("hdhomerun.local", "not an IPv4 address"),
        ("10.0.0.1=x,10.0.0.2=x", "duplicate device_id: x"),
        ("10.0.0.1,10.0.0.1", "duplicate device_id: 10.0.0.1"),
    ],
)
def test_parse_targets_rejects(spec, message):
    with pytest.raises(ValueError, match=message):
        parse_targets(spec)


def device_reads(device: FakeDevice) -> int:
    return device.requests.count("/sys/hwmodel")  # read once per device read


def test_concurrent_scrapes_share_one_device_read():
    # Single-flight: however many clients hit /metrics at once, the device sees one read.
    d = FakeDevice(DEVICE_VALUES, delay=0.02)
    try:
        c = HDHomeRunCollector([Target("127.0.0.1", d.port, "x")], 2.0, cache_seconds=1.0)
        threads = [threading.Thread(target=lambda: list(c.collect())) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert device_reads(d) == 1
    finally:
        d.close()


def test_cached_result_expires():
    d = FakeDevice(DEVICE_VALUES)
    try:
        c = HDHomeRunCollector([Target("127.0.0.1", d.port, "x")], 1.0, cache_seconds=0.2)
        list(c.collect())
        list(c.collect())  # within the window: served from cache
        assert device_reads(d) == 1
        time.sleep(0.3)
        list(c.collect())
        assert device_reads(d) == 2
    finally:
        d.close()


def test_cache_disabled_reads_every_time():
    d = FakeDevice(DEVICE_VALUES)
    try:
        c = HDHomeRunCollector([Target("127.0.0.1", d.port, "x")], 1.0, cache_seconds=0)
        list(c.collect())
        list(c.collect())
        assert device_reads(d) == 2
    finally:
        d.close()


def test_concurrent_scrapes_share_one_read_even_with_cache_off():
    # Single-flight must not depend on the cache: requests that arrive while a read is
    # in flight join it rather than queueing up their own reads behind the lock.
    d = FakeDevice(DEVICE_VALUES, delay=0.02)
    try:
        c = HDHomeRunCollector([Target("127.0.0.1", d.port, "x")], 2.0, cache_seconds=0)
        threads = [threading.Thread(target=lambda: list(c.collect())) for _ in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert device_reads(d) == 1
    finally:
        d.close()
