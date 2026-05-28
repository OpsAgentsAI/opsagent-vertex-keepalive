FROM google/cloud-sdk:alpine
RUN apk add --no-cache python3
WORKDIR /app
COPY probe.py /app/probe.py
ENTRYPOINT ["python3", "/app/probe.py"]
