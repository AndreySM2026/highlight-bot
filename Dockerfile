FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/temp

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

# Healthcheck задаётся в панели Timeweb: путь /health (не добавлять HEALTHCHECK в Dockerfile).
CMD ["python", "-u", "run.py"]
