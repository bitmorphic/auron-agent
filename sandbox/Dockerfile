# AURON Sandbox Image
# Pinned base, preinstalled analysis packages, non-root user.
# No runtime package installation permitted.

FROM python:3.12-slim

# Install analysis packages — pinned for reproducibility
RUN pip install --no-cache-dir \
    pandas==2.2.3 \
    numpy==2.1.3 \
    matplotlib==3.9.2

# Create non-root user (UID/GID 10001)
RUN groupadd -g 10001 auron && \
    useradd -u 10001 -g 10001 -s /usr/sbin/nologin -M -d /nonexistent auron

# Create required directories with correct ownership
RUN mkdir -p /data /app /out /tmp/mpl && \
    chown 10001:10001 /out /tmp /tmp/mpl

WORKDIR /app

USER 10001:10001

CMD ["python", "/app/code.py"]

