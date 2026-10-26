FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/alembic.ini .
COPY backend/alembic ./alembic
COPY web/app ./web_app
ENV WEB_DIR=/app/web_app
EXPOSE 8000
# Apply migrations, then serve. --proxy-headers makes rate limiting see the real client IP behind the host's proxy.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
