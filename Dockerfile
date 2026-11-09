# Image commune backend (daphne) et worker (celery)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home app && mkdir /beat && chown -R app /app /beat
USER app

EXPOSE 8000
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
