FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY pulse ./pulse
# Создайте его из targets.yml.example перед сборкой.
COPY targets.yml ./

RUN pip install --no-cache-dir .

# На Railway смонтируйте volume в /app/data и задайте
# PULSE_DB_PATH=/app/data/pulse.db, иначе состояние инцидентов
# обнуляется при каждом деплое и алерты приходят повторно.
VOLUME ["/app/data"]

CMD ["pulse"]
