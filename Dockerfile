# Alliance // Automated Game Master (Single-Container Specification)
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV DB_PATH=/data/alliance.db
ENV UPLOADS_DIR=/data/uploads
ENV KEYS_DIR=/data/keys
ENV TIMEZONE=Europe/Moscow
ENV TZ=Europe/Moscow

# Install supervisord and lightweight native libraries for Pillow / cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    curl \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user and group (UID 1000 / GID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m -s /bin/sh appuser

WORKDIR /app

# Install Python production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and supervisor configuration
COPY app/ /app/app/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Prepare persistent mount and log directories, set ownership to user 1000
RUN mkdir -p /data/uploads /data/keys /var/log/supervisor && \
    chown -R 1000:1000 /app /data /var/log/supervisor

# Expose ASGI container port
EXPOSE 8000

# Persistent Docker volume mount
VOLUME ["/data"]

# Run container as non-root user 1000:1000
USER 1000:1000

# Start process supervisor managing Uvicorn and Scheduler
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
