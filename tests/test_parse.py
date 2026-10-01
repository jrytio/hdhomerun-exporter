import pytest
from fixtures import STREAMINFO, SYS_DEBUG, TUNER_DEBUG_IDLE, TUNER_DEBUG_STREAMING

from hdhomerun_exporter.parse import (
    ParseError,
    client_host,
    parse_link,
    parse_streaminfo,
    parse_tuner_debug,
)


def test_streaming_tuner_debug():
    d = parse_tuner_debug(TUNER_DEBUG_STREAMING)
    assert d.in_use and d.locked
    assert (d.signal_strength, d.signal_quality, d.symbol_quality) == (100, 98, 100)
    assert d.bitrate == {"device": 19466272, "transport_stream": 9366912, "network": 9367360}
    assert d.network_pps == 802
    assert d.errors == {"transport": 0, "crc": 0, "resync": 0, "overflow": 0, "network": 0}
    assert d.modulation == "8vsb"
    assert d.frequency_hz == "575000000"


def test_idle_tuner_debug():
    d = parse_tuner_debug(TUNER_DEBUG_IDLE)
    assert not d.in_use and not d.locked
    assert d.modulation == "" and d.frequency_hz == ""


def test_tuned_but_unlocked_tuner():
    # Antenna unplugged or channel off-air: a channel is set, there is no lock.
    text = TUNER_DEBUG_IDLE.replace("ch=none", "ch=auto:575000000")
    d = parse_tuner_debug(text)
    assert d.in_use and not d.locked
    assert d.modulation == ""
    assert d.frequency_hz == "575000000"


def test_error_counters_map_to_kinds():
    text = (
        TUNER_DEBUG_STREAMING.replace("te=0", "te=7")
        .replace("crc=0", "crc=3")
        .replace("resync=0", "resync=1")
        .replace("overflow=0", "overflow=5")
        .replace("err=0", "err=2")
    )
    d = parse_tuner_debug(text)
    assert d.errors == {"transport": 7, "crc": 3, "resync": 1, "overflow": 5, "network": 2}


def test_stop_reason_is_not_an_error_count():
    # net stop= is the reason code for the last stream stop (0 while streaming; an idle
    # tuner keeps showing e.g. 4 or 9), so it must never be counted as errors.
    d = parse_tuner_debug(TUNER_DEBUG_IDLE.replace("stop=0", "stop=9"))
    assert d.errors == {"transport": 0, "crc": 0, "resync": 0, "overflow": 0, "network": 0}


def test_missing_tuner_status_field_raises():
    with pytest.raises(ParseError, match="tun.snq"):
        parse_tuner_debug(TUNER_DEBUG_STREAMING.replace(" snq=98", ""))


def test_fields_a_firmware_does_not_print_are_left_out():
    # No dev: line and no net bps: only the tun: status is required, so a firmware that
    # prints a different set of counters still yields what it does have.
    text = (
        "tun: ch=8vsb:575000000 lock=8vsb:575000000 ss=100 snq=98 seq=100 dbg=1\n"
        "ts:  bps=9366912 te=4 crc=0\n"
        "net: pps=802 err=0 stop=0\n"
    )
    d = parse_tuner_debug(text)
    assert d.signal_quality == 98
    assert d.bitrate == {"transport_stream": 9366912}
    assert d.network_pps == 802
    assert d.errors == {"transport": 4, "crc": 0, "network": 0}


def test_missing_packet_rate_is_none():
    d = parse_tuner_debug(TUNER_DEBUG_STREAMING.replace(" pps=802", ""))
    assert d.network_pps is None


def test_non_numeric_counter_raises():
    with pytest.raises(ParseError, match="ts.te"):
        parse_tuner_debug(TUNER_DEBUG_STREAMING.replace("te=0", "te=many"))


def test_non_numeric_field_raises():
    with pytest.raises(ParseError, match="not an integer"):
        parse_tuner_debug(TUNER_DEBUG_STREAMING.replace("ss=100", "ss=high"))


def test_unknown_extra_lines_and_tokens_are_ignored():
    text = TUNER_DEBUG_STREAMING + "new: some free text\nno colon here\n"
    text = text.replace("snq=98", "snq=98 extra")
    assert parse_tuner_debug(text).signal_quality == 98


def test_link():
    assert parse_link(SYS_DEBUG) == "100f"
    assert parse_link("mem: nbk=5\n") == "unknown"


def test_streaminfo():
    programs = parse_streaminfo(STREAMINFO)
    assert programs["3"] == ("5.1", "DEMO")
    assert programs["9"] == ("5.7", "DEMO7")
    assert len(programs) == 7  # tsid line skipped


def test_client_host():
    assert client_host("http://10.42.21.20:60207") == "10.42.21.20"
    assert client_host("udp://10.42.2.198:5000") == "10.42.2.198"
    assert client_host("none") == ""
    assert client_host("") == ""
