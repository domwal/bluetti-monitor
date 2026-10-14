# Precisa rodar num host Linux com Bluetooth (BlueZ) — ver docker-compose.yml
# e a seção "Docker" do README para o motivo (sem passthrough de BLE no
# Docker Desktop para Windows/Mac).
FROM python:3.11-slim

# build-essential/libffi-dev/libssl-dev: em algumas arquiteturas (ex. ARM de
# Raspberry Pi) o pip pode não achar wheel pré-compilado pra cryptography/
# crcmod e precisar compilar na hora.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

WORKDIR /app/backend
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
