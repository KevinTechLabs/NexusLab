FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py brain.py ./
COPY static ./static
EXPOSE 8484
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/healthz', timeout=4)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8484"]
