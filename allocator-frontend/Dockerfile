FROM node:20-bookworm-slim AS build
WORKDIR /app

ARG APP_VERSION=local-dev
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV VITE_APP_VERSION=${APP_VERSION}
ENV VITE_GIT_SHA=${GIT_SHA}
ENV VITE_BUILD_TIME=${BUILD_TIME}

COPY package.json vite.config.js index.html ./
COPY src ./src

RUN npm install
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
