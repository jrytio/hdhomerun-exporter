"""Entry point: config from env, WSGI app serving /metrics and /healthz."""

from __future__ import annotations

import logging
import math
import os
import signal
import sys
from collections.abc import Callable, Iterable
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import CollectorRegistry, make_wsgi_app
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from .collector import HDHomeRunCollector, parse_targets

log = logging.getLogger("hdhomerun_exporter")

WSGIApp = Callable[[dict, Callable], Iterable[bytes]]


def build_app(registry: CollectorRegistry) -> WSGIApp:
    metrics = make_wsgi_app(registry)

    def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if path == "/metrics":
            return metrics(environ, start_response)
        if path == "/healthz":
            # Never touches a device: kubelet probes must add no device load.
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok\n"]
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found\n"]

    return app


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise ValueError(f"LISTEN_PORT={value!r} is not an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"LISTEN_PORT={port} is outside 1-65535")
    return port


def env_seconds(name: str, default: str) -> float:
    value = os.environ.get(name, default)
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name}={value!r} is not a number") from None


def build_registry(
    targets_spec: str, timeout: float, cache_seconds: float = 1.0
) -> CollectorRegistry:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"TIMEOUT_SECONDS={timeout} must be a finite number above 0")
    if not math.isfinite(cache_seconds) or cache_seconds < 0:
        raise ValueError(f"CACHE_SECONDS={cache_seconds} must be a finite number, 0 or above")
    registry = CollectorRegistry()
    registry.register(HDHomeRunCollector(parse_targets(targets_spec), timeout, cache_seconds))
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    return registry


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        registry = build_registry(
            os.environ.get("HDHOMERUN_TARGETS", ""),
            env_seconds("TIMEOUT_SECONDS", "3"),
            env_seconds("CACHE_SECONDS", "1"),
        )
        port = parse_port(os.environ.get("LISTEN_PORT", "9137"))
    except ValueError as exc:
        sys.exit(f"hdhomerun-exporter: bad configuration: {exc}")
    # As PID 1 in a container an unhandled SIGTERM is ignored, so `docker stop` would
    # wait out its grace period and then kill the process.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    with make_server(
        "", port, build_app(registry), _ThreadingWSGIServer, handler_class=_QuietHandler
    ) as server:
        log.info("listening on :%d", port)
        server.serve_forever()


if __name__ == "__main__":
    main()
