#!/bin/bash
echo "[*] Starting FastAPI Vehicle API on Linux..."
# Load environment variables if .env exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Run uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
