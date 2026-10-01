# hdhomerun-exporter

A Prometheus exporter for [SiliconDust HDHomeRun](https://www.silicondust.com/) network TV tuners, with an example Grafana dashboard.

It reads each tuner over the device's own TCP control protocol (the interface `hdhomerun_config` uses). That covers signal, reception errors, bitrates, and who is watching which channel. The error counters are only exposed there; the HTTP JSON API doesn't have them.

![HDHomeRun dashboard](docs/dashboard.png)

## Run it

```bash
docker run -d --name hdhomerun-exporter -p 9137:9137 \
  -e HDHOMERUN_TARGETS=192.168.1.50 \
  ghcr.io/jrytio/hdhomerun-exporter:latest
```

Replace `192.168.1.50` with your tuner's IP (shown in the HDHomeRun app, or at `http://<tuner>/discover.json`). Check it with `curl localhost:9137/metrics`.

The image is multi-arch (amd64 and arm64), so it also runs on a Raspberry Pi.

## Scrape it

```yaml
# prometheus.yml
scrape_configs:
  - job_name: hdhomerun
    static_configs:
      - targets: ["hdhomerun-exporter:9137"]
```

## Dashboard

In Grafana, go to **Dashboards → New → Import** and upload [`dashboards/hdhomerun.json`](dashboards/hdhomerun.json). Pick your Prometheus datasource when prompted.

## Configuration

| Env | Default | |
|---|---|---|
| `HDHOMERUN_TARGETS` | required | Comma-separated `ip[:port][=name]`, e.g. `192.168.1.50=living-room,192.168.1.51=den`. `name` becomes the `device_id` label and defaults to the IP. |
| `LISTEN_PORT` | `9137` | |
| `TIMEOUT_SECONDS` | `3` | Time budget for reading one device |
| `CACHE_SECONDS` | `1` | Reuse a device read for this long. Concurrent requests always share one read. |

Targets must be IPv4 addresses; hostnames and IPv6 aren't accepted. A bad setting stops the exporter at startup with a message naming it.

The exporter serves `/metrics`, and `/healthz` for container health checks. `/healthz` answers without contacting any tuner.

## Metrics

Every device series carries `device_id` and `target` (the `ip:port` it was read from), and per-tuner series also carry `tuner`.

| Metric | What |
|---|---|
| `hdhomerun_up` | 1 if the device was read successfully |
| `hdhomerun_info{hwmodel,model,firmware}` | Device identity |
| `hdhomerun_tuners` | Number of tuners |
| `hdhomerun_ethernet_link_info{link}` | Ethernet link, e.g. `100f` = 100 Mbit full duplex |
| `hdhomerun_tuner_in_use` / `_locked` | Channel set / signal locked |
| `hdhomerun_tuner_signal_strength_percent` / `_signal_quality_percent` / `_symbol_quality_percent` | Reception, for tuners in use. Symbol quality below 100 means viewers see glitches. |
| `hdhomerun_tuner_errors_total{kind}` | Error counters (`transport`, `crc`, `resync`, `overflow`, `network`). They restart at zero with each new stream, so use `rate()` or `increase()`. |
| `hdhomerun_tuner_bitrate_bits_per_second{stage}` / `_network_packets_per_second` | Throughput, for tuners in use |
| `hdhomerun_tuner_channel_info{vchannel,name,frequency_hz,modulation,client}` | What a locked tuner is showing, and to whom |
| `hdhomerun_scrape_duration_seconds` | Time spent reading the device |
| `hdhomerun_exporter_build_info{version}` | Exporter version |

`client` is the viewer's IP address, so `/metrics` amounts to a viewing history. Expose it accordingly.

## Alerts

Two rules to start from:

```yaml
groups:
  - name: hdhomerun
    rules:
      - alert: HDHomeRunDown
        expr: hdhomerun_up == 0
        for: 5m
      - alert: HDHomeRunPoorReception
        # Only tuners in use have this series, so idle tuners never fire it.
        expr: hdhomerun_tuner_symbol_quality_percent < 100
        for: 5m
```

## Compatibility

Tested on an HDHomeRun CONNECT (HDHR4-2US) with firmware 20260313. Other models speak the same control protocol and should work, but haven't been tried. Only the tuner status (channel, lock and the three signal readings) is required; bitrate or error fields a model doesn't report are left out. CableCARD and ATSC 3.0 details aren't exported.

If your model shows up as down, please open an issue with the output of `hdhomerun_config <device> get /tuner0/debug`.

## Development

```bash
uv sync && uv run pytest && uv run ruff check && uv run ruff format --check
```

The tests replay bytes captured from a real HDHR4-2US. `tools/counter_check.py` is the live test that established the error-counter behaviour.

## License

[MIT](LICENSE)
