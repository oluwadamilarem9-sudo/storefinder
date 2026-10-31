# StoreFinder web (Next.js)

SaaS UI for StoreFinder. Talks to the FastAPI backend (`services.account_service`).

Production Blueprint: repo-root [`render.yaml`](../render.yaml) service **`storefinder-web`**. See [`DEPLOY_RENDER.md`](../DEPLOY_RENDER.md).

## Local run

1. Start the API (repo root), with a **local** SQLite URL only:

```powershell
$env:DATABASE_URL = "sqlite:///./storefinder_web_dev.db"
$env:JWT_SECRET_KEY = "dev-only-local-test-key-32chars!!"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

2. Discovery workers:
   - Default off Render: the API embeds the existing worker pool (`START_DISCOVERY_WORKERS` defaults to on locally).
   - Or set `START_DISCOVERY_WORKERS=0` and run `python shopify-lead-finder/worker.py` (same as production).

3. Start the web app:

```powershell
cd web
npm run dev
```

Open **http://127.0.0.1:3000** (use this host consistently — `localhost` and `127.0.0.1` are different browser origins).

`NEXT_PUBLIC_API_URL` defaults to `http://127.0.0.1:8000` via `.env.local`.
