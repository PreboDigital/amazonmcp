# Combined build: frontend + backend. Serves both from same origin (no CORS).
# Use this for the backend service. Set Railway Root Directory to empty (repo root).

# Stage 1: Build frontend
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Backend with frontend static files
FROM python:3.13-slim
WORKDIR /app

# Copy backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .

# Copy frontend build from stage 1
COPY --from=frontend /app/frontend/dist ./static

EXPOSE 8000
CMD ["sh", "start.sh"]
