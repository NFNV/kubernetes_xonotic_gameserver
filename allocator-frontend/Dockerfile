FROM node:20-bookworm-slim AS build
WORKDIR /app

COPY package.json vite.config.js index.html ./
COPY src ./src

RUN npm install
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
