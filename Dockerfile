FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir google-auth==2.38.0 requests==2.32.3

COPY keepalive.py slots.json /app/

ENV SLOTS_FILE=/app/slots.json
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/keepalive.py"]
