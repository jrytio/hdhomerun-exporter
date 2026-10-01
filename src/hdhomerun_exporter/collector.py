"""Reads each configured device at scrape time and turns it into metric families."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from .parse import (
    ERROR_KINDS,
    ParseError,
    TunerDebug,
    client_host,
    parse_link,
    parse_streaminfo,
    parse_tuner_debug,
)
from .protocol import DEFAULT_PORT, Client, GetSetError, ProtocolError

log = logging.getLogger(__name__)

MAX_TUNERS = 16
# The device's reply to a getset name it does not have. Reading /tunerN/debug until
# this answer is how the tuner count is found; any OTHER error is a failed read.
UNKNOWN_VARIABLE = "unknown getset variable"


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    device_id: str

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def parse_targets(spec: str) -> list[Target]:
    """'ipv4[:port][=device_id],...' -> targets. Raises ValueError on anything malformed.

    Hosts must be IPv4 literals: DNS resolution happens outside the socket timeout,
    so a hostname would let one slow lookup overrun the per-device scrape budget.
    """
    targets = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        address, _, device_id = item.partition("=")
        host, sep, port_text = address.strip().rpartition(":")
        if not sep:
            host, port = address.strip(), DEFAULT_PORT
        else:
            try:
                port = int(port_text)
            except ValueError:
                raise ValueError(f"bad port in target {item!r}") from None
            if not 1 <= port <= 65535:
                raise ValueError(f"port out of range in target {item!r}")
        if not host:
            raise ValueError(f"missing host in target {item!r}")
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            raise ValueError(f"host is not an IPv4 address in target {item!r}") from None
        targets.append(Target(host, port, device_id.strip() or host))
    if not targets:
        raise ValueError("no targets configured")
    ids = [t.device_id for t in targets]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate device_id: {', '.join(duplicates)}")
    return targets


@dataclass(frozen=True)
class TunerSample:
    index: int
    debug: TunerDebug
    vchannel: str = ""
    name: str = ""
    client: str = ""


@dataclass(frozen=True)
class DeviceSample:
    hwmodel: str
    model: str
    firmware: str
    link: str
    tuners: tuple[TunerSample, ...]


def _get_optional(client: Client, name: str) -> str:
    """A get whose absence must not fail the device: GetSetError -> ''."""
    try:
        return client.get(name)
    except GetSetError:
        return ""


def read_device(target: Target, timeout: float) -> DeviceSample:
    """Read everything for one device over one connection, within `timeout` seconds."""
    with Client(target.host, target.port, deadline=time.monotonic() + timeout) as client:
        hwmodel = client.get("/sys/hwmodel")
        model = client.get("/sys/model")
        firmware = client.get("/sys/version")
        link = parse_link(client.get("/sys/debug"))
        tuners = []
        for index in range(MAX_TUNERS):
            try:
                debug = parse_tuner_debug(client.get(f"/tuner{index}/debug"))
            except GetSetError as exc:
                if UNKNOWN_VARIABLE not in str(exc):
                    raise
                break  # past the last tuner
            if not debug.locked:
                tuners.append(TunerSample(index, debug))
                continue
            vchannel = _get_optional(client, f"/tuner{index}/vchannel")
            program = _get_optional(client, f"/tuner{index}/program")
            streams = parse_streaminfo(_get_optional(client, f"/tuner{index}/streaminfo"))
            target_url = _get_optional(client, f"/tuner{index}/target")
            stream_vchannel, name = streams.get(program, ("", ""))
            if vchannel in ("", "none"):
                vchannel = stream_vchannel
            tuners.append(TunerSample(index, debug, vchannel, name, client_host(target_url)))
    if not tuners:
        raise ProtocolError("device reported no tuners")
    return DeviceSample(hwmodel, model, firmware, link, tuners=tuple(tuners))


Result = tuple[Target, DeviceSample | None, float]


class HDHomeRunCollector(Collector):
    """Reads every target on collect(), single-flight, reusing a result for cache_seconds.

    Anything in the cluster can GET /metrics. The lock means concurrent requests share
    one device read instead of each opening its own sessions, and the short cache
    absorbs bursts. At the normal 15s scrape interval every scrape still reads live.
    """

    def __init__(self, targets: list[Target], timeout: float, cache_seconds: float = 1.0) -> None:
        self.targets = targets
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._results: list[Result] = []
        self._read_at = float("-inf")

    def _scrape(self, target: Target) -> Result:
        start = time.monotonic()
        sample = None
        try:
            sample = read_device(target, self.timeout)
        except (OSError, ProtocolError, GetSetError, ParseError) as exc:
            log.warning(
                "scrape of %s (%s) failed: %s: %s",
                target.device_id,
                target.address,
                type(exc).__name__,
                exc,
            )
        except Exception:
            log.exception("scrape of %s (%s) failed unexpectedly", target.device_id, target.address)
        return target, sample, time.monotonic() - start

    def collect(self) -> Iterator[Metric]:
        arrived = time.monotonic()
        with self._lock:
            # Reuse the last result if it completed after this request arrived (the
            # request was waiting on that in-flight read: single-flight, cache or not),
            # or if it is younger than cache_seconds.
            joined_in_flight = self._read_at >= arrived
            fresh = time.monotonic() - self._read_at < self.cache_seconds
            if not (joined_in_flight or fresh):
                with ThreadPoolExecutor(max_workers=len(self.targets)) as pool:
                    self._results = list(pool.map(self._scrape, self.targets))
                self._read_at = time.monotonic()
            results = self._results
        yield from build_families(results)


def build_families(results: Iterable[Result]) -> list[Metric]:
    base = ["device_id", "target"]
    tuner = [*base, "tuner"]

    def gauge(name: str, doc: str, labels: list[str]) -> GaugeMetricFamily:
        return GaugeMetricFamily(name, doc, labels=labels)

    up = gauge("hdhomerun_up", "1 if the whole device read succeeded, else 0.", base)
    duration = gauge(
        "hdhomerun_scrape_duration_seconds", "Wall time spent reading this device.", base
    )
    info = gauge(
        "hdhomerun_info", "Device identity; always 1.", [*base, "hwmodel", "model", "firmware"]
    )
    tuner_count = gauge("hdhomerun_tuners", "Number of tuners the device reports.", base)
    link = gauge(
        "hdhomerun_ethernet_link_info",
        "Ethernet link mode from /sys/debug (e.g. 100f); always 1.",
        [*base, "link"],
    )
    in_use = gauge("hdhomerun_tuner_in_use", "1 if the tuner has a channel set.", tuner)
    locked = gauge("hdhomerun_tuner_locked", "1 if the tuner has a modulation lock.", tuner)
    strength = gauge(
        "hdhomerun_tuner_signal_strength_percent", "Signal strength (ss), 0-100.", tuner
    )
    quality = gauge(
        "hdhomerun_tuner_signal_quality_percent", "Signal-to-noise quality (snq), 0-100.", tuner
    )
    symbol = gauge(
        "hdhomerun_tuner_symbol_quality_percent",
        "Symbol quality (seq), 0-100; below 100 means uncorrectable errors.",
        tuner,
    )
    bitrate = gauge(
        "hdhomerun_tuner_bitrate_bits_per_second",
        "Bitrate per stage: device (demodulator), transport_stream (filtered), "
        "network (sent to the client).",
        [*tuner, "stage"],
    )
    pps = gauge(
        "hdhomerun_tuner_network_packets_per_second",
        "Packets per second sent to the client.",
        tuner,
    )
    errors = CounterMetricFamily(
        "hdhomerun_tuner_errors",
        "Errors from /tunerN/debug (te, crc, resync, overflow, err); restarts at 0 "
        "with each new stream.",
        labels=[*tuner, "kind"],
    )
    channel = gauge(
        "hdhomerun_tuner_channel_info",
        "The channel a locked tuner is on and the client streaming it; always 1.",
        [*tuner, "vchannel", "name", "frequency_hz", "modulation", "client"],
    )

    for target, sample, seconds in results:
        device = [target.device_id, target.address]
        up.add_metric(device, 0 if sample is None else 1)
        duration.add_metric(device, seconds)
        if sample is None:
            continue
        info.add_metric([*device, sample.hwmodel, sample.model, sample.firmware], 1)
        tuner_count.add_metric(device, len(sample.tuners))
        link.add_metric([*device, sample.link], 1)
        for t in sample.tuners:
            labels = [*device, str(t.index)]
            d = t.debug
            in_use.add_metric(labels, int(d.in_use))
            locked.add_metric(labels, int(d.locked))
            strength.add_metric(labels, d.signal_strength)
            quality.add_metric(labels, d.signal_quality)
            symbol.add_metric(labels, d.symbol_quality)
            for stage, bps in d.bitrate.items():
                bitrate.add_metric([*labels, stage], bps)
            pps.add_metric(labels, d.network_pps)
            for kind in ERROR_KINDS:
                errors.add_metric([*labels, kind], d.errors[kind])
            if d.locked:
                channel.add_metric(
                    [*labels, t.vchannel, t.name, d.frequency_hz, d.modulation, t.client], 1
                )

    return [
        up,
        duration,
        info,
        tuner_count,
        link,
        in_use,
        locked,
        strength,
        quality,
        symbol,
        bitrate,
        pps,
        errors,
        channel,
    ]
