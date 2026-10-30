# Both runtimes are needed: Python controller + Node Claude adapters.
FROM node:22-bookworm-slim AS node-runtime
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && npm install --global pnpm@9.15.9
WORKDIR /app
COPY requirements-hosted.txt ./
RUN pip install --no-cache-dir -r requirements-hosted.txt
COPY backend/package.json backend/pnpm-lock.yaml ./backend/
RUN cd backend && pnpm install --prod --frozen-lockfile
COPY . .
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 STUDY_BUDDY_NODE=/usr/local/bin/node
EXPOSE 10000
CMD ["sh", "scripts/start_hosted_workbook.sh"]
