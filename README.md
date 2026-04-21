# Personal Homepage

A minimal, editorial-style personal homepage.

## Stack

- Frontend: Hugo (static site)
- Backend: FastAPI (health, content admin, private contact messages)
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

# Contact endpoint (public write-only)
curl -sS -X POST http://127.0.0.1:8000/api/contact \
   -H 'Content-Type: application/json' \
   -d '{"content":"Hello from local test","wantReply":true}'
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
4. Admin 页面不会把密钥写入 `localStorage/sessionStorage`；刷新后需要重新输入。

### 服务器同步仓库后修改管理员密钥

1. 修改后端密钥：

```bash
sudo vim /opt/personal-homepage/deploy/env/api.env
# 更新：
# ADMIN_API_KEY=your-new-strong-key
```

2. 应用配置：

```bash
sudo systemctl restart personal-homepage-api
sudo nginx -t && sudo systemctl reload nginx
```

## 私信留言与邮件通知

- 首页 Contact 区提供单向私信表单。
- 留言不会公开展示，运行时会优先写入 SQLite 留言库，并在 Admin Message Center 查看。
- Admin 消息能力：列表、详情、标记已处理（不提供删除）。
- Admin 页面提供 SMTP 设置面板，可在线调整发信参数并立即生效。
- 留言入口默认启用 10KB 报文限制、严格 JSON 校验、敏感词 DFA 过滤和按真实 IP/指纹的令牌桶限流。

### 关键环境变量

- `CONTACT_DB_PATH`: 留言 SQLite 数据库路径（生产环境推荐）
- `CONTACT_MESSAGES_PATH`: 旧版 JSONL 留言文件路径，仅用于兼容迁移
- `SENSITIVE_WORDS_PATH`: DFA 敏感词库文件路径
- `CONTACT_SETTINGS_PATH`: 联系与 SMTP 在线配置文件路径
- `CONTACT_MAX_BODY_BYTES`: 留言请求 JSON 最大体积
- `CONTACT_MAX_JSON_DEPTH`: JSON 最大嵌套层级
- `CONTACT_MAX_INTEGER_ABS`: JSON 数字允许的最大绝对值
- `CONTACT_RATE_LIMIT_WINDOW_SECONDS`: 限流窗口秒数
- `CONTACT_RATE_LIMIT_MAX_REQUESTS`: 窗口内最大提交次数
- `TRUSTED_PROXY_IPS`: 可信反向代理 IP / 网段白名单
- `CONTACT_ENABLE_GEO_LOOKUP`: 是否启用基于真实 IP 的 GeoIP 外部查询
- `CONTACT_IP_HASH_SALT`: IP 哈希盐值
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_SSL` / `SMTP_USE_STARTTLS`
- `SMTP_USER` / `SMTP_PASS` / `MAIL_FROM` / `MAIL_TO`

> 推荐：服务器使用 `deploy/env/api.env` 保存真实 SMTP 凭据，`api.env.example` 仅保留占位符。

说明：如果你在 Admin 页面保存了 SMTP 设置，后端会优先使用 `CONTACT_SETTINGS_PATH` 中的在线配置。
