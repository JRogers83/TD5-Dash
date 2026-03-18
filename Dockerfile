# python:3.11-slim-bookworm matches the Python version shipped with Pi OS Bookworm,
# giving a close approximation of the production environment for local dev/test.
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install dependencies first (better layer caching)
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r ./backend/requirements.txt

# Copy source — volumes override these at runtime in docker-compose
COPY backend/  ./backend/
COPY frontend/ ./frontend/

EXPOSE 8000

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
