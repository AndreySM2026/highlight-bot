FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates fonts-dejavu-core \
    && curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
        -o /usr/local/bin/yt-dlp \
    && chmod a+x /usr/local/bin/yt-dlp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка моделей Whisper (base + tiny для длинных видео).
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); WhisperModel('tiny', device='cpu', compute_type='int8')"

COPY . .

RUN mkdir -p /app/data/temp

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

# Healthcheck задаётся в панели Timeweb: путь /health (не добавлять HEALTHCHECK в Dockerfile).
CMD ["python", "-u", "run.py"]
