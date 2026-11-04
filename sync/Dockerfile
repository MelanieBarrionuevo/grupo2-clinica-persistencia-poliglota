# El sincronizador reutiliza los clientes y la construcción de la proyección de la API.
# Contexto de construcción: raíz del repositorio (ver docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY sync/worker.py ./worker.py

RUN useradd --create-home --uid 10001 clinica
USER clinica

CMD ["python", "worker.py"]
