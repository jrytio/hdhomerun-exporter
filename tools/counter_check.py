"""Learn whether /tunerN/debug error counts reset per stream. Dev tool, not shipped.

Occupies ONE free tuner while it runs. Without an argument it first scans every
lineup channel for 15 s each (~4 min for 14 channels) and picks the one with
the most transport+CRC errors; with a guide number (e.g. 3.1) it skips the scan.
It then streams that channel twice, back to back, and reports whether the second
stream's counts started again from zero.

    HDHOMERUN_HOST=192.168.1.50 uv run python tools/counter_check.py [guide_number]
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from hdhomerun_exporter.parse import TunerDebug, parse_tuner_debug
from hdhomerun_exporter.protocol import Client, GetSetError

HOST = os.environ.get("HDHOMERUN_HOST", "")


def my_ip() -> str:
    with socket.create_connection((HOST, 65001), timeout=3) as s:
        return s.getsockname()[0]


def stream(guide: str, seconds: int) -> None:
    url = f"http://{HOST}:5004/auto/v{guide}?duration={seconds}"
    try:
        with urllib.request.urlopen(url, timeout=seconds + 15) as response:
            while response.read(1 << 16):
                pass
    except urllib.error.HTTPError as exc:
        # 503 = the device refused the tune (no free tuner, or it cannot receive the channel).
        print(f"{guide}: stream refused: HTTP {exc.code}")


def our_tuner(ip: str) -> tuple[int, TunerDebug] | None:
    with Client(HOST, deadline=time.monotonic() + 3) as client:
        for n in range(16):
            try:
                target = client.get(f"/tuner{n}/target")
            except GetSetError:
                return None
            if f"//{ip}:" in target:
                return n, parse_tuner_debug(client.get(f"/tuner{n}/debug"))
    return None


def watch(guide: str, seconds: int, ip: str) -> list[tuple[int, TunerDebug]]:
    thread = threading.Thread(target=stream, args=(guide, seconds), daemon=True)
    thread.start()
    readings = []
    while thread.is_alive():
        time.sleep(2)
        reading = our_tuner(ip)
        if reading:
            readings.append(reading)
    thread.join()
    return readings


def rf_errors(d: TunerDebug) -> int:
    return d.errors["transport"] + d.errors["crc"]


def main() -> None:
    if not HOST:
        sys.exit("set HDHOMERUN_HOST to the tuner's IP")
    ip = my_ip()
    if len(sys.argv) > 1:
        guide = sys.argv[1]
    else:
        with urllib.request.urlopen(f"http://{HOST}/lineup.json", timeout=5) as r:
            lineup = [c["GuideNumber"] for c in json.load(r)]
        worst, worst_errors = lineup[0], -1
        for g in lineup:
            readings = watch(g, 15, ip)
            if not readings:
                print(f"{g}: no reading (no free tuner?)")
                continue
            _, d = readings[-1]
            print(f"{g}: snq={d.signal_quality} seq={d.symbol_quality} errors={d.errors}")
            if rf_errors(d) > worst_errors:
                worst, worst_errors = g, rf_errors(d)
        guide = worst
        if worst_errors <= 0:
            print("No channel produced transport/CRC errors. Briefly loosen the antenna lead")
            print(f"during a rerun with a guide number, e.g.: tools/counter_check.py {guide}")
    print(f"\nStream 1 of {guide} (30 s):")
    first = watch(guide, 30, ip)
    for tuner, d in first:
        print(f"  tuner{tuner} {d.errors}")
    time.sleep(3)
    print(f"Stream 2 of {guide} (20 s):")
    second = watch(guide, 20, ip)
    for tuner, d in second:
        print(f"  tuner{tuner} {d.errors}")
    if not first or not second:
        sys.exit("inconclusive: a stream produced no readings")
    end1, start2 = rf_errors(first[-1][1]), rf_errors(second[0][1])
    counts = [rf_errors(d) for _, d in first]
    monotonic = all(b >= a for a, b in zip(counts, counts[1:], strict=False))
    print(f"\nend of stream 1: {end1}; start of stream 2: {start2}")
    print(f"within-stream monotonic: {monotonic}")
    if end1 == 0:
        print("INCONCLUSIVE: no errors occurred, so a reset cannot be observed.")
    elif start2 < end1:
        print("RESET PER STREAM: counts restart at zero with each stream (a counter that resets).")
    else:
        print("CUMULATIVE ACROSS STREAMS: counts carry over from one stream to the next.")


if __name__ == "__main__":
    main()
