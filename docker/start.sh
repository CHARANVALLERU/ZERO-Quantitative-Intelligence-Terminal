#!/bin/bash
set -e

# Start backend
echo "Starting backend..."
cd /app/backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Wait for backend to be ready
echo "Waiting for backend..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "Backend is ready!"
        break
    fi
    sleep 1
done

# Start frontend (served by FastAPI)
echo "Starting frontend..."
cd /app

# Use uvicorn to serve frontend static files
python -c "
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

frontend_build = '/app/frontend/out'

async def serve_frontend(app):
    app.mount('/static', StaticFiles(directory=os.path.join(frontend_build, 'static')), name='static')
    
    @app.get('/{full_path:path}')
    async def serve(frontend_build=frontend_build, full_path: str = ''):
        import os
        file_path = os.path.join(frontend_build, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(frontend_build, 'index.html'))
" &
FRONTEND_PID=$!

echo "ZERO Terminal started!"
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8000"
echo "WebSocket: ws://localhost:8000/ws/market-data"

# Wait for processes
echo "Running with PID $BACKEND_PID (backend) and $FRONTEND_PID (frontend)"
wait
