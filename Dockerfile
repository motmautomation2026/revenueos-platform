# RevenueOS web app (Next.js). Built on the VPS by docker-compose.yml.
#
# Debian (not Alpine): the free OCR uses @napi-rs/canvas, a native module built for glibc.
# The full node_modules is kept (not Next's trimmed "standalone" output): tesseract.js starts a
# worker from a file path, and the agents read prompts/, schemas/ and the OCR language data from
# disk at run time (process.cwd()), which a trimmed build can miss.

FROM node:24-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Public values only: they are compiled into the browser code. Secrets come at run time (.env).
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
RUN npm run build


FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"

CMD ["npx", "next", "start", "-p", "3000"]
