# Use official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create data directory for JSON storage
RUN mkdir -p data

# Expose port (Cloud Run sets PORT automatically)
EXPOSE 8080

# Command to run on start
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
