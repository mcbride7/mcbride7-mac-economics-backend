FROM python:3.12-slim

# Run as a non-root user — the earlier Dockerfile in DEPLOY.md ran as root,
# which is fine for a quick local test but not for anything internet-facing.
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
