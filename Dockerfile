FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service

RUN groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid bridge --create-home bridge

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data /config \
    && chown -R bridge:bridge /service /data /config

USER bridge

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

