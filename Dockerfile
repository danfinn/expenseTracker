# Dockerfile
FROM python:3.11

# Install system dependencies for postgresql-client (for the healthcheck)
# and libgl1 (for EasyOCR)
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client libgl1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]

