FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose application port
EXPOSE 8000

# Start Intent Hunter CDP Master Process (Bot + Scraper + API)
CMD ["python", "-m", "src.main"]
