# The public OSWorld controller: upstream OSWorld's controllers, getters and metrics at OSWORLD_COMMIT (fetch-osworld.sh),
# run unmodified behind a bearer token (server.py) against a computer's osworld-server app, which a forwarder on the
# guest server's loopback port reaches with the computer's headers (forwarder.py). No LLM judge is built in.
FROM mirror.gcr.io/library/python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf
ENV OSWORLD_COMMIT=5123bfdbbc34b947bcffcff564abc62d0ab7072d \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/osworld:/app/stubs
# opencv's libglib, ffmpeg for librosa's audio and fpcalc (chromaprint) for pyacoustid; curl for the source.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates libglib2.0-0 ffmpeg libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY fetch-osworld.sh requirements.txt ./
RUN sh fetch-osworld.sh /opt/osworld \
    && pip install --no-cache-dir --require-hashes -r requirements.txt \
    && useradd --create-home controller \
    && mkdir /tmp/osworld-cache && chown controller /tmp/osworld-cache
COPY server.py forwarder.py runtime.py serve.py ./
COPY stubs/ stubs/
USER controller
EXPOSE 8000
CMD ["python", "serve.py"]
