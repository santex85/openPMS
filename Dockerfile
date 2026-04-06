# Builder: install deps into a venv. Runtime image copies only app artifacts (no tests/pytest.ini).
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --system --no-log-init --shell /usr/sbin/nologin openpms

COPY --from=builder /app/alembic.ini .
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/app ./app
COPY --from=builder /app/scripts ./scripts

RUN chmod +x scripts/start.sh \
    && chown -R openpms:openpms /app

USER openpms

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"

CMD ["bash", "scripts/start.sh"]
