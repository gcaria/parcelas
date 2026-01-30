FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*


COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ ./api/

# Set Python path
ENV PYTHONPATH=/app

# Run on port 8080 (Cloud Run requirement)
ENV PORT=8080

# Run with uvicorn
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
