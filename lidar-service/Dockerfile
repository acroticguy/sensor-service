# Stage 1: Builder
FROM python:3.13-slim-bullseye AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install basic network related tools for debugging
RUN apt update && apt install -y iputils-ping iproute2 && rm -rf /var/lib/apt/lists/*

# Stage 2: Production
FROM python:3.13-slim-bullseye

WORKDIR /app

# Copy only necessary runtime dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy the application code
COPY . .

# Set PYTHONPATH to include the app directory
ENV PYTHONPATH=/app

# Expose the port FastAPI runs on
EXPOSE 8001

# Command to run the application
CMD uvicorn app.main:app --host 0.0.0.0 --port 8001