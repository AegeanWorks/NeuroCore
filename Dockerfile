FROM python:3.12-slim

WORKDIR /app

# Runtime dependencies only (test tools live in requirements-dev.txt). The compiler is needed
# only while wheels build, so it is installed and purged in the same layer.
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove gcc \
    && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user. UID 1000 matches the usual first Linux user, so the bind-mounted
# ./data and ./modules stay writable; docker-compose.yml can override it per host.
RUN useradd --create-home --uid 1000 neurocore

COPY --chown=neurocore:neurocore . .
RUN mkdir -p data && chown neurocore:neurocore data

USER neurocore

EXPOSE 8000

# Bind all interfaces inside the container so a published port works. Remote exposure is
# bounded by the host publish mapping (loopback-only in docker-compose.yml). A bare
# `docker run` without NEUROCORE_API_TOKEN refuses to start rather than run unauthenticated.
ENV NEUROCORE_HOST=0.0.0.0

CMD ["python", "main.py"]
