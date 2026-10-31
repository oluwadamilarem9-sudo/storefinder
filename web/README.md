# StoreFinder web (Next.js)

SaaS UI for StoreFinder. Talks to the existing FastAPI backend (`services.account_service`).

## Local run

1. Start the API (repo root), with a **local** SQLite URL only:

```powershell
$env:DATABASE_URL = "sqlite:///./storefinder_web_dev.db"
$env:JWT_SECRET_KEY = "dev-only-change-me"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

2. Optional: start a discovery worker in another terminal (same `DATABASE_URL`) so queued runs execute:

```powershell
cd shopify-lead-finder
python worker.py
```

3. Start the web app:

```powershell
cd web
npm run dev
```

Open http://localhost:3000

`NEXT_PUBLIC_API_URL` defaults to `http://127.0.0.1:8000` via `.env.local`.
