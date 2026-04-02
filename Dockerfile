FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --system --no-log-init --shell /usr/sbin/nologin openpms

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY pytest.ini .
COPY tests ./tests
COPY scripts ./scripts

RUN chown -R openpms:openpms /app

USER openpms

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
