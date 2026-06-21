# Use official lightweight Python image
FROM python:3.11-slim

# Metadata labels
LABEL maintainer="EcoTrack Team"
LABEL description="EcoTrack — AI-powered Carbon Footprint Awareness Platform"
LABEL version="1.0.0"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install dependencies (cached layer — only re-runs when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create data directory for JSON storage
RUN mkdir -p data

# Expose port (default PORT is 8000; Cloud Run overrides via $PORT)
EXPOSE 8000

# Healthcheck — polls the /api/health endpoint every 30 seconds
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/api/health')" || exit 1

# Command to run on start
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
