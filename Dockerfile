FROM python:3.11-slim

# ffprobe ffmpeg ke andar hota hai — alag install nahi karna
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway ka $PORT use karo
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 3600 --workers 1 app:app
