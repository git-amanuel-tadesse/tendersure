# The team server (spec 20): the web app built once, served by tendersure-api --serve.
# Client data is never in the image: the bank, learned files and exercises live on
# the persistent disk at $TENDERSURE_HOME (see render.yaml and .dockerignore).

# --- 1. the web app (apps/web/dist) ---
FROM node:22-slim AS web
# the lockfile is pnpm 8's (lockfileVersion 6.0)
RUN corepack enable && corepack prepare pnpm@8.15.9 --activate
WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/api-client/package.json packages/api-client/
RUN pnpm install --frozen-lockfile
COPY packages/api-client packages/api-client
COPY apps/web apps/web
RUN pnpm --filter @tendersure/web build

# --- 2. the Python parties, and the built web app beside them ---
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TENDERSURE_HOME=/app/home \
    TENDERSURE_WEB_DIST=/app/web
WORKDIR /app
COPY packages/core packages/core
COPY apps/api apps/api
COPY apps/cli apps/cli
# the CLI too: check, learn, evaluate and privacy-check run in Render's shell, on the same home
RUN pip install ./packages/core ./apps/api ./apps/cli
COPY --from=web /src/apps/web/dist /app/web
# Render SSH (and scp onto the disk) needs ~/.ssh for the running user
RUN mkdir -p /root/.ssh && chmod 0700 /root/.ssh && mkdir -p /app/home
EXPOSE 10000
CMD ["tendersure-api", "--serve"]
