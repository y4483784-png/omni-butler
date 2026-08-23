# Build from repository root: docker compose build nginx
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx-scale.conf /etc/nginx/nginx-scale.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
