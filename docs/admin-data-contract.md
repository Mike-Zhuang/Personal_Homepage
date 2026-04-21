# Admin Data Contract (Phase 1 Baseline)

This document freezes the editable section contract for the first admin release.

## Editable Sections

- `site` -> `data/site.toml`
- `projects` -> `data/projects.toml`
- `education` -> `data/education.toml`
- `achievements` -> `data/achievements.toml`
- `now` -> `data/now.toml`
- `writing` -> `data/writing.toml`

## API Payload Contract

All update requests use this shape:

```json
{
  "content": {
    "...": "section-specific fields"
  }
}
```

`content` must be an object and must match the section-level required keys below.

## Required Keys by Section

- `site`: `hero`, `profile`, `about`, `social`, `contact`, `footer`
- `projects`: `section`, `items`
- `education`: `section`, `items`
- `achievements`: `section`, `items`
- `now`: `section`, `items`
- `writing`: `section`

## List-Type Keys

The following keys must be arrays when present:

- `projects.items`
- `education.items`
- `achievements.items`
- `now.items`
- `writing.items` (optional but must be array if provided)

## Nested Optional Fields Used By Homepage V2

These fields are optional and do not change the top-level section contract:

- `site.hero.badges[]`
- `site.hero.quickFacts[] = { label, value }`
- `projects.items[].featured`
- `projects.items[].year`
- `projects.items[].highlights[]`
- `writing.items[].summary`

The admin UI does not need special endpoint support for these fields because it edits arbitrary nested objects and arrays already.

## Supported Admin Endpoints

- `GET /api/admin/sections`
- `GET /api/admin/content/{section}`
- `PUT /api/admin/content/{section}`
- `GET /api/admin/backups/{section}`
- `POST /api/admin/rollback/{section}/{backup_name}`
- `POST /api/admin/publish`
- `GET /api/admin/publish/status`
- `GET /api/admin/messages?status=all|new|processed&limit=60`
- `GET /api/admin/messages/{message_id}`
- `POST /api/admin/messages/{message_id}/process`
- `GET /api/admin/contact-settings`
- `PUT /api/admin/contact-settings`

All endpoints require `X-Admin-API-Key`.

## Message Center Contract

Message records are runtime-only data and are not stored in git-managed `data/` files.

Record shape:

```json
{
  "id": "msg_20260421134501_ab12cd34",
  "createdAt": "2026-04-21T13:45:01.123456+00:00",
  "status": "new",
  "processedAt": null,
  "name": "optional",
  "email": "optional",
  "phone": "optional",
  "wantReply": true,
  "content": "required message text",
  "ipHash": "hashed-client-ip",
  "userAgent": "optional user agent"
}
```

Rules:

- Public site only supports submit (`POST /api/contact`), no public read endpoint.
- Message content is required; name/email/phone/wantReply are optional.
- Admin supports list/detail/mark-processed only. Delete is intentionally disabled.

## SMTP Settings Contract

`GET /api/admin/contact-settings` returns current contact delivery settings with masked password state:

```json
{
  "contactPlaceholderMode": false,
  "smtpHost": "smtp.exmail.qq.com",
  "smtpPort": 465,
  "smtpUseSsl": true,
  "smtpUseStarttls": false,
  "smtpUser": "ops@example.com",
  "smtpPassConfigured": true,
  "mailFrom": "ops@example.com",
  "mailTo": "mike@mikezhuang.cn",
  "mailSubjectPrefix": "[Personal Homepage]"
}
```

`PUT /api/admin/contact-settings` accepts full settings update. `smtpPass` is optional; when omitted or blank, existing password is kept unchanged.

## Write Safety Rules

- Save uses atomic write (temp file + replace).
- A backup is created before overwrite.
- Backup retention keeps latest `ADMIN_BACKUP_LIMIT` items per section (default: 10).
- If `ADMIN_AUTO_PUBLISH_ON_SAVE=true`, save and rollback will trigger the publish script asynchronously.
