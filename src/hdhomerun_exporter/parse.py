"""Parsers for the text values the device returns over getset."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class ParseError(ValueError):
    """A value the exporter depends on is missing or not a number."""


# kind -> (debug section, key) in /tunerN/debug. "net: stop=" is deliberately absent:
# it is the reason code for the last stream stop, not a count.
ERROR_FIELDS = {
    "transport": ("ts", "te"),
    "crc": ("ts", "crc"),
    "resync": ("dev", "resync"),
    "overflow": ("dev", "overflow"),
    "network": ("net", "err"),
}
BITRATE_STAGES = {"device": "dev", "transport_stream": "ts", "network": "net"}


def parse_sections(text: str) -> dict[str, dict[str, str]]:
    """Parse 'section: k=v k=v' lines into {section: {k: v}}.

    Lenient on purpose: lines without a colon and tokens without '=' are ignored,
    so a firmware that adds a free-text line does not take the device down.
    Strictness lives in the callers, which demand the keys they use.
    """
    sections: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep or not name.strip():
            continue
        fields = {}
        for token in rest.split():
            key, eq, value = token.partition("=")
            if eq:
                fields[key] = value
        sections[name.strip()] = fields
    return sections


@dataclass(frozen=True)
class TunerDebug:
    channel: str | None  # e.g. "8vsb:575000000"; None when "none"
    lock: str | None  # e.g. "8vsb:575000000"; None when "none"
    signal_strength: int
    signal_quality: int
    symbol_quality: int
    # Only the tun: status above is required. What follows holds whatever the firmware
    # printed, so a model with a different set of counters still yields the rest.
    bitrate: dict[str, int]  # stage -> bits/s, stages from BITRATE_STAGES
    network_pps: int | None
    errors: dict[str, int]  # kind -> count, kinds from ERROR_FIELDS

    @property
    def in_use(self) -> bool:
        return self.channel is not None

    @property
    def locked(self) -> bool:
        return self.lock is not None

    @property
    def modulation(self) -> str:
        return self.lock.split(":", 1)[0] if self.lock else ""

    @property
    def frequency_hz(self) -> str:
        for value in (self.lock, self.channel):
            if value and value.rsplit(":", 1)[-1].isdigit():
                return value.rsplit(":", 1)[-1]
        return ""


def _none(value: str) -> str | None:
    return None if value == "none" else value


def parse_tuner_debug(text: str) -> TunerDebug:
    sections = parse_sections(text)

    def field(section: str, key: str) -> str:
        try:
            return sections[section][key]
        except KeyError:
            raise ParseError(f"tuner debug is missing {section}.{key}") from None

    def number(section: str, key: str) -> int:
        value = field(section, key)
        try:
            return int(value)
        except ValueError:
            raise ParseError(f"tuner debug {section}.{key}={value!r} is not an integer") from None

    def optional(section: str, key: str) -> int | None:
        return number(section, key) if key in sections.get(section, {}) else None

    def present(fields: dict[str, tuple[str, str]]) -> dict[str, int]:
        values = {name: optional(*where) for name, where in fields.items()}
        return {name: value for name, value in values.items() if value is not None}

    return TunerDebug(
        channel=_none(field("tun", "ch")),
        lock=_none(field("tun", "lock")),
        signal_strength=number("tun", "ss"),
        signal_quality=number("tun", "snq"),
        symbol_quality=number("tun", "seq"),
        bitrate=present({stage: (section, "bps") for stage, section in BITRATE_STAGES.items()}),
        network_pps=optional("net", "pps"),
        errors=present(ERROR_FIELDS),
    )


def parse_link(sys_debug: str) -> str:
    """Ethernet link mode from /sys/debug ('100f', '10h', ...), or 'unknown'."""
    return parse_sections(sys_debug).get("eth", {}).get("link") or "unknown"


_STREAM_LINE = re.compile(r"^(\d+):\s*(\S+)\s*(.*?)\s*$")


def parse_streaminfo(text: str) -> dict[str, tuple[str, str]]:
    """/tunerN/streaminfo -> {program: (vchannel, name)}; 'tsid=...' lines are skipped."""
    programs = {}
    for line in text.splitlines():
        match = _STREAM_LINE.match(line.strip())
        if match:
            programs[match.group(1)] = (match.group(2), match.group(3))
    return programs


def client_host(target: str) -> str:
    """/tunerN/target ('http://192.168.1.20:60207', 'udp://...', 'none') -> client host or ''."""
    if not target or target == "none":
        return ""
    return urlsplit(target).hostname or ""
