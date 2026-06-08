# Stage 1 — Build the React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2 — Python backend runtime
FROM python:3.12-slim
WORKDIR /app

RUN pip install uv --no-cache-dir

# Install Python dependencies first (layer-cached unless pyproject.toml/uv.lock change)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-group dev --no-install-project

# Copy backend source
COPY backend/ ./

# Embed the frontend build so FastAPI can serve the SPA
COPY --from=frontend-build /app/frontend/dist ./pullbox/static/

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8585
CMD ["uvicorn", "pullbox.main:app", "--host", "0.0.0.0", "--port", "8585"]
