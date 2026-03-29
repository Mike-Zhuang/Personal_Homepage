# Personal Homepage

A minimal, editorial-style personal homepage.

## Stack

- Frontend: Hugo (static site)
- Backend: FastAPI (minimal API for health and contact placeholder)
- Deployment: Nginx + systemd + cron sync script

## Project Layout

```
.
├── api/
│   ├── app/main.py
│   └── requirements.txt
├── content/
├── data/
├── deploy/
│   ├── env/api.env.example
│   ├── nginx/personal-homepage.conf
│   ├── scripts/sync-and-reload.sh
│   └── systemd/personal-homepage-api.service
├── layouts/
├── static/
├── config.toml
└── README.md
```

## Local Development

### 1) Frontend (Hugo)

```bash
hugo server
```

The site will run on `http://127.0.0.1:1313`.

`config.development.toml` is included for local preview:

- `baseURL` uses `http://127.0.0.1:1313/`
- `params.apiBaseUrl` uses `http://127.0.0.1:8000`

### 2) Backend (FastAPI)

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Health endpoint:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

Or use the helper script:

```bash
./scripts/dev-api.sh
```

## VS Code Debug Preview

Use the built-in debug launch profile:

1. Open Run and Debug panel.
2. Select `Full Stack: Run and Preview`.
3. Press F5.

This will:

- Start FastAPI backend on `127.0.0.1:8000`
- Start Hugo dev server on `127.0.0.1:1313`
- Open the homepage in your default browser

## Where To Fill Your Content Later

- Site-level copy: `data/site.toml`
- Projects: `data/projects.toml`
- Writing list: `data/writing.toml`
- Current focus: `data/now.toml`

All visible website copy is currently in English placeholders.

Detailed edit map: `docs/content-fill-guide.md`.

Checklist before you publish content: `docs/website-content-checklist.md`.
