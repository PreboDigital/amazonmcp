# Railway Deployment Guide — Amazon Ads Optimizer

## Deployment URLs

| Service | URL |
|---------|-----|
| **App (unified)** | https://amazonmcp-backend-production.up.railway.app |
| **Frontend (legacy)** | https://amazonmcp-frontend-production.up.railway.app — **deprecated** when using unified deploy |

**Local development:**
- Frontend: http://localhost:5173 (Vite proxy → backend)
- Backend: http://localhost:8000

---

## Fix CORS: Use Unified Deployment (Recommended)

**Problem:** Separate frontend + backend causes CORS errors when the frontend calls the API across origins.

**Solution:** Serve frontend and API from the **same origin** (single backend service). No CORS.

### Unified Deploy Steps

1. **Backend service** — Railway Dashboard → your backend service → Settings
2. **Root Directory**: Clear it (leave empty) — so Railway uses the repo root
3. **Build**: Railway auto-detects the root `Dockerfile` and builds frontend + backend together
4. **Result**: One URL serves both app and API. Use `https://amazonmcp-backend-production.up.railway.app`
5. **Frontend service**: You can **pause or delete** it — the backend now serves the frontend

### What the unified Dockerfile does

- Builds the Vite frontend (no `VITE_API_BASE_URL` needed — uses relative `/api`)
- Copies `frontend/dist` into `backend/static`
- Runs FastAPI; serves `/api/*` and static files from same origin

---

## Services to Create

| Service | Type | Purpose |
|---------|------|---------|
| **Web** | Backend (unified) | FastAPI API + frontend static — **single service** |
| **PostgreSQL** | Database | Railway plugin — stores credentials, campaigns, reports |
| **Frontend** | Static (optional) | Only if you want a separate frontend deploy (not recommended — CORS) |

### Recommended: Single Web Service + PostgreSQL (Unified)

1. **Add PostgreSQL** — Railway Dashboard → New → Database → PostgreSQL
2. **Add Web Service** — New → GitHub Repo → your repo
3. **Root Directory**: **empty** (repo root) — so the root `Dockerfile` is used
4. **Build**: Railway uses the root `Dockerfile` (frontend + backend)

---

## Environment Variables (Railway → Variables)

### Required

```env
ENVIRONMENT=production
DATABASE_URL=${{Postgres.DATABASE_URL}}   # Auto-linked if you add Postgres plugin
# First admin (set on first deploy; bootstrap creates when no users exist)
FIRST_ADMIN_EMAIL=admin@yourdomain.com
FIRST_ADMIN_PASSWORD=<strong-password>
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
# Required for in-app schedule creation — your app's public URL (e.g. https://amazonmcp-production.up.railway.app)
PUBLIC_URL=https://your-app.up.railway.app
```

---

## Auto-Fetch Schedule (Upstash QStash)

Use [Upstash QStash](https://upstash.com/docs/qstash) to schedule HTTP calls to your cron endpoints.

### 1. Get QStash Credentials

- Go to [Upstash Console](https://console.upstash.com/) → QStash
- Copy `QSTASH_TOKEN` (for creating schedules)

### 2. Create Schedules (In-App)

**Recommended:** Use the **Data Sync** page in the app. Admins can add schedules directly:
- Set `QSTASH_TOKEN` and `CRON_SECRET` in Railway Variables
- **Set `PUBLIC_URL`** to your app's public URL (e.g. `https://amazonmcp-production.up.railway.app`) — required for schedule creation; Railway's `RAILWAY_PUBLIC_DOMAIN` may not always be available
- Go to Data Sync → Add schedule (job + frequency)

### 3. Create Schedules (curl / alternative)

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

### Unified (recommended — no CORS)

- **Root Directory**: empty (repo root)
- **Builder**: Dockerfile (auto-detected)
- **No** `VITE_API_BASE_URL` — frontend uses relative `/api`

### Separate frontend + backend (legacy — CORS issues)

- **Backend** Root Directory: `backend` — uses nixpacks
- **Frontend** Root Directory: `frontend` — set `VITE_API_BASE_URL=https://amazonmcp-backend-production.up.railway.app/api`
- CORS must be configured; preflight can fail on some proxies

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
- [ ] Add Backend service — **Root Directory: empty** (unified deploy)
- [ ] **Backend** vars: DATABASE_URL (`${{Postgres.DATABASE_URL}}`), SECRET_KEY, API_KEY, ENCRYPTION_KEY, FIRST_ADMIN_EMAIL, FIRST_ADMIN_PASSWORD
- [ ] Do **not** set VITE_API_BASE_URL (unified deploy uses relative `/api`)
- [ ] Set CRON_SECRET and QSTASH_TOKEN for in-app schedule management
- [ ] Add Resend + Upstash Redis vars if using
- [ ] Create QStash schedules for sync, reports, search-terms
- [ ] Add Amazon Security Profile: Allowed Origins + Return URLs (see Amazon console)
- [ ] Apply changes and deploy
