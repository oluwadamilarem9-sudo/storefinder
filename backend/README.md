# StoreFinder Backend

This backend adds user accounts, per-user projects, and per-project discovery runs so leads are not shared globally.

## Run

```bash
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

## Auth flow

- Sign up with name, email, password
- Log in to receive a token
- Use the token in the Authorization header: `Bearer <token>`
- Create a project
- Create a discovery run
- Add leads belonging to that project and run

## Important design rule

Every lead, project, and run belongs to a user. Users cannot access other users' projects.

## Production database and deployment

The backend supports both a local SQLite file (default) and a production `DATABASE_URL` (recommended).

- Local development (default): the backend will use `sqlite:///./storefinder.db` unless you pass `db_path` to `create_app()`.
- Production: set the `DATABASE_URL` environment variable to a full SQLAlchemy-compatible connection string (for example, a managed Postgres connection string):

```
export DATABASE_URL=postgresql://user:pass@host:5432/storefinder
export JWT_SECRET_KEY="a-very-long-secret-you-must-change"
export CORS_ALLOWED_ORIGINS="https://storefinder.streamlit.app"
```

When running under a production database the app uses SQLAlchemy with connection pooling and `pool_pre_ping=True` to avoid stale connections. For deployments on Render, the included `render.yaml` wires the managed database into `DATABASE_URL` automatically.

Be sure to provision a secure `JWT_SECRET_KEY` in your deployment platform — the fallback secret in the repo is insecure and only intended for local development.

If you use Render or a similar host, point your frontend `BACKEND_URL` to the backend service URL and set `CORS_ALLOWED_ORIGINS` to the frontend host so the Streamlit UI can call the API from the browser.
