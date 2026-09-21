FROM python:3.11-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system dependencies (cached)
RUN apt-get update && apt-get install -y --no-install-recommends \
    p7zip-full \
    unar \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Cache Python dependencies layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code (only this layer rebuilds on code changes!)
COPY . .

# Default command
CMD ["python", "run_bot.py"]
