# Railway Deployment Guide — Amazon Ads Optimizer

## Services to Create

| Service | Type | Purpose |
|---------|------|---------|
| **Web** | Backend | FastAPI API (campaigns, reports, sync, AI) |
| **PostgreSQL** | Database | Railway plugin — stores credentials, campaigns, reports |
| **Frontend** | Static (optional) | Build Vite app and serve from backend, or deploy separately |

### Recommended: Single Web Service + PostgreSQL

1. **Add PostgreSQL** — Railway Dashboard → New → Database → PostgreSQL
2. **Add Web Service** — New → GitHub Repo → `PreboDigital/amazonmcp`
3. **Root Directory**: `backend` (so Railway builds the Python app)
4. **Build**: Auto-detected from `requirements.txt` + `nixpacks.toml`

---

## Environment Variables (Railway → Variables)

### Required

```env
ENVIRONMENT=production
DATABASE_URL=${{Postgres.DATABASE_URL}}   # Auto-linked if you add Postgres plugin
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
API_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
ENCRYPTION_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
CORS_ORIGINS=https://your-frontend.railway.app,https://your-domain.com
```

### Optional (AI, PA-API, Email)

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
PAAPI_ACCESS_KEY=
PAAPI_SECRET_KEY=
PAAPI_PARTNER_TAG=
```

### Resend (Email)

```env
RESEND_API_KEY=re_xxx
FROM_EMAIL=no-reply@prebodigital.co.za
```

### Upstash Redis

```env
UPSTASH_REDIS_REST_URL=https://xxx.upstash.io
UPSTASH_REDIS_REST_TOKEN=xxx
```

### Cron (for QStash scheduled jobs)

```env
CRON_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(24))">
```

---

## Auto-Fetch Schedule (Upstash QStash)

Use [Upstash QStash](https://upstash.com/docs/qstash) to schedule HTTP calls to your cron endpoints.

### 1. Get QStash Credentials

- Go to [Upstash Console](https://console.upstash.com/) → QStash
- Copy `QSTASH_TOKEN` (for creating schedules)

### 2. Create Schedules

Replace `https://your-app.railway.app` with your Railway public URL.

**Campaign sync** (every 6 hours):

```bash
curl -X POST "https://qstash.upstash.io/v2/schedules" \
  -H "Authorization: Bearer YOUR_QSTASH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "https://your-app.railway.app/api/cron/sync",
    "cron": "0 */6 * * *",
    "body": "{}",
    "headers": {
      "Content-Type": "application/json",
      "X-Cron-Secret": "YOUR_CRON_SECRET"
    }
  }'
```

**Reports** (daily at 6:00 UTC):

```bash
curl -X POST "https://qstash.upstash.io/v2/schedules" \
  -H "Authorization: Bearer YOUR_QSTASH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "https://your-app.railway.app/api/cron/reports",
    "cron": "0 6 * * *",
    "body": "{}",
    "headers": {
      "Content-Type": "application/json",
      "X-Cron-Secret": "YOUR_CRON_SECRET"
    }
  }'
```

**Search terms** (daily at 7:00 UTC):

```bash
curl -X POST "https://qstash.upstash.io/v2/schedules" \
  -H "Authorization: Bearer YOUR_QSTASH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "https://your-app.railway.app/api/cron/search-terms",
    "cron": "0 7 * * *",
    "body": "{}",
    "headers": {
      "Content-Type": "application/json",
      "X-Cron-Secret": "YOUR_CRON_SECRET"
    }
  }'
```

### Cron Expressions

| Schedule | Cron | Description |
|----------|------|-------------|
| Every 6 hours | `0 */6 * * *` | 00:00, 06:00, 12:00, 18:00 UTC |
| Daily 6am UTC | `0 6 * * *` | Once per day |
| Every 12 hours | `0 */12 * * *` | 00:00, 12:00 UTC |

---

## Build Configuration

**Root Directory**: `backend`

Railway will use `backend/nixpacks.toml` and `backend/Procfile` to build and run. No extra config needed.

**Frontend**: Deploy separately to Vercel, Netlify, or Railway static. Set `VITE_API_BASE_URL=https://your-backend.railway.app/api`.

---

## Frontend Build & Serve from Backend (Optional)

To serve the frontend from the same service:

1. Add static file serving in `app/main.py`:

```python
from fastapi.staticfiles import StaticFiles
# After all routers:
app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="static")
```

2. Build frontend during deploy and set `VITE_API_BASE_URL` to `/api` (relative).

---

## Summary Checklist

- [ ] Add PostgreSQL plugin
- [ ] Add Web service from GitHub
- [ ] Set all required env vars (DATABASE_URL, SECRET_KEY, API_KEY, ENCRYPTION_KEY)
- [ ] Set CRON_SECRET for scheduled jobs
- [ ] Add Resend + Upstash Redis vars if using
- [ ] Create QStash schedules for sync, reports, search-terms
- [ ] Apply changes and deploy
