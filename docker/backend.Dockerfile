# syntax=docker/dockerfile:1
ARG GITHUB_REPO=Amancs1541/AccessPiliot
ARG GIT_REF=main

# ---- Stage 1: fetch the code from GitHub -------------------------------------------------------------
FROM alpine/git:latest AS source
ARG GITHUB_REPO
ARG GIT_REF
# Re-fetched whenever the branch/tag/sha moves, so a new push invalidates the cached clone below.
ADD https://api.github.com/repos/${GITHUB_REPO}/commits/${GIT_REF} /tmp/ref.json
RUN git clone "https://github.com/${GITHUB_REPO}.git" /src \
 && cd /src && git checkout "${GIT_REF}"

# ---- Stage 2: runtime (Python 3.9 is the tested version) ---------------------------------------------
FROM python:3.9-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY --from=source /src/backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY --from=source /src/backend/ ./
COPY entrypoint.sh /entrypoint.sh

# uid 10001 matches the `init` service's chown of the /data volume.
# /app/.env -> /data/.env so secrets the app writes at runtime persist across restarts.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /data && chown appuser /data \
 && ln -s /data/.env /app/.env

USER appuser
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
