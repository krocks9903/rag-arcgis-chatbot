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
# Admin pages are included from frontend-react/public/ during the Vite build.

FROM nginx:alpine
COPY --from=build /web/dist /usr/share/nginx/html
# Prefer real files (admin.html, assets) over the SPA fallback.
RUN printf '%s\n' \
  'server {' \
  '  listen 80;' \
  '  root /usr/share/nginx/html;' \
  '  index index.html;' \
  '  location = /admin.html { try_files $uri =404; }' \
  '  location / {' \
  '    try_files $uri $uri/ /index.html;' \
  '  }' \
  '}' > /etc/nginx/conf.d/default.conf
EXPOSE 80
