# Local docker-compose web image: React SPA + admin pages.
# Build from repo root:
#   docker build -f frontend-react/Dockerfile -t engage-estero-web .
FROM node:22-alpine AS build
WORKDIR /web
COPY frontend-react/package.json frontend-react/package-lock.json ./
RUN npm ci
COPY frontend-react/ ./
ARG VITE_API_BASE=http://localhost:8080
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build
COPY frontend/admin.html frontend/admin.js frontend/admin.css frontend/styles.css frontend/config.js ./dist/
COPY frontend/assets ./dist/assets

FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
# SPA fallback: unknown paths → index.html (API is on a different host/port).
RUN printf '%s\n' \
  'server {' \
  '  listen 80;' \
  '  root /usr/share/nginx/html;' \
  '  index index.html;' \
  '  location / {' \
  '    try_files $uri $uri/ /index.html;' \
  '  }' \
  '}' > /etc/nginx/conf.d/default.conf
EXPOSE 80
