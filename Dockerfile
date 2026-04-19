# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a prefix
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code (no .env — secrets come via environment variables)
COPY . .

# Remove .env if accidentally included
RUN rm -f .env

# Create non-root user for security
RUN useradd -m -u 1000 agentuser && chown -R agentuser:agentuser /app
USER agentuser

# Expose dashboard port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Default: run the live dashboard server
# Override with: docker run ... python main.py  (CLI mode)
CMD ["python", "main.py", "--serve"]
