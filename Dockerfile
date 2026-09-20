# ---------------------------------------------------------------
# One image that serves the screens and the API on a single port.
#
#   docker build -t securedocs .
#   docker run -p 5000:5000 --env-file backend/.env securedocs
#
# Two stages so the finished image does not carry the frontend build
# tools around with it.
# ---------------------------------------------------------------

# ---- stage 1: build the screens ----
FROM node:22-slim AS client

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the server ----
FROM node:22-slim

# Non-root. If the process is ever compromised it should not own the box.
ENV NODE_ENV=production
WORKDIR /app

COPY backend/package*.json ./backend/
RUN cd backend && npm ci --omit=dev

COPY backend/ ./backend/
COPY --from=client /app/frontend/dist ./frontend/dist

# Where Tesseract caches its language data. Without this it re-downloads
# roughly 15MB per language on every container start.
ENV OCR_CACHE_PATH=/app/backend/.tesseract
RUN mkdir -p /app/backend/.tesseract /app/backend/uploads \
    && chown -R node:node /app

USER node
EXPOSE 5000

# STORAGE_BACKEND must be minio in any real deployment. The container
# filesystem is wiped on every restart, so files written to disk here
# are gone the moment the host reschedules the app.
CMD ["node", "backend/server.js"]
