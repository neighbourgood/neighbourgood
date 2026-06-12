FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8300

# Keep-alive must outlive the Node proxy's client keep-alive (undici: ~4s),
# otherwise the proxy reuses sockets uvicorn has already closed → ECONNRESET.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300", "--timeout-keep-alive", "65"]
