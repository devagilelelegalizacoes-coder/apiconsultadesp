#!/bin/bash
echo "[*] Starting Background Worker on Linux..."
# Load environment variables if .env exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Run the worker module
python3 -m app.core.worker
