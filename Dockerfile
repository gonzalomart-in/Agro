# Imagen del bot (Telegram + Whisper). El modelo de lenguaje corre aparte, en el contenedor de Ollama.
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# corre sin permisos de administrador; el modelo de Whisper se guarda en /cache (un volumen),
# así no se vuelve a descargar cada vez que se reinicia
RUN useradd --create-home bot && mkdir -p /cache && chown bot /cache
USER bot
ENV HF_HOME=/cache PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.bot"]
