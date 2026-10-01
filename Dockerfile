# syntax=docker/dockerfile:1
# Bases pinned by digest; bump both lines together.
FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# README.md and LICENSE are named in pyproject.toml, so the package build needs them.
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
LABEL org.opencontainers.image.source="https://github.com/jrytio/hdhomerun-exporter" \
      org.opencontainers.image.description="Prometheus exporter for SiliconDust HDHomeRun tuners" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=build /app/.venv /app/.venv
USER 65534:65534
EXPOSE 9137
ENTRYPOINT ["/app/.venv/bin/python", "-m", "hdhomerun_exporter"]
