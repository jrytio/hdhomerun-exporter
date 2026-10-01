import io
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tomllib

import pytest
from fakes import FakeDevice
from fixtures import DEVICE_VALUES

from hdhomerun_exporter.__main__ import build_app, build_registry, parse_port


def call(app, path: str) -> tuple[str, bytes]:
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "test",
        "SERVER_PORT": "9137",
        "wsgi.input": io.BytesIO(),
        "wsgi.url_scheme": "http",
    }
    status = []
    body = b"".join(app(environ, lambda s, headers, exc_info=None: status.append(s)))
    return status[0], body


@pytest.fixture
def device():
    d = FakeDevice(DEVICE_VALUES)
    yield d
    d.close()


def test_metrics_endpoint_scrapes_the_device(device):
    app = build_app(build_registry(f"127.0.0.1:{device.port}=1234ABCD", 1.0))
    status, body = call(app, "/metrics")
    assert status.startswith("200")
    assert b'hdhomerun_up{device_id="1234ABCD"' in body
    assert b"process_" in body or b"python_info" in body  # exporter self-metrics


def test_build_info_reports_the_package_version(device):
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text())["project"]["version"]
    registry = build_registry(f"127.0.0.1:{device.port}", 1.0)
    assert registry.get_sample_value("hdhomerun_exporter_build_info", {"version": version}) == 1


def test_healthz_never_touches_the_device(device):
    app = build_app(build_registry(f"127.0.0.1:{device.port}=1234ABCD", 1.0))
    status, body = call(app, "/healthz")
    assert status.startswith("200") and body == b"ok\n"
    assert device.requests == []


def test_other_paths_404(device):
    app = build_app(build_registry(f"127.0.0.1:{device.port}", 1.0))
    status, _ = call(app, "/probe")
    assert status.startswith("404")


def test_bad_targets_fail_at_startup():
    with pytest.raises(ValueError):
        build_registry("", 1.0)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_bad_timeout_fails_at_startup(timeout):
    with pytest.raises(ValueError, match="TIMEOUT_SECONDS"):
        build_registry("10.0.0.1", timeout)


@pytest.mark.parametrize("value", ["0", "65536", "-1", "http"])
def test_bad_listen_port_fails_at_startup(value):
    with pytest.raises(ValueError, match="LISTEN_PORT"):
        parse_port(value)


def test_listen_port():
    assert parse_port("9137") == 9137


@pytest.mark.parametrize("cache", [-1, float("nan"), float("inf")])
def test_bad_cache_seconds_fails_at_startup(cache):
    with pytest.raises(ValueError, match="CACHE_SECONDS"):
        build_registry("10.0.0.1", 1.0, cache)


def test_sigterm_stops_the_exporter_cleanly():
    # In a container the exporter is PID 1, where an unhandled SIGTERM is ignored and
    # `docker stop` ends in SIGKILL. Handled, it exits 0 here and there.
    with socket.create_server(("127.0.0.1", 0)) as s:
        port = s.getsockname()[1]
    env = {**os.environ, "HDHOMERUN_TARGETS": "127.0.0.1", "LISTEN_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "hdhomerun_exporter"], env=env, stderr=subprocess.PIPE, text=True
    )
    try:
        assert "listening" in proc.stderr.readline()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 0
    finally:
        proc.kill()
        proc.stderr.close()


@pytest.mark.parametrize("name", ["TIMEOUT_SECONDS", "CACHE_SECONDS"])
def test_non_numeric_setting_is_named_in_the_startup_error(name):
    env = {**os.environ, "HDHOMERUN_TARGETS": "127.0.0.1", name: "abc"}
    result = subprocess.run(
        [sys.executable, "-m", "hdhomerun_exporter"], env=env, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert f"{name}='abc' is not a number" in result.stderr
