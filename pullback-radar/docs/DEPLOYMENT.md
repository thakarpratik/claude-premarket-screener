# Deployment

## Components
- **Backend.** FastAPI (`uvicorn pullback_radar.app:create_app --factory`). It holds every provider
  credential, runs the scanner and the background scheduler, and talks to PostgreSQL.
- **Frontend.** A Next.js server. The browser only ever calls the Next.js origin, and `/api/*` is
  rewritten to `BACKEND_URL`, so session cookies are first-party and no secret reaches client code.
- **Database.** PostgreSQL 14+ (tested on 16). Tables are created on start-up; see `schema.sql`. SQLite is
  for local development only.

## Docker Compose (single host)
```bash
cp backend/.env.example backend/.env    # set DATA_MODE=live and keys for real data
docker compose up --build -d            # web on :3000, backend internal, postgres volume
```
Put a TLS-terminating reverse proxy (Caddy, nginx, a cloud load balancer) in front of port 3000. Then set
`COOKIE_SECURE=true` and `CORS_ORIGINS=https://your.domain`.

## Separate services (for example Render, Fly.io, Railway, a VM)
1. **PostgreSQL.** A managed instance. Set `DATABASE_URL=postgresql://user:pass@host:5432/db`; the
   `postgres://` and `postgresql://` prefixes are both accepted.
2. **Backend.** `pip install -r requirements.txt`, then
   `uvicorn pullback_radar.app:create_app --factory --host 0.0.0.0 --port 8000`. Run **one** instance:
   the in-process scheduler runs per process. To scale out, set `SCHEDULER_ENABLED=false` on extra replicas.
3. **Frontend.** `npm ci && npm run build && npm start` with `BACKEND_URL` pointing at the backend's
   private URL. On Vercel, set the project root to `pullback-radar/web` and add `BACKEND_URL` as an
   environment variable. The rewrite proxies requests server-side.
4. **Persistent disk.** Mount `backend/data` (or set `CACHE_DIR` and `UPLOAD_DIR`) to keep the provider
   cache for closed sessions and journal screenshots.

## Production checklist
- `DATA_MODE=live` with `POLYGON_API_KEY`. Set `POLYGON_DELAY_MINUTES` to your plan's real latency.
- Check that your provider plans allow your use, in particular redistribution to other users of the app.
- `COOKIE_SECURE=true` behind HTTPS. After creating your accounts, set `ALLOW_REGISTRATION=false` if the
  app is private.
- Keep `SEC_USER_AGENT` descriptive and include contact details (an SEC requirement).
- Set `MAX_CANDIDATES` to fit your rate limits. One deep scan costs roughly 4–6 requests per symbol
  (daily bars, intraday bars, quote, reference, news, splits), minus cache hits.
- Logs: the API logs provider errors, which also appear in `GET /api/status`
  (`recent_errors`, `provider_stats`). Ship stdout to your log system.
- Backups: back up PostgreSQL. It holds accounts, settings, watchlists, alerts, journal and backtests.
  Scan payloads are pruned to the latest 20 per user.
- Update `pullback_radar/data/fomc_schedule.json` when the Federal Reserve publishes a new year.

## Verifying a deployment
```bash
curl https://your.domain/api/health      # {"ok": true}
curl https://your.domain/api/status      # mode "live", ready true, providers listed, no setup messages
```
Then sign in, run **Scan now**, and check that cards show `delayed` or `real-time` freshness with today's
timestamps, not `synthetic`.
