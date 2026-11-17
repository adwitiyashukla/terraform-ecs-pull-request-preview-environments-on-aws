FROM python:3.12-slim AS build

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/venv/bin:$PATH \
    PORT=8000

RUN useradd --system --uid 10001 --no-create-home app

COPY --from=build /venv /venv
WORKDIR /srv
COPY main.py .

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + os.environ.get('PATH_PREFIX', '') + '/healthz', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
