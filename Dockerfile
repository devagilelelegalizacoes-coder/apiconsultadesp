# Use Microsoft Playwright image as base (contains browsers and dependencies)
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Set the working directory
WORKDIR /app

# Install system dependencies needed for compiling Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install -r requirements.txt

# Install only Chromium for Playwright (fast and standard for scraping)
RUN playwright install chromium

# Copy the rest of the application code
COPY . .

# Expose the API port
EXPOSE 80

# Metadata (Optional but useful for Portainer/Dashboard)
LABEL version="1.0.0" \
      description="CONSULTA FACIL VEICULAR DESPACHANTE 2.0 API"

# Command to run the application
# We use --workers 1 since scrapers are heavy and we manage concurrency internally
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80", "--workers", "1"]
