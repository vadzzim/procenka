# Frontend static build served by nginx, which also proxies the API.
# Build context: repository root (see deploy/compose.yaml); files sent to the
# builder are limited by Dockerfile.dockerignore next to this file.

FROM node:24-alpine AS build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# tsc -b && vite build; TS types come from the committed src/api/schema.d.ts.
RUN npm run build

FROM nginx:1.29-alpine
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
