FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Publishes dist/ into the shared volume on start, then exits successfully so the
# api service (which waits on service_completed_successfully) can serve it read-only.
FROM alpine:3.20
COPY --from=build /app/dist /dist
VOLUME /app/dist
CMD ["sh", "-c", "rm -rf /app/dist/* && cp -r /dist/. /app/dist/ && echo 'frontend assets published'"]
