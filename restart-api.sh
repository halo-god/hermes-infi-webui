#!/bin/bash
cd /home/hermes/hermes-infi-webui/backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --log-level info
