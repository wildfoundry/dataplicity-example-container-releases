# syntax=docker/dockerfile:1
FROM python:3.12-slim
WORKDIR /opt/equipment-gateway
COPY pyproject.toml ./
COPY equipment_gateway ./equipment_gateway
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir .; else pip install --no-cache-dir .; fi \
    && groupadd --gid 65532 equipment && useradd --uid 65532 --gid 65532 --create-home equipment
USER 65532:65532
ENTRYPOINT ["equipment-gateway"]
