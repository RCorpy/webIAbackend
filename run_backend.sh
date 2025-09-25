#!/bin/bash

# Navigate to the backend folder
cd "$(dirname "$0")/backend" || exit 1

# Run uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
