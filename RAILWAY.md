# Railway Deployment Guide — Amazon Ads Optimizer

## Deployment URLs

| Service | URL |
|---------|-----|
| **Frontend** | https://amazonmcp-frontend-production.up.railway.app |
| **Backend** | https://amazonmcp-backend-production.up.railway.app (or your backend service URL) |

**Local development:**
- Frontend: http://localhost:5173 (Vite proxy → backend)
- Backend: http://localhost:8000

---

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
CORS_ORIGINS=http://localhost:5173,http://localhost:3000,https://amazonmcp-frontend-production.up.railway.app
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

**Frontend** (Root Directory: `frontend`): Set `VITE_API_BASE_URL=https://amazonmcp-backend-production.up.railway.app/api` in Railway Variables so the frontend calls the backend.

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

## Database Connection (Important)

1. **Use Railway's variable reference** for `DATABASE_URL`:
   ```env
   DATABASE_URL=${{Postgres.DATABASE_URL}}
   ```
   This auto-links when Postgres is in the same project. Railway provides `postgresql://`; the backend converts it to `postgresql+asyncpg://` for asyncpg.

2. **SSL**: Railway Postgres uses SSL. The backend automatically enables SSL when connecting to `*.rlwy.net` hosts.

3. **Fresh database = empty data**: Railway Postgres starts empty. After deploy:
   - Tables are created automatically on first startup (`init_db`)
   - **Credentials** must be re-added via Settings → Add Credentials
   - **AI API keys** (OpenAI, Anthropic) must be re-entered in Settings
   - **First admin**: Set `FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` to bootstrap the first user, or register via the app

4. **Verify connection**: Hit `GET /api/health` — it returns `"database": "connected"` if the DB is reachable.

5. **Healthcheck failures**: If deploy fails with "Healthcheck failed", check **Deploy logs** (not Build logs) for the actual error. Common causes:
   - Missing required vars: `ENVIRONMENT=production` requires `SECRET_KEY`, `API_KEY`, `ENCRYPTION_KEY`
   - Database connection timeout or SSL issues
   - Healthcheck timeout extended to 120s in `railway.toml`; you can also set `RAILWAY_HEALTHCHECK_TIMEOUT_SEC=300` in Railway Variables for slow cold starts

---

## Summary Checklist

- [ ] Add PostgreSQL plugin
- [ ] Add Backend service (Root: `backend`), Frontend service (Root: `frontend`)
- [ ] **Backend** vars: DATABASE_URL (use `${{Postgres.DATABASE_URL}}`), SECRET_KEY, API_KEY, ENCRYPTION_KEY, CORS_ORIGINS
- [ ] **Frontend** vars: VITE_API_BASE_URL=https://amazonmcp-backend-production.up.railway.app/api
- [ ] Set CRON_SECRET for scheduled jobs
- [ ] Add Resend + Upstash Redis vars if using
- [ ] Create QStash schedules for sync, reports, search-terms
- [ ] Add Amazon Security Profile: Allowed Origins + Return URLs (see Amazon console)
- [ ] Apply changes and deploy
