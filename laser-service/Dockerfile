# Multi-stage build for Laser Service API
FROM python:3.11-slim as builder

# Set build environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create and set the working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim as production

# Set runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Default environment variables (can be overridden)
ENV API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LOG_LEVEL=info \
    DATABASE_API_URL=https://api.navitrak.ai \
    LASER_BRIDGE_HOST=localhost \
    LASER_PORTS=2030 \
    LASER_API_LASER_TIMEOUT=1.0 \
    LASER_API_HEARTBEAT_INTERVAL=5.0 \
    LASER_API_CONNECTION_RETRY_INTERVAL=10.0 \
    LASER_API_MAX_CONNECTION_RETRIES=3 \
    LASER_API_LOG_LEVEL=INFO

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Create app user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Create and set the working directory
WORKDIR /app

# Copy Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY . .

# Create necessary directories and set permissions
RUN mkdir -p /app/logs && \
    chown -R appuser:appuser /app

# Switch to app user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${API_PORT}/api/v1/health || exit 1

# Expose port (configurable via API_PORT env var)
EXPOSE ${API_PORT}

# Default command
CMD ["python", "main.py"]