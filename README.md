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

Optional local log file:

```bash
API_DEV_LOG_FILE=./runtime/logs/api-dev.log ./scripts/dev-api.sh
```

### 3) Template Path Guard

Run this check before build/dev server if needed:

```bash
./scripts/check-template-asset-paths.sh
```

## VS Code Debug Preview

Use the built-in debug launch profile:

1. Open Run and Debug panel.
2. Select `Full Stack: Run`.
3. Press F5.

This will:

- Start FastAPI backend on `127.0.0.1:8000`
- Start Hugo dev server on `127.0.0.1:1313`

You can open the page manually:

```text
http://127.0.0.1:1313
http://127.0.0.1:1313/admin/
```

## Where To Fill Your Content Later

- Site-level copy: `data/site.toml`
- Projects: `data/projects.toml`
- Writing list: `data/writing.toml`
- Current focus: `data/now.toml`

All visible website copy is currently in English placeholders.

Detailed edit map: `docs/content-fill-guide.md`.

Checklist before you publish content: `docs/website-content-checklist.md`.

## Secret Configuration (管理员密钥)

1. `deploy/env/api.env.example` 仅用于示例，禁止填写真实密钥。
2. 真实密钥请放在 `deploy/env/api.env`（已加入 `.gitignore`，不会提交）。
3. 本地开发脚本会从以下位置读取 `ADMIN_API_KEY`（按优先级）：
   - `api/.env`
   - `deploy/env/api.env`

### 服务器同步仓库后修改管理员密钥

1. 修改后端密钥：

```bash
sudo vim /opt/personal-homepage/deploy/env/api.env
# 更新：
# ADMIN_API_KEY=your-new-strong-key
```

2. 修改 Nginx 管理代理里的同一份密钥：

```bash
sudo vim /opt/personal-homepage/deploy/nginx/personal-homepage.conf
# location /api/admin/ 里：
# set $admin_api_key "your-new-strong-key";
```

3. 应用配置：

```bash
sudo systemctl restart personal-homepage-api
sudo nginx -t && sudo systemctl reload nginx
```
