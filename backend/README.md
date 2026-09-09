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
