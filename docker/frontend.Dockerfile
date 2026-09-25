# syntax=docker/dockerfile:1
ARG GITHUB_REPO=Amancs1541/AccessPiliot
ARG GIT_REF=main

# ---- Stage 1: fetch the code from GitHub -------------------------------------------------------------
FROM alpine/git:latest AS source
ARG GITHUB_REPO
ARG GIT_REF
ADD https://api.github.com/repos/${GITHUB_REPO}/commits/${GIT_REF} /tmp/ref.json
RUN git clone "https://github.com/${GITHUB_REPO}.git" /src \
 && cd /src && git checkout "${GIT_REF}"

# ---- Stage 2: build the SPA --------------------------------------------------------------------------
FROM node:24-alpine AS build
WORKDIR /app
COPY --from=source /src/ ./
# The repo tracks (a) the maintainer's own tenant/client IDs in .env.local — removed so nobody else's build
# silently signs in against someone else's tenant (auth is configured from the dashboard setup flow), and
# (b) a Windows-built node_modules and an old dist/ — removed and rebuilt cleanly for Linux.
RUN rm -rf node_modules dist .env.local .env .env.production && npm ci
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ---- Stage 3: serve ----------------------------------------------------------------------------------
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
