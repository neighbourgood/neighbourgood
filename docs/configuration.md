# Configuration

NeighbourGood is configured with environment variables, all prefixed `NG_`. Put them in `.env` in the project root (start from [`.env.example`](../.env.example)); Docker Compose passes them to the backend. Restart the backend after changing them.

## Essentials

| Variable | Default | Notes |
|----------|---------|-------|
| `NG_SECRET_KEY` | none (the built-in default is rejected) | **Required in production.** Signs login tokens; at least 32 characters. Generate with `openssl rand -hex 32`. Changing it logs everyone out |
| `NG_DATABASE_URL` | `sqlite:///./neighbourgood.db` | Docker Compose sets a PostgreSQL URL; SQLite is for local development |
| `NG_DEBUG` | `false` | `true` only for local development: accepts the default secret key, relaxes HSTS, CSP and CSRF. Never in production |
| `NG_ADMIN_EMAILS` | `[]` | JSON array. Accounts with these emails become platform admins on their next request |
| `NG_FRONTEND_URL` | `http://localhost:3800` | Public URL of the web app, used in email links |
| `NG_CORS_ORIGINS` | `["http://localhost:3800","http://localhost:5173"]` | JSON array of allowed browser origins; set it to your public URL |
| `NG_IMAGE_TAG` | `latest` | Docker Compose only: which published image release to run, e.g. `2.10.0` to stay on one version |

## Crisis mode

| Variable | Default | Notes |
|----------|---------|-------|
| `NG_PLATFORM_MODE` | `blue` | `red` puts **every** community on the instance into Red Sky. Each community keeps its own setting and returns to it when the instance is set back to `blue`. Communities can also switch individually (admin toggle or 60 % vote) |

## Instance identity (federation)

Shown at `/instance/info` and in other instances' directories.

| Variable | Example |
|----------|---------|
| `NG_INSTANCE_NAME` | `My NeighbourGood` |
| `NG_INSTANCE_DESCRIPTION` | `A neighbourhood resource-sharing community` |
| `NG_INSTANCE_REGION` | `Berlin, Germany` |
| `NG_INSTANCE_URL` | `https://neighbourgood.example.com` |
| `NG_ADMIN_NAME` | Name of the person responsible for the instance |
| `NG_ADMIN_CONTACT` | Contact address for other instance admins |

## Email

Without SMTP settings, emails (booking notifications, password resets) are written to the backend log instead of being sent.

| Variable | Default |
|----------|---------|
| `NG_SMTP_HOST` | unset |
| `NG_SMTP_PORT` | `587` |
| `NG_SMTP_TLS` | `true` |
| `NG_SMTP_USER` | unset |
| `NG_SMTP_PASSWORD` | unset |
| `NG_SMTP_FROM` | `noreply@neighbourgood.local` |

## Telegram and AI

Details in [telegram-setup.md](telegram-setup.md).

| Variable | Default | Notes |
|----------|---------|-------|
| `NG_TELEGRAM_BOT_TOKEN` | unset | Bot API token from BotFather; the integration is off while unset |
| `NG_TELEGRAM_BOT_NAME` | unset | Bot username without `@` |
| `NG_TELEGRAM_WEBHOOK_SECRET` | derived | Derived from `NG_SECRET_KEY` and the bot token when unset; the webhook is always protected |
| `NG_AI_PROVIDER` | unset | `ollama` or `openai` (any OpenAI-compatible API). Unset = rule-based matching only |
| `NG_AI_BASE_URL` | `http://localhost:11434` | Ollama default |
| `NG_AI_MODEL` | `llama3.2` | |
| `NG_AI_API_KEY` | unset | Needed for OpenAI, not for Ollama |

## WhatsApp

Optional bot through an unofficial WhatsApp Web bridge (WAHA). Read [whatsapp-setup.md](whatsapp-setup.md) first; `bash install.sh --whatsapp` sets the first four for you.

| Variable | Default | Notes |
|----------|---------|-------|
| `NG_WHATSAPP_BRIDGE_URL` | unset | Bridge address, `http://waha:3000` in Docker Compose; the integration is off while unset |
| `NG_WHATSAPP_API_KEY` | unset | Sent to the bridge as `X-Api-Key`; Compose passes it to WAHA as `WAHA_API_KEY` |
| `NG_WHATSAPP_WEBHOOK_SECRET` | unset | HMAC key the bridge signs webhook calls with; unsigned calls are rejected |
| `COMPOSE_PROFILES` | unset | `whatsapp` starts the `waha` container (Docker Compose setting, not read by the app) |
| `NG_WHATSAPP_BOT_NUMBER` | read from the paired phone | International format without `+`, for `wa.me` links |
| `NG_WHATSAPP_SESSION` | `default` | WAHA session name |
| `NG_WHATSAPP_SEND_INTERVAL_MS` | `1500` | Minimum gap between outgoing messages (random up to twice this) |
| `NG_WHATSAPP_HOURLY_LIMIT_PER_CHAT` | `20` | Messages per chat and hour before the bot holds back |
| `NG_WAHA_IMAGE` | `devlikeapro/waha:latest-2026.9.2` | Docker Compose only: bridge image (use an `arm` tag on ARM servers) |

## Limits and safety

| Variable | Default | Notes |
|----------|---------|-------|
| `NG_ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` (24 h) | How long a login lasts |
| `NG_MAX_IMAGE_SIZE` | `5242880` (5 MB) | Largest image upload |
| `NG_MAX_REQUEST_BODY_BYTES` | `1048576` (1 MB) | Largest JSON request body (413 above) |
| `NG_UPLOAD_DIR` | `uploads` | Where resource images are stored |
| `NG_MESH_MAX_MESSAGE_AGE_HOURS` | `72` | Mesh messages older than this are refused on sync |
| `NG_WEBHOOK_ALLOW_PRIVATE` | `false` | Allow outbound webhooks to private/LAN addresses (turns the SSRF guard off) |

Built-in limits that are not configurable: 5 login/register attempts per minute per IP, 200 API requests per minute per IP, 10 uploads per minute per IP, and a 15-minute lockout after 5 failed logins for the same email and IP.

## Frontend (behind a reverse proxy)

`ORIGIN`, `ADDRESS_HEADER` and `XFF_DEPTH` are set on the `frontend` service, not in `.env`. See [self-hosting.md](self-hosting.md#running-behind-a-reverse-proxy-nginx--caddy).
