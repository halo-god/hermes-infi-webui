#!/bin/bash
cd ~/Downloads/hermes-python/backend
source .venv/bin/activate
export PYTHONUNBUFFERED=1
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info 2>&1 | tee /tmp/hermes-api.log
