FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_DB_PATH=/data/assistant_data.sqlite3 \
    HF_HOME=/data/hf \
    FASTEMBED_CACHE_PATH=/data/fastembed

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY assistant_ai ./assistant_ai
COPY web ./web
COPY certs ./certs
COPY auth.py fastapi_server.py voice_stt.py voice_tts.py ./

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8765

# Один процесс: SQLite и блокировки пользователей живут в памяти процесса.
CMD ["uvicorn", "fastapi_server:app", "--host", "0.0.0.0", "--port", "8765", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
