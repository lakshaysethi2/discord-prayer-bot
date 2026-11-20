FROM python:3.12-slim

WORKDIR /app

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA} \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root: the bot only writes ./data (SQLite) and /app/media.
RUN useradd -r -u 10001 app && mkdir -p /app/data /app/media && chown -R app /app
USER app

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8000"]
