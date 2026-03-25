# Changes from original:
# - Multi-stage build: deps installed in builder, only runtime files copied to final image
# - Non-root user (uid 1001) — avoids running as root inside the container
# - Pinned patch version (3.11.12) instead of floating 3.11-slim tag
# - Layer caching: requirements.txt copied and installed before app code
# - HEALTHCHECK instruction so Docker/K8s can detect a hung process independently

# ── Stage 1: install dependencies ─────────────────────────────────────────────
FROM python:3.11.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 2: minimal runtime image ────────────────────────────────────────────
FROM python:3.11.12-slim

RUN useradd -r -u 1001 -s /sbin/nologin appuser

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /root/.local /home/appuser/.local
COPY app.py .

RUN chown -R appuser:appuser /app

USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
