from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import hashlib
import html
import json
import logging
import re
import sqlite3
import smtplib
import ssl
import time
import unicodedata
from ipaddress import ip_address, ip_network
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from threading import Lock, RLock, Thread
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import tomli_w
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = PROJECT_ROOT / "runtime" / "contact-messages.log"
DEFAULT_MESSAGES_PATH = PROJECT_ROOT / "runtime" / "messages" / "messages.jsonl"
DEFAULT_CONTACT_DB_PATH = PROJECT_ROOT / "runtime" / "messages" / "messages.sqlite3"
DEFAULT_CONTACT_SETTINGS_PATH = PROJECT_ROOT / "runtime" / "messages" / "contact-settings.json"
DEFAULT_SENSITIVE_WORDS_PATH = PROJECT_ROOT / "api" / "app" / "sensitive-words.txt"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "runtime" / "backups"
DEFAULT_PUBLISH_SCRIPT = PROJECT_ROOT / "deploy" / "scripts" / "publish-content.sh"

SECTION_FILES = {
    "site": "site.toml",
    "projects": "projects.toml",
    "education": "education.toml",
    "achievements": "achievements.toml",
    "now": "now.toml",
    "writing": "writing.toml",
}

SECTION_REQUIRED_KEYS = {
    "site": {"hero", "profile", "about", "social", "contact", "footer"},
    "projects": {"section", "items"},
    "education": {"section", "items"},
    "achievements": {"section", "items"},
    "now": {"section", "items"},
    "writing": {"section"},
}

SECTION_LIST_KEYS = {
    "projects": ("items",),
    "education": ("items",),
    "achievements": ("items",),
    "now": ("items",),
    "writing": ("items",),
}

WRITE_LOCK = Lock()
MESSAGE_WRITE_LOCK = Lock()
CONTACT_SETTINGS_LOCK = Lock()
PUBLISH_STATE_LOCK = Lock()
RATE_LIMIT_LOCK = Lock()
CONTACT_DB_LOCK = RLock()
RATE_LIMIT_BUCKETS: dict[str, dict[str, float]] = {}
RECENT_SUBMISSIONS: dict[str, float] = {}
SENSITIVE_WORDS_ROOT: dict[str, Any] = {}
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9()\-\s]{5,31}$")
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._%+\-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$")
SENSITIVE_WORD_END = "__end__"
IGNORED_SENSITIVE_CHARS = set(" \t\r\n-_*.,;:|/\\'\"`~!@#$%^&()+=<>[]{}·。，“”‘’、！？…·")
SUSPICIOUS_PATTERN_RULES = {
    "xssScriptTag": re.compile(r"<\s*script\b", re.IGNORECASE),
    "xssInlineHandler": re.compile(r"\bon\w+\s*=", re.IGNORECASE),
    "xssJavascriptUrl": re.compile(r"javascript\s*:", re.IGNORECASE),
    "sqlBooleanBypass": re.compile(r"(?:'|\")\s*(?:or|and)\s+(?:'?\d+'?\s*=\s*'?\d+'?|true|false)", re.IGNORECASE),
    "sqlComment": re.compile(r"(?:--|#|/\*)"),
    "sqlDangerousKeyword": re.compile(r"\b(?:drop|truncate|union|sleep|benchmark|insert|delete|update)\b", re.IGNORECASE),
}
PUBLISH_STATE: dict[str, str | None] = {
    "status": "idle",
    "startedAt": None,
    "finishedAt": None,
    "lastError": None,
    "lastOutput": None,
}


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_origins(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    if parsed <= 0:
        return default

    return parsed


CONTACT_LOG_PATH = Path(os.getenv("CONTACT_LOG_PATH", str(DEFAULT_LOG_PATH))).expanduser()
CONTACT_MESSAGES_PATH = Path(os.getenv("CONTACT_MESSAGES_PATH", str(DEFAULT_MESSAGES_PATH))).expanduser()
CONTACT_DB_PATH = Path(os.getenv("CONTACT_DB_PATH", str(DEFAULT_CONTACT_DB_PATH))).expanduser()
CONTACT_SETTINGS_PATH = Path(os.getenv("CONTACT_SETTINGS_PATH", str(DEFAULT_CONTACT_SETTINGS_PATH))).expanduser()
SENSITIVE_WORDS_PATH = Path(os.getenv("SENSITIVE_WORDS_PATH", str(DEFAULT_SENSITIVE_WORDS_PATH))).expanduser()
CONTACT_PLACEHOLDER_MODE = parse_bool(os.getenv("CONTACT_PLACEHOLDER_MODE"), default=True)
CONTACT_MAX_BODY_BYTES = parse_int(os.getenv("CONTACT_MAX_BODY_BYTES"), default=10 * 1024)
CONTACT_MAX_JSON_DEPTH = parse_int(os.getenv("CONTACT_MAX_JSON_DEPTH"), default=8)
CONTACT_MAX_INTEGER_ABS = parse_int(os.getenv("CONTACT_MAX_INTEGER_ABS"), default=2147483647)
CONTACT_RATE_LIMIT_WINDOW_SECONDS = parse_int(os.getenv("CONTACT_RATE_LIMIT_WINDOW_SECONDS"), default=60)
CONTACT_RATE_LIMIT_MAX_REQUESTS = parse_int(os.getenv("CONTACT_RATE_LIMIT_MAX_REQUESTS"), default=2)
CONTACT_RATE_LIMIT_BURST = parse_int(os.getenv("CONTACT_RATE_LIMIT_BURST"), default=2)
CONTACT_DUPLICATE_WINDOW_SECONDS = parse_int(os.getenv("CONTACT_DUPLICATE_WINDOW_SECONDS"), default=120)
CONTACT_IP_HASH_SALT = os.getenv("CONTACT_IP_HASH_SALT", "")
CONTACT_ERROR_LOG_PATH = Path(
    os.getenv("CONTACT_ERROR_LOG_PATH", str(PROJECT_ROOT / "runtime" / "contact-errors.jsonl"))
).expanduser()
TRUSTED_PROXY_IPS = parse_origins(os.getenv("TRUSTED_PROXY_IPS", "127.0.0.1,::1"))
CONTACT_ENABLE_GEO_LOOKUP = parse_bool(os.getenv("CONTACT_ENABLE_GEO_LOOKUP"), default=False)
ALLOW_ORIGINS = parse_origins(os.getenv("CORS_ALLOW_ORIGINS"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(DEFAULT_DATA_ROOT))).expanduser()
BACKUP_ROOT = Path(os.getenv("ADMIN_BACKUP_ROOT", str(DEFAULT_BACKUP_ROOT))).expanduser()
BACKUP_LIMIT = parse_int(os.getenv("ADMIN_BACKUP_LIMIT"), default=10)
CONTENT_PUBLISH_SCRIPT = Path(os.getenv("CONTENT_PUBLISH_SCRIPT", str(DEFAULT_PUBLISH_SCRIPT))).expanduser()
AUTO_PUBLISH_ON_SAVE = parse_bool(os.getenv("ADMIN_AUTO_PUBLISH_ON_SAVE"), default=False)
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = parse_int(os.getenv("SMTP_PORT"), default=465)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_USE_SSL = parse_bool(os.getenv("SMTP_USE_SSL"), default=True)
SMTP_USE_STARTTLS = parse_bool(os.getenv("SMTP_USE_STARTTLS"), default=False)
MAIL_TO = os.getenv("MAIL_TO", "mike@mikezhuang.cn").strip()
MAIL_FROM = os.getenv("MAIL_FROM", "").strip()
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "[Personal Homepage]").strip() or "[Personal Homepage]"

CONTACT_SETTINGS_KEYS = {
    "contactPlaceholderMode",
    "smtpHost",
    "smtpPort",
    "smtpUseSsl",
    "smtpUseStarttls",
    "smtpUser",
    "smtpPass",
    "mailFrom",
    "mailTo",
    "mailSubjectPrefix",
}

if not ALLOW_ORIGINS:
    ALLOW_ORIGINS = ["http://127.0.0.1:1313", "http://localhost:1313"]

logger = logging.getLogger("personal-homepage")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

TRUSTED_PROXY_NETWORKS = []
for candidate in TRUSTED_PROXY_IPS:
    try:
        if "/" in candidate:
            TRUSTED_PROXY_NETWORKS.append(ip_network(candidate, strict=False))
        else:
            parsed_ip = ip_address(candidate)
            prefix = 32 if parsed_ip.version == 4 else 128
            TRUSTED_PROXY_NETWORKS.append(ip_network(f"{parsed_ip}/{prefix}", strict=False))
    except ValueError:
        logger.warning("ignore invalid TRUSTED_PROXY_IPS entry: %s", candidate)

app = FastAPI(title="Personal Homepage API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    init_contact_storage()
    load_sensitive_words()


@app.middleware("http")
async def add_security_headers(request: Request, call_next):  # type: ignore[override]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


@app.middleware("http")
async def contact_payload_guard(request: Request, call_next):  # type: ignore[override]
    if request.url.path == "/api/contact" and request.method.upper() == "POST":
        try:
            content_type = request.headers.get("content-type", "").lower()
            if "application/json" not in content_type:
                raise HTTPException(status_code=415, detail="Contact endpoint only accepts application/json.")

            content_length_text = request.headers.get("content-length", "").strip()
            if content_length_text:
                try:
                    content_length_value = int(content_length_text)
                except ValueError as error:
                    raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from error

                if content_length_value > CONTACT_MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="Request body is too large.")

            body_bytes = await request.body()
            request.state.contact_payload = parse_contact_request_payload(body_bytes)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    return await call_next(request)


class ContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    wantReply: bool = False
    content: str | None = Field(default=None, max_length=2000)
    # 兼容旧请求字段，后续将由 content 统一承载留言正文
    message: str | None = Field(default=None, max_length=2000)
    # 蜜罐字段：真实用户页面不会填写
    website: str | None = Field(default=None, max_length=120)
    # 前端采集的访问上下文，尽量帮助区分真实访客与自动化流量
    clientMeta: dict[str, Any] | None = None


class ContactResponse(BaseModel):
    status: str
    message: str
    receivedAt: str


class AdminSectionItem(BaseModel):
    key: str
    file: str


class AdminSectionsResponse(BaseModel):
    sections: list[AdminSectionItem]


class AdminContentResponse(BaseModel):
    section: str
    sourceFile: str
    content: dict[str, Any]


class AdminUpdateRequest(BaseModel):
    content: dict[str, Any]


class AdminWriteResponse(BaseModel):
    status: str
    section: str
    backup: str | None = None
    updatedAt: str
    publishStatus: str | None = None


class BackupItem(BaseModel):
    name: str
    createdAt: str
    sizeBytes: int


class AdminBackupsResponse(BaseModel):
    section: str
    backups: list[BackupItem]


class PublishStatusResponse(BaseModel):
    status: str
    startedAt: str | None = None
    finishedAt: str | None = None
    lastError: str | None = None
    lastOutput: str | None = None


class AdminMessageItem(BaseModel):
    id: str
    createdAt: str
    status: str
    wantReply: bool
    name: str
    email: str
    phone: str
    preview: str


class AdminMessagesResponse(BaseModel):
    total: int
    messages: list[AdminMessageItem]


class AdminMessageDetailResponse(BaseModel):
    message: dict[str, Any]


class AdminMessageProcessResponse(BaseModel):
    status: str
    messageId: str
    processedAt: str


class AdminContactSettingsResponse(BaseModel):
    contactPlaceholderMode: bool
    smtpHost: str
    smtpPort: int
    smtpUseSsl: bool
    smtpUseStarttls: bool
    smtpUser: str
    smtpPassConfigured: bool
    mailFrom: str
    mailTo: str
    mailSubjectPrefix: str


class AdminContactSettingsUpdateRequest(BaseModel):
    contactPlaceholderMode: bool
    smtpHost: str = Field(default="", max_length=200)
    smtpPort: int = Field(default=465)
    smtpUseSsl: bool = True
    smtpUseStarttls: bool = False
    smtpUser: str = Field(default="", max_length=200)
    smtpPass: str | None = Field(default=None, max_length=200)
    mailFrom: str = Field(default="", max_length=200)
    mailTo: str = Field(default="", max_length=200)
    mailSubjectPrefix: str = Field(default="[Personal Homepage]", max_length=200)


def looks_like_email(value: str) -> bool:
    if len(value) > 254 or value.count("@") != 1 or ".." in value:
        return False

    local_part, domain_part = value.rsplit("@", 1)
    if not local_part or not domain_part:
        return False

    if local_part.startswith(".") or local_part.endswith("."):
        return False

    if domain_part.startswith(".") or domain_part.endswith(".") or "." not in domain_part:
        return False

    return bool(EMAIL_PATTERN.fullmatch(value))


def looks_like_phone(value: str) -> bool:
    compact = re.sub(r"[\s()\-]+", "", value)
    if not compact.startswith("+") and not compact.isdigit():
        return False

    digit_count = sum(char.isdigit() for char in compact)
    return bool(PHONE_PATTERN.fullmatch(value)) and 6 <= digit_count <= 20


def sanitize_for_log(value: str) -> str:
    collapsed = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]+", " ", value)
    return " ".join(collapsed.strip().split())


def sanitize_multiline_text(value: str | None, limit: int) -> str:
    if value is None:
        return ""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]+", " ", normalized)

    if len(normalized) > limit:
        return normalized[:limit]

    return normalized


def sanitize_optional_text(value: str | None, limit: int) -> str:
    if value is None:
        return ""

    sanitized = sanitize_for_log(value)
    if len(sanitized) > limit:
        return sanitized[:limit]

    return sanitized


def truncate_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n... (truncated)"


def sanitize_header_value(value: str | None, limit: int = 280) -> str:
    return sanitize_optional_text(value, limit=limit)


def sanitize_meta_value(value: Any, limit: int = 160) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    return sanitize_optional_text(str(value), limit=limit)


def write_structured_contact_log(kind: str, payload: dict[str, Any]) -> None:
    record = {
        "kind": kind,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    CONTACT_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONTACT_ERROR_LOG_PATH.open("a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(record, ensure_ascii=False))
        file_obj.write("\n")


def escape_html_text(value: str) -> str:
    return html.escape(value, quote=True)


def build_sanitized_contact_snapshot(name: str, email: str, phone: str, content: str) -> dict[str, str]:
    return {
        "nameEscaped": escape_html_text(name),
        "emailEscaped": escape_html_text(email),
        "phoneEscaped": escape_html_text(phone),
        "contentEscaped": escape_html_text(content),
    }


def normalize_sensitive_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    chars: list[str] = []
    for char in normalized:
        if char in IGNORED_SENSITIVE_CHARS:
            continue
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            chars.append(char)
    return "".join(chars)


def add_sensitive_word(word: str) -> None:
    normalized = normalize_sensitive_text(word)
    if not normalized:
        return

    cursor = SENSITIVE_WORDS_ROOT
    for char in normalized:
        cursor = cursor.setdefault(char, {})
    cursor[SENSITIVE_WORD_END] = normalized


def load_sensitive_words() -> None:
    SENSITIVE_WORDS_ROOT.clear()
    if not SENSITIVE_WORDS_PATH.exists() or not SENSITIVE_WORDS_PATH.is_file():
        logger.warning("sensitive words file not found: %s", SENSITIVE_WORDS_PATH)
        return

    with SENSITIVE_WORDS_PATH.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            candidate = line.strip()
            if not candidate or candidate.startswith("#"):
                continue
            add_sensitive_word(candidate)


def find_sensitive_words(*values: str) -> list[str]:
    if not SENSITIVE_WORDS_ROOT:
        load_sensitive_words()

    matched_words: list[str] = []
    for value in values:
        normalized = normalize_sensitive_text(value)
        if not normalized:
            continue

        for start_index in range(len(normalized)):
            cursor = SENSITIVE_WORDS_ROOT
            for current_char in normalized[start_index:]:
                if current_char not in cursor:
                    break
                cursor = cursor[current_char]
                matched_word = cursor.get(SENSITIVE_WORD_END)
                if matched_word:
                    matched_words.append(str(matched_word))

    return list(dict.fromkeys(matched_words))


def parse_json_int(value: str) -> int:
    if len(value.lstrip("-")) > 10:
        raise ValueError("Integer is too large.")

    parsed = int(value)
    if abs(parsed) > CONTACT_MAX_INTEGER_ABS:
        raise ValueError("Integer exceeds allowed range.")

    return parsed


def parse_json_float(value: str) -> float:
    if len(value) > 32:
        raise ValueError("Float is too large.")
    parsed = float(value)
    if abs(parsed) > float(CONTACT_MAX_INTEGER_ABS):
        raise ValueError("Float exceeds allowed range.")
    return parsed


def reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def validate_json_depth(value: Any, depth: int = 0) -> None:
    if depth > CONTACT_MAX_JSON_DEPTH:
        raise HTTPException(status_code=413, detail="JSON structure is too deep.")

    if isinstance(value, dict):
        for nested_value in value.values():
            validate_json_depth(nested_value, depth + 1)
        return

    if isinstance(value, list):
        for nested_value in value:
            validate_json_depth(nested_value, depth + 1)


def parse_contact_request_payload(body_bytes: bytes) -> dict[str, Any]:
    if len(body_bytes) > CONTACT_MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body is too large.")

    try:
        body_text = body_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="Request body must be valid UTF-8 JSON.") from error

    try:
        payload = json.loads(
            body_text,
            parse_int=parse_json_int,
            parse_float=parse_json_float,
            parse_constant=reject_json_constant,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Invalid JSON payload: {error}") from error
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.") from error

    validate_json_depth(payload)

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Contact payload must be a JSON object.")

    return payload


def open_contact_db() -> sqlite3.Connection:
    CONTACT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CONTACT_DB_PATH, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_contact_storage() -> None:
    with CONTACT_DB_LOCK:
        with open_contact_db() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS contact_messages (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    processed_at TEXT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    want_reply INTEGER NOT NULL,
                    preview TEXT NOT NULL,
                    ip_hash TEXT NOT NULL,
                    user_agent TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_contact_messages_created_at ON contact_messages(created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_contact_messages_status_created_at ON contact_messages(status, created_at DESC)"
            )
            connection.commit()

        migrate_legacy_jsonl_messages()


def migrate_legacy_jsonl_messages() -> None:
    if not CONTACT_MESSAGES_PATH.exists() or not CONTACT_MESSAGES_PATH.is_file():
        return

    legacy_records = read_legacy_message_records()
    if not legacy_records:
        return

    with CONTACT_DB_LOCK:
        with open_contact_db() as connection:
            for record in legacy_records:
                insert_message_record(connection, record, replace_existing=False)

            connection.commit()


def read_legacy_message_records() -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    with CONTACT_MESSAGES_PATH.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            payload = line.strip()
            if not payload:
                continue

            try:
                item = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if isinstance(item, dict) and item.get("id") and item.get("createdAt"):
                messages.append(item)

    return messages


def insert_message_record(
    connection: sqlite3.Connection,
    record: dict[str, Any],
    replace_existing: bool = True,
) -> None:
    insert_mode = "INSERT OR REPLACE" if replace_existing else "INSERT OR IGNORE"
    connection.execute(
        f"""
        {insert_mode} INTO contact_messages (
            id,
            created_at,
            status,
            processed_at,
            name,
            email,
            phone,
            want_reply,
            preview,
            ip_hash,
            user_agent,
            record_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(record.get("id") or ""),
            str(record.get("createdAt") or ""),
            str(record.get("status") or "new"),
            str(record.get("processedAt") or "") or None,
            str(record.get("name") or ""),
            str(record.get("email") or ""),
            str(record.get("phone") or ""),
            1 if bool(record.get("wantReply")) else 0,
            build_message_preview(str(record.get("content") or "")),
            str(record.get("ipHash") or ""),
            str(record.get("userAgent") or ""),
            json.dumps(record, ensure_ascii=False),
        ),
    )


def get_default_contact_settings() -> dict[str, Any]:
    return {
        "contactPlaceholderMode": CONTACT_PLACEHOLDER_MODE,
        "smtpHost": SMTP_HOST,
        "smtpPort": SMTP_PORT,
        "smtpUseSsl": SMTP_USE_SSL,
        "smtpUseStarttls": SMTP_USE_STARTTLS,
        "smtpUser": SMTP_USER,
        "smtpPass": SMTP_PASS,
        "mailFrom": MAIL_FROM,
        "mailTo": MAIL_TO,
        "mailSubjectPrefix": MAIL_SUBJECT_PREFIX,
    }


def normalize_contact_settings(raw: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = get_default_contact_settings() if base is None else dict(base)

    settings = {
        "contactPlaceholderMode": bool(raw.get("contactPlaceholderMode", defaults["contactPlaceholderMode"])),
        "smtpHost": sanitize_optional_text(raw.get("smtpHost"), limit=200),
        "smtpPort": parse_int(str(raw.get("smtpPort", defaults["smtpPort"])), defaults["smtpPort"]),
        "smtpUseSsl": bool(raw.get("smtpUseSsl", defaults["smtpUseSsl"])),
        "smtpUseStarttls": bool(raw.get("smtpUseStarttls", defaults["smtpUseStarttls"])),
        "smtpUser": sanitize_optional_text(raw.get("smtpUser"), limit=200),
        "smtpPass": sanitize_optional_text(raw.get("smtpPass"), limit=200),
        "mailFrom": sanitize_optional_text(raw.get("mailFrom"), limit=200),
        "mailTo": sanitize_optional_text(raw.get("mailTo"), limit=200),
        "mailSubjectPrefix": sanitize_optional_text(raw.get("mailSubjectPrefix"), limit=200),
    }

    if settings["smtpPort"] > 65535:
        settings["smtpPort"] = defaults["smtpPort"]

    if not settings["mailSubjectPrefix"]:
        settings["mailSubjectPrefix"] = "[Personal Homepage]"

    return settings


def read_contact_settings() -> dict[str, Any]:
    defaults = get_default_contact_settings()

    with CONTACT_SETTINGS_LOCK:
        if not CONTACT_SETTINGS_PATH.exists() or not CONTACT_SETTINGS_PATH.is_file():
            return defaults

        try:
            with CONTACT_SETTINGS_PATH.open("r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
        except (OSError, json.JSONDecodeError):
            return defaults

        if not isinstance(payload, dict):
            return defaults

        sanitized_payload = {key: payload.get(key) for key in CONTACT_SETTINGS_KEYS if key in payload}
        merged = dict(defaults)
        merged.update(sanitized_payload)
        return normalize_contact_settings(merged, base=defaults)


def write_contact_settings(settings: dict[str, Any]) -> None:
    CONTACT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CONTACT_SETTINGS_LOCK:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(CONTACT_SETTINGS_PATH.parent),
                delete=False,
                prefix=f".{CONTACT_SETTINGS_PATH.name}.",
                suffix=".tmp",
            ) as temp_file:
                json.dump(settings, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
                temp_path = Path(temp_file.name)

            os.replace(temp_path, CONTACT_SETTINGS_PATH)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)


def to_contact_settings_response(settings: dict[str, Any]) -> AdminContactSettingsResponse:
    return AdminContactSettingsResponse(
        contactPlaceholderMode=bool(settings.get("contactPlaceholderMode", True)),
        smtpHost=str(settings.get("smtpHost") or ""),
        smtpPort=int(settings.get("smtpPort") or 465),
        smtpUseSsl=bool(settings.get("smtpUseSsl", True)),
        smtpUseStarttls=bool(settings.get("smtpUseStarttls", False)),
        smtpUser=str(settings.get("smtpUser") or ""),
        smtpPassConfigured=bool(settings.get("smtpPass")),
        mailFrom=str(settings.get("mailFrom") or ""),
        mailTo=str(settings.get("mailTo") or ""),
        mailSubjectPrefix=str(settings.get("mailSubjectPrefix") or "[Personal Homepage]"),
    )


def resolve_contact_content(payload: ContactRequest) -> str:
    candidate = payload.content if payload.content is not None else payload.message
    content = sanitize_multiline_text(candidate, limit=2000)

    if len(content) < 3:
        raise HTTPException(status_code=422, detail="Message content is required.")

    return content


def build_message_id() -> str:
    now_part = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    token_part = secrets.token_hex(4)
    return f"msg_{now_part}_{token_part}"


def is_trusted_proxy(source_ip: str) -> bool:
    try:
        parsed_ip = ip_address(source_ip)
    except ValueError:
        return False

    return any(parsed_ip in network for network in TRUSTED_PROXY_NETWORKS)


def is_public_ip(ip_value: str) -> bool:
    try:
        parsed_ip = ip_address(ip_value)
    except ValueError:
        return False

    return not (
        parsed_ip.is_loopback
        or parsed_ip.is_private
        or parsed_ip.is_reserved
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
    )


def get_client_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client and request.client.host else "unknown"
    if not is_trusted_proxy(direct_ip):
        return direct_ip

    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if x_forwarded_for:
        forwarded_chain = [item.strip() for item in x_forwarded_for.split(",") if item.strip()]
        for forwarded_ip in forwarded_chain:
            if forwarded_ip and not is_trusted_proxy(forwarded_ip):
                return forwarded_ip

    x_real_ip = request.headers.get("x-real-ip", "").strip()
    if x_real_ip and not is_trusted_proxy(x_real_ip):
        return x_real_ip

    return direct_ip


def hash_client_ip(ip_value: str) -> str:
    salt = CONTACT_IP_HASH_SALT or ADMIN_API_KEY or "contact-ip-salt"
    digest = hashlib.sha256(f"{salt}:{ip_value}".encode("utf-8")).hexdigest()
    return digest[:20]


def build_request_headers_snapshot(request: Request) -> dict[str, str]:
    header_names = [
        "accept",
        "accept-language",
        "accept-encoding",
        "origin",
        "referer",
        "host",
        "x-forwarded-for",
        "x-real-ip",
        "cf-connecting-ip",
        "cf-ipcountry",
        "priority",
        "x-forwarded-proto",
        "x-forwarded-host",
        "x-forwarded-port",
        "sec-fetch-site",
        "sec-fetch-mode",
        "sec-fetch-dest",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "user-agent",
    ]
    snapshot: dict[str, str] = {}
    for name in header_names:
        value = sanitize_header_value(request.headers.get(name))
        if value:
            snapshot[name] = value
    return snapshot


def normalize_client_meta(payload: ContactRequest) -> dict[str, str]:
    raw_meta = payload.clientMeta if isinstance(payload.clientMeta, dict) else {}
    normalized: dict[str, str] = {}
    allowed_keys = {
        "timezone",
        "language",
        "languages",
        "networkType",
        "connectionType",
        "downlink",
        "rtt",
        "onlineStatus",
        "screenResolution",
        "viewportSize",
        "refererPath",
        "pageUrl",
        "referrer",
        "platform",
        "cookieEnabled",
        "touchPoints",
        "hardwareConcurrency",
        "deviceMemory",
        "colorScheme",
        "fingerprint",
    }

    for key in allowed_keys:
        value = sanitize_meta_value(raw_meta.get(key))
        if value:
            normalized[key] = value

    return normalized


def parse_positive_int(value: str, default: int = 0) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(parsed, default)


def parse_screen_resolution(value: str) -> tuple[int, int]:
    if "x" not in value:
        return (0, 0)

    width_text, height_text = value.lower().split("x", 1)
    return (parse_positive_int(width_text), parse_positive_int(height_text))


def detect_browser_engine(user_agent: str) -> dict[str, str]:
    normalized = user_agent or ""
    browser_name = "未知浏览器"
    browser_version = "-"
    engine_name = "未知内核"
    engine_version = "-"
    host_environment = "标准浏览器"

    browser_patterns = [
        ("Edge", re.compile(r"Edg/([0-9.]+)")),
        ("Chrome", re.compile(r"Chrome/([0-9.]+)")),
        ("Firefox", re.compile(r"Firefox/([0-9.]+)")),
        ("Safari", re.compile(r"Version/([0-9.]+).*Safari/")),
    ]
    engine_patterns = [
        ("Blink", re.compile(r"Chrome/([0-9.]+)|Edg/([0-9.]+)")),
        ("Gecko", re.compile(r"rv:([0-9.]+).*Gecko/")),
        ("WebKit", re.compile(r"AppleWebKit/([0-9.]+)")),
    ]

    for candidate_name, pattern in browser_patterns:
        matched = pattern.search(normalized)
        if matched:
            browser_name = candidate_name
            browser_version = next(group for group in matched.groups() if group) if matched.groups() else matched.group(1)
            break

    for candidate_name, pattern in engine_patterns:
        matched = pattern.search(normalized)
        if matched:
            engine_name = candidate_name
            engine_version = next(group for group in matched.groups() if group) if matched.groups() else matched.group(1)
            break

    if "MicroMessenger/" in normalized:
        host_environment = "微信内置浏览器"
        if "Windows" in normalized or "Macintosh" in normalized:
            host_environment += " (PC版)"
        else:
            host_environment += " (移动版)"
    elif "QQ/" in normalized or "QQBrowser/" in normalized:
        host_environment = "QQ 宿主浏览器"

    if "XWEB/" in normalized or "xweb/" in normalized:
        host_environment += " / XWEB"

    return {
        "browserName": browser_name,
        "browserVersion": browser_version,
        "engineName": engine_name,
        "engineVersion": engine_version,
        "hostEnvironment": host_environment,
    }


def infer_device_profile(user_agent: str, client_meta: dict[str, str]) -> dict[str, Any]:
    platform = client_meta.get("platform") or user_agent
    hardware_concurrency = parse_positive_int(client_meta.get("hardwareConcurrency", "0"))
    device_memory = parse_positive_int(client_meta.get("deviceMemory", "0"))
    touch_points = parse_positive_int(client_meta.get("touchPoints", "0"))
    screen_width, screen_height = parse_screen_resolution(client_meta.get("screenResolution", ""))
    short_edge = min(screen_width, screen_height) if screen_width and screen_height else 0
    ua_lower = user_agent.lower()

    is_mobile_ua = any(keyword in ua_lower for keyword in ["iphone", "android", "mobile"])
    is_tablet_ua = any(keyword in ua_lower for keyword in ["ipad", "tablet"])
    is_windows = "windows" in ua_lower
    is_mac = "macintosh" in ua_lower or "mac os x" in ua_lower

    device_type = "未知设备"
    capability_label = "常规设备"
    risk_tags: list[str] = []

    if is_tablet_ua or short_edge >= 768 and touch_points > 0 and not is_windows and not is_mac:
        device_type = "平板设备"
    elif is_mobile_ua or (touch_points > 0 and short_edge and short_edge < 768):
        device_type = "手机设备"
    elif is_windows or is_mac or "linux" in ua_lower:
        device_type = "桌面设备"

    if hardware_concurrency >= 8 or device_memory >= 8:
        capability_label = "高配"
    elif hardware_concurrency <= 2 or (0 < device_memory <= 2):
        capability_label = "低配"
    else:
        capability_label = "中配"

    if device_type == "手机设备":
        portrait = f"{capability_label}安卓手机" if "android" in ua_lower else f"{capability_label}手机"
        if "iphone" in ua_lower:
            portrait = "苹果手机"
    elif device_type == "平板设备":
        portrait = f"{capability_label}平板"
    elif is_windows:
        portrait = f"Windows {capability_label}工作站"
    elif is_mac:
        portrait = f"macOS {capability_label}工作站"
    else:
        portrait = f"{capability_label}{device_type}"

    if is_mobile_ua and touch_points == 0:
        risk_tags.append("高风险伪造设备")

    return {
        "deviceType": device_type,
        "portrait": portrait,
        "platformHint": platform or "-",
        "hardwareConcurrency": hardware_concurrency,
        "deviceMemoryGb": device_memory,
        "screenResolution": client_meta.get("screenResolution") or "-",
        "touchPoints": touch_points,
        "riskTags": risk_tags,
    }


def infer_network_profile(headers_snapshot: dict[str, str], client_meta: dict[str, str], user_agent: str) -> dict[str, Any]:
    network_type = client_meta.get("networkType") or client_meta.get("connectionType") or "unknown"
    touch_points = parse_positive_int(client_meta.get("touchPoints", "0"))
    ua_lower = user_agent.lower()
    risk_tags: list[str] = []
    red_alert_label = "无"

    if any(keyword in ua_lower for keyword in ["iphone", "android", "mobile"]) and touch_points == 0:
        risk_tags.append("高风险伪造设备")
        risk_tags.append("爬虫嫌疑")
        red_alert_label = "红色告警：高风险伪造设备/爬虫"

    return {
        "networkType": network_type,
        "downlinkMbps": client_meta.get("downlink") or "-",
        "rttMs": client_meta.get("rtt") or "-",
        "onlineStatus": client_meta.get("onlineStatus") or "-",
        "touchValidation": "触控能力正常" if touch_points > 0 else "未检测到触控输入",
        "riskTags": risk_tags,
        "redAlertLabel": red_alert_label,
        "forwardedChain": headers_snapshot.get("x-forwarded-for") or "-",
    }


def fetch_geo_location_from_ip(ip_value: str) -> dict[str, Any]:
    if not CONTACT_ENABLE_GEO_LOOKUP or not is_public_ip(ip_value):
        return {
            "status": "disabled",
            "provider": "ip-api",
            "lookupUrl": f"http://ip-api.com/json/{ip_value}?lang=zh-CN",
            "note": "已预留 GeoIP 扩展，默认关闭外部查询。",
        }

    lookup_url = (
        "http://ip-api.com/json/"
        f"{ip_value}?lang=zh-CN&fields=status,message,country,regionName,city,isp,org,as,query,timezone"
    )
    try:
        with urlopen(lookup_url, timeout=3) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        return {
            "status": "error",
            "provider": "ip-api",
            "lookupUrl": lookup_url,
            "message": str(error),
        }

    return payload if isinstance(payload, dict) else {"status": "error", "provider": "ip-api"}


def build_enriched_context(client_ip: str, user_agent: str, headers_snapshot: dict[str, str], client_meta: dict[str, str]) -> dict[str, Any]:
    browser_profile = detect_browser_engine(user_agent)
    device_profile = infer_device_profile(user_agent, client_meta)
    network_profile = infer_network_profile(headers_snapshot, client_meta, user_agent)
    all_risk_tags = list(dict.fromkeys(device_profile.get("riskTags", []) + network_profile.get("riskTags", [])))

    return {
        "summary": {
            "设备实体画像": device_profile.get("portrait"),
            "浏览器与宿主环境": (
                f"{browser_profile.get('browserName')} {browser_profile.get('browserVersion')} / "
                f"{browser_profile.get('engineName')} {browser_profile.get('engineVersion')} / "
                f"{browser_profile.get('hostEnvironment')}"
            ),
            "网络与交互能力": (
                f"网络: {network_profile.get('networkType')} / "
                f"触控: {network_profile.get('touchValidation')}"
            ),
            "异常探针预警": network_profile.get("redAlertLabel"),
            "风险提示": "，".join(all_risk_tags) if all_risk_tags else "未发现明显伪造信号",
        },
        "deviceEntityProfile": {
            "labelZh": "设备实体画像",
            "portrait": device_profile.get("portrait"),
            "deviceType": device_profile.get("deviceType"),
            "platformHint": device_profile.get("platformHint"),
            "hardwareConcurrency": device_profile.get("hardwareConcurrency"),
            "deviceMemoryGb": device_profile.get("deviceMemoryGb"),
            "screenResolution": device_profile.get("screenResolution"),
        },
        "browserHostProfile": {
            "labelZh": "浏览器与宿主环境",
            **browser_profile,
        },
        "networkInteractionProfile": {
            "labelZh": "网络与交互能力",
            **network_profile,
        },
        "riskAssessment": {
            "labelZh": "风险评估",
            "riskTags": all_risk_tags,
            "isHighRisk": "高风险伪造设备" in all_risk_tags,
            "redAlertLabel": network_profile.get("redAlertLabel"),
        },
        "geo_location": {
            "labelZh": "地理位置扩展预留",
            **fetch_geo_location_from_ip(client_ip),
        },
    }


def collect_request_context(request: Request, client_meta: dict[str, str], client_ip: str) -> dict[str, Any]:
    headers_snapshot = build_request_headers_snapshot(request)
    parsed_url = request.url
    referer = headers_snapshot.get("referer", "")
    origin = headers_snapshot.get("origin", "")
    user_agent = headers_snapshot.get("user-agent", "")

    return {
        "method": request.method,
        "path": sanitize_optional_text(parsed_url.path, limit=160),
        "query": sanitize_optional_text(parsed_url.query, limit=240),
        "scheme": sanitize_optional_text(parsed_url.scheme, limit=20),
        "host": sanitize_optional_text(parsed_url.hostname or "", limit=120),
        "port": sanitize_meta_value(parsed_url.port, limit=16),
        "clientIp": client_ip,
        "isTrustedProxyHop": is_trusted_proxy(request.client.host) if request.client and request.client.host else False,
        "referer": referer,
        "origin": origin,
        "headers": headers_snapshot,
        "clientMeta": client_meta,
        "enrichment": build_enriched_context(client_ip, user_agent, headers_snapshot, client_meta),
    }


def detect_suspicious_patterns(*values: str) -> list[str]:
    findings: list[str] = []
    combined_values = [value for value in values if value]

    for rule_name, pattern in SUSPICIOUS_PATTERN_RULES.items():
        if any(pattern.search(value) for value in combined_values):
            findings.append(rule_name)

    return findings


def build_submission_identity(ip_hash: str, user_agent: str, client_meta: dict[str, str]) -> tuple[str, str]:
    fingerprint_source = client_meta.get("fingerprint") or client_meta.get("viewportSize") or client_meta.get("screenResolution") or "meta-none"
    fingerprint_hash = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:12]
    return (
        f"identity:{ip_hash}:{fingerprint_hash}",
        f"network:{ip_hash}",
    )


def allow_rate_limit_key(key: str, capacity: float, refill_per_second: float, now_ts: float) -> bool:
    state = RATE_LIMIT_BUCKETS.get(key)
    if state is None:
        RATE_LIMIT_BUCKETS[key] = {"tokens": capacity - 1, "updatedAt": now_ts}
        return True

    elapsed = max(0.0, now_ts - float(state["updatedAt"]))
    tokens = min(capacity, float(state["tokens"]) + (elapsed * refill_per_second))
    if tokens < 1:
        state["tokens"] = tokens
        state["updatedAt"] = now_ts
        return False

    state["tokens"] = tokens - 1
    state["updatedAt"] = now_ts
    return True


def allow_contact_submission(identity_key: str, network_key: str, dedupe_key: str) -> tuple[bool, str]:
    now_ts = time.time()
    refill_per_second = CONTACT_RATE_LIMIT_MAX_REQUESTS / max(CONTACT_RATE_LIMIT_WINDOW_SECONDS, 1)
    network_capacity = max(float(CONTACT_RATE_LIMIT_MAX_REQUESTS), float(CONTACT_RATE_LIMIT_BURST) + 2.0)

    with RATE_LIMIT_LOCK:
        expired_keys = [
            key for key, created_at in RECENT_SUBMISSIONS.items()
            if now_ts - created_at > CONTACT_DUPLICATE_WINDOW_SECONDS
        ]
        for key in expired_keys:
            RECENT_SUBMISSIONS.pop(key, None)

        duplicate_at = RECENT_SUBMISSIONS.get(dedupe_key)
        if duplicate_at and now_ts - duplicate_at <= CONTACT_DUPLICATE_WINDOW_SECONDS:
            return False, "duplicate"

        if not allow_rate_limit_key(identity_key, float(CONTACT_RATE_LIMIT_BURST), refill_per_second, now_ts):
            return False, "identity"

        if not allow_rate_limit_key(network_key, network_capacity, refill_per_second, now_ts):
            return False, "network"

        RECENT_SUBMISSIONS[dedupe_key] = now_ts
        return True, "ok"


def read_message_records() -> list[dict[str, Any]]:
    init_contact_storage()
    with CONTACT_DB_LOCK:
        with open_contact_db() as connection:
            rows = connection.execute(
                "SELECT record_json FROM contact_messages ORDER BY created_at ASC, id ASC"
            ).fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(str(row["record_json"]))
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(item, dict) and item.get("id") and item.get("createdAt"):
            messages.append(item)

    return messages


def write_message_records(messages: list[dict[str, Any]]) -> None:
    init_contact_storage()
    with CONTACT_DB_LOCK:
        with open_contact_db() as connection:
            connection.execute("DELETE FROM contact_messages")
            for item in messages:
                insert_message_record(connection, item)
            connection.commit()


def append_message_record(record: dict[str, Any]) -> None:
    init_contact_storage()
    with MESSAGE_WRITE_LOCK:
        with CONTACT_DB_LOCK:
            with open_contact_db() as connection:
                insert_message_record(connection, record)
                connection.commit()


def build_message_preview(content: str, limit: int = 72) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def to_admin_message_item(record: dict[str, Any]) -> AdminMessageItem:
    return AdminMessageItem(
        id=str(record.get("id", "")),
        createdAt=str(record.get("createdAt", "")),
        status=str(record.get("status", "new")),
        wantReply=bool(record.get("wantReply", False)),
        name=str(record.get("name") or "-"),
        email=str(record.get("email") or "-"),
        phone=str(record.get("phone") or "-"),
        preview=build_message_preview(str(record.get("content") or "")),
    )


def find_message_by_id(message_id: str) -> dict[str, Any] | None:
    init_contact_storage()
    with CONTACT_DB_LOCK:
        with open_contact_db() as connection:
            row = connection.execute(
                "SELECT record_json FROM contact_messages WHERE id = ? LIMIT 1",
                (message_id,),
            ).fetchone()

    if row is None:
        return None

    try:
        payload = json.loads(str(row["record_json"]))
    except (TypeError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def mark_message_processed(message_id: str) -> str | None:
    init_contact_storage()
    with MESSAGE_WRITE_LOCK:
        with CONTACT_DB_LOCK:
            with open_contact_db() as connection:
                row = connection.execute(
                    "SELECT record_json FROM contact_messages WHERE id = ? LIMIT 1",
                    (message_id,),
                ).fetchone()
                if row is None:
                    return None

                try:
                    current = json.loads(str(row["record_json"]))
                except (TypeError, json.JSONDecodeError):
                    return None

                if str(current.get("status")) == "processed" and current.get("processedAt"):
                    return str(current.get("processedAt"))

                processed_at = datetime.now(timezone.utc).isoformat()
                current["status"] = "processed"
                current["processedAt"] = processed_at
                connection.execute(
                    """
                    UPDATE contact_messages
                    SET status = ?, processed_at = ?, preview = ?, record_json = ?
                    WHERE id = ?
                    """,
                    (
                        "processed",
                        processed_at,
                        build_message_preview(str(current.get("content") or "")),
                        json.dumps(current, ensure_ascii=False),
                        message_id,
                    ),
                )
                connection.commit()
                return processed_at


def is_smtp_configured(settings: dict[str, Any]) -> bool:
    return bool(
        settings.get("smtpHost")
        and settings.get("smtpUser")
        and settings.get("smtpPass")
        and settings.get("mailTo")
    )


def build_notification_email_body(record: dict[str, Any]) -> str:
    want_reply_text = "Yes" if record.get("wantReply") else "No"
    request_context = record.get("requestContext") if isinstance(record.get("requestContext"), dict) else {}
    client_meta = request_context.get("clientMeta") if isinstance(request_context.get("clientMeta"), dict) else {}
    headers = request_context.get("headers") if isinstance(request_context.get("headers"), dict) else {}
    enrichment = request_context.get("enrichment") if isinstance(request_context.get("enrichment"), dict) else {}
    enrichment_summary = enrichment.get("summary") if isinstance(enrichment.get("summary"), dict) else {}
    suspicious_tags = record.get("securitySignals") if isinstance(record.get("securitySignals"), list) else []
    sensitive_words = record.get("sensitiveWords") if isinstance(record.get("sensitiveWords"), list) else []
    client_meta_lines = [f"{key}: {value}" for key, value in client_meta.items()]
    header_lines = [f"{key}: {value}" for key, value in headers.items()]
    summary_lines = [f"{key}: {value}" for key, value in enrichment_summary.items()]

    return "\n".join(
        [
            "You received a new private message from the personal homepage.",
            "",
            "[Message]",
            str(record.get("content", "")),
            "",
            "[Sender]",
            f"Name: {record.get('name') or '-'}",
            f"Email: {record.get('email') or '-'}",
            f"Phone: {record.get('phone') or '-'}",
            f"Wants Reply: {want_reply_text}",
            "",
            "[Meta]",
            f"Message ID: {record.get('id')}",
            f"Created At (UTC): {record.get('createdAt')}",
            f"Client IP Hash: {record.get('ipHash')}",
            f"User Agent: {record.get('userAgent') or '-'}",
            f"Path: {request_context.get('path') or '-'}",
            f"Query: {request_context.get('query') or '-'}",
            f"Host: {request_context.get('host') or '-'}",
            f"Client IP: {request_context.get('clientIp') or '-'}",
            f"Origin: {request_context.get('origin') or '-'}",
            f"Referer: {request_context.get('referer') or '-'}",
            f"Security Signals: {', '.join(suspicious_tags) if suspicious_tags else '-'}",
            f"Sensitive Words: {', '.join(sensitive_words) if sensitive_words else '-'}",
            "",
            "[Enrichment Summary]",
            *(summary_lines or ["-"]),
            "",
            "[Client Meta]",
            *(client_meta_lines or ["-"]),
            "",
            "[Headers]",
            *(header_lines or ["-"]),
        ]
    )


def send_notification_email(record: dict[str, Any], settings: dict[str, Any]) -> None:
    if not is_smtp_configured(settings):
        return

    subject = f"{settings.get('mailSubjectPrefix', '[Personal Homepage]')} New Message {record.get('id')}"
    sender = str(settings.get("mailFrom") or settings.get("smtpUser") or "")
    body = build_notification_email_body(record)

    email_message = EmailMessage()
    email_message["From"] = sender
    email_message["To"] = str(settings.get("mailTo") or "")
    email_message["Subject"] = subject
    email_message.set_content(body)

    context = ssl.create_default_context()

    smtp_host = str(settings.get("smtpHost") or "")
    smtp_port = int(settings.get("smtpPort") or 465)
    smtp_user = str(settings.get("smtpUser") or "")
    smtp_pass = str(settings.get("smtpPass") or "")

    if bool(settings.get("smtpUseSsl", True)):
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15) as smtp:
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(email_message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.ehlo()
        if bool(settings.get("smtpUseStarttls", False)):
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(email_message)


def trigger_notification_email(record: dict[str, Any]) -> None:
    settings = read_contact_settings()

    if bool(settings.get("contactPlaceholderMode", True)):
        return

    if not is_smtp_configured(settings):
        return

    def worker() -> None:
        try:
            send_notification_email(record, settings)
        except Exception as error:  # noqa: BLE001
            logger.exception("contact email send failed")
            write_structured_contact_log(
                "mailer_error",
                {
                    "messageId": record.get("id"),
                    "errorCode": "5001",
                    "errorMessage": str(error),
                },
            )

    Thread(target=worker, daemon=True).start()


def get_section_path(section: str) -> Path:
    file_name = SECTION_FILES.get(section)
    if file_name is None:
        raise HTTPException(status_code=404, detail="Section not found.")

    return DATA_ROOT / file_name


def as_project_relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def require_admin_api_key(
    x_admin_api_key: str | None = Header(default=None, alias="X-Admin-API-Key"),
) -> None:
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=503, detail="Admin API key is not configured.")

    if not x_admin_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized.")

    if not secrets.compare_digest(x_admin_api_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized.")


def load_toml_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Content file not found.")

    try:
        with path.open("rb") as file_obj:
            content = tomllib.load(file_obj)
    except tomllib.TOMLDecodeError as error:
        raise HTTPException(status_code=500, detail=f"Invalid TOML format in {path.name}.") from error

    if not isinstance(content, dict):
        raise HTTPException(status_code=500, detail=f"Unexpected TOML structure in {path.name}.")

    return content


def validate_section_content(section: str, content: dict[str, Any]) -> None:
    if not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="Content must be a JSON object.")

    required_keys = SECTION_REQUIRED_KEYS.get(section, set())
    missing_keys = sorted(key for key in required_keys if key not in content)
    if missing_keys:
        missing_text = ", ".join(missing_keys)
        raise HTTPException(status_code=422, detail=f"Missing required keys: {missing_text}.")

    for list_key in SECTION_LIST_KEYS.get(section, ()):
        list_value = content.get(list_key)
        if list_value is None:
            if section == "writing":
                continue
            raise HTTPException(status_code=422, detail=f"{list_key} must be a list.")

        if not isinstance(list_value, list):
            raise HTTPException(status_code=422, detail=f"{list_key} must be a list.")

    if section in {"projects", "education", "achievements", "now", "writing"}:
        section_meta = content.get("section")
        if not isinstance(section_meta, dict):
            raise HTTPException(status_code=422, detail="section must be an object.")


def list_backup_paths(section: str) -> list[Path]:
    backup_dir = BACKUP_ROOT / section
    if not backup_dir.exists() or not backup_dir.is_dir():
        return []

    expected_suffix = f"-{SECTION_FILES[section]}"
    return sorted(path for path in backup_dir.glob(f"*{expected_suffix}") if path.is_file())


def prune_backups(section: str) -> None:
    backups = list_backup_paths(section)
    if len(backups) <= BACKUP_LIMIT:
        return

    for path in backups[: len(backups) - BACKUP_LIMIT]:
        path.unlink(missing_ok=True)


def create_backup(section: str, source_path: Path) -> str | None:
    if not source_path.exists() or not source_path.is_file():
        return None

    backup_dir = BACKUP_ROOT / section
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"{timestamp}-{source_path.name}"
    backup_path = backup_dir / backup_name
    shutil.copy2(source_path, backup_path)
    prune_backups(section)
    return backup_name


def atomic_write_toml(target_path: Path, content: dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = tomli_w.dumps(content)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target_path.parent),
            delete=False,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
        ) as temp_file:
            temp_file.write(payload)
            temp_path = Path(temp_file.name)

        os.replace(temp_path, target_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def read_section_content(section: str) -> tuple[Path, dict[str, Any]]:
    section_path = get_section_path(section)
    content = load_toml_file(section_path)
    validate_section_content(section, content)
    return section_path, content


def write_section_content(section: str, content: dict[str, Any]) -> tuple[str | None, str]:
    validate_section_content(section, content)
    section_path = get_section_path(section)

    with WRITE_LOCK:
        backup_name = create_backup(section, section_path)
        atomic_write_toml(section_path, content)
        updated_at = datetime.now(timezone.utc).isoformat()

    return backup_name, updated_at


def list_backups(section: str) -> list[BackupItem]:
    backup_paths = list_backup_paths(section)
    backup_items: list[BackupItem] = []

    for backup_path in reversed(backup_paths):
        stats = backup_path.stat()
        backup_items.append(
            BackupItem(
                name=backup_path.name,
                createdAt=datetime.fromtimestamp(stats.st_mtime, timezone.utc).isoformat(),
                sizeBytes=stats.st_size,
            )
        )

    return backup_items


def get_publish_state_snapshot() -> dict[str, str | None]:
    with PUBLISH_STATE_LOCK:
        return dict(PUBLISH_STATE)


def update_publish_state(
    *,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    last_error: str | None = None,
    last_output: str | None = None,
) -> None:
    with PUBLISH_STATE_LOCK:
        PUBLISH_STATE["status"] = status
        if started_at is not None:
            PUBLISH_STATE["startedAt"] = started_at
        if finished_at is not None:
            PUBLISH_STATE["finishedAt"] = finished_at
        PUBLISH_STATE["lastError"] = last_error
        PUBLISH_STATE["lastOutput"] = last_output


def publish_worker() -> None:
    if not CONTENT_PUBLISH_SCRIPT.exists() or not CONTENT_PUBLISH_SCRIPT.is_file():
        update_publish_state(
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_error=f"Publish script not found: {CONTENT_PUBLISH_SCRIPT}",
            last_output=None,
        )
        return

    try:
        result = subprocess.run(
            [str(CONTENT_PUBLISH_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        combined_output = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        update_publish_state(
            status="success",
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_error=None,
            last_output=truncate_text(combined_output) if combined_output else None,
        )
    except subprocess.CalledProcessError as error:
        combined_output = "\n".join(part for part in [error.stdout, error.stderr] if part).strip()
        update_publish_state(
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_error=f"Publish script failed with exit code {error.returncode}.",
            last_output=truncate_text(combined_output) if combined_output else None,
        )
    except Exception as error:  # noqa: BLE001
        update_publish_state(
            status="failed",
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_error=f"Unexpected publish error: {error}",
            last_output=None,
        )


def start_publish_job() -> str:
    with PUBLISH_STATE_LOCK:
        if PUBLISH_STATE["status"] == "running":
            return "running"

        started_at = datetime.now(timezone.utc).isoformat()
        PUBLISH_STATE["status"] = "running"
        PUBLISH_STATE["startedAt"] = started_at
        PUBLISH_STATE["finishedAt"] = None
        PUBLISH_STATE["lastError"] = None
        PUBLISH_STATE["lastOutput"] = None

    Thread(target=publish_worker, daemon=True).start()
    return "started"


def to_publish_status_response() -> PublishStatusResponse:
    state = get_publish_state_snapshot()
    return PublishStatusResponse(
        status=state.get("status") or "idle",
        startedAt=state.get("startedAt"),
        finishedAt=state.get("finishedAt"),
        lastError=state.get("lastError"),
        lastOutput=state.get("lastOutput"),
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "personal-homepage-api"}


@app.get(
    "/api/admin/sections",
    response_model=AdminSectionsResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_sections() -> AdminSectionsResponse:
    sections = [
        AdminSectionItem(key=key, file=str(Path("data") / file_name))
        for key, file_name in SECTION_FILES.items()
    ]
    return AdminSectionsResponse(sections=sections)


@app.get(
    "/api/admin/content/{section}",
    response_model=AdminContentResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_get_content(section: str) -> AdminContentResponse:
    section_path, content = read_section_content(section)
    return AdminContentResponse(
        section=section,
        sourceFile=as_project_relative(section_path),
        content=content,
    )


@app.put(
    "/api/admin/content/{section}",
    response_model=AdminWriteResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_update_content(section: str, payload: AdminUpdateRequest) -> AdminWriteResponse:
    backup_name, updated_at = write_section_content(section, payload.content)
    publish_status = start_publish_job() if AUTO_PUBLISH_ON_SAVE else None
    return AdminWriteResponse(
        status="saved",
        section=section,
        backup=backup_name,
        updatedAt=updated_at,
        publishStatus=publish_status,
    )


@app.get(
    "/api/admin/backups/{section}",
    response_model=AdminBackupsResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_list_backups(section: str) -> AdminBackupsResponse:
    get_section_path(section)
    return AdminBackupsResponse(section=section, backups=list_backups(section))


@app.post(
    "/api/admin/rollback/{section}/{backup_name}",
    response_model=AdminWriteResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_rollback_content(section: str, backup_name: str) -> AdminWriteResponse:
    get_section_path(section)

    if "/" in backup_name or "\\" in backup_name or backup_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid backup name.")

    expected_suffix = f"-{SECTION_FILES[section]}"
    if not backup_name.endswith(expected_suffix):
        raise HTTPException(status_code=400, detail="Backup does not belong to this section.")

    backup_path = BACKUP_ROOT / section / backup_name
    if not backup_path.exists() or not backup_path.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found.")

    backup_content = load_toml_file(backup_path)
    validate_section_content(section, backup_content)

    with WRITE_LOCK:
        section_path = get_section_path(section)
        current_backup = create_backup(section, section_path)
        atomic_write_toml(section_path, backup_content)
        updated_at = datetime.now(timezone.utc).isoformat()

    return AdminWriteResponse(
        status="rolled_back",
        section=section,
        backup=current_backup,
        updatedAt=updated_at,
        publishStatus=start_publish_job() if AUTO_PUBLISH_ON_SAVE else None,
    )


@app.post(
    "/api/admin/publish",
    response_model=PublishStatusResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_publish() -> PublishStatusResponse:
    start_publish_job()
    return to_publish_status_response()


@app.get(
    "/api/admin/publish/status",
    response_model=PublishStatusResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_publish_status() -> PublishStatusResponse:
    return to_publish_status_response()


@app.get(
    "/api/admin/messages",
    response_model=AdminMessagesResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_list_messages(status: str = "all", limit: int = 60) -> AdminMessagesResponse:
    normalized_status = status.strip().lower()
    if normalized_status not in {"all", "new", "processed"}:
        raise HTTPException(status_code=422, detail="status must be one of: all, new, processed.")

    safe_limit = max(1, min(limit, 300))
    records = list(reversed(read_message_records()))

    if normalized_status != "all":
        records = [item for item in records if str(item.get("status", "new")) == normalized_status]

    selected = records[:safe_limit]
    items = [to_admin_message_item(item) for item in selected]
    return AdminMessagesResponse(total=len(records), messages=items)


@app.get(
    "/api/admin/messages/{message_id}",
    response_model=AdminMessageDetailResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_get_message(message_id: str) -> AdminMessageDetailResponse:
    message = find_message_by_id(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    return AdminMessageDetailResponse(message=message)


@app.post(
    "/api/admin/messages/{message_id}/process",
    response_model=AdminMessageProcessResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_mark_message_processed(message_id: str) -> AdminMessageProcessResponse:
    processed_at = mark_message_processed(message_id)
    if processed_at is None:
        raise HTTPException(status_code=404, detail="Message not found.")

    return AdminMessageProcessResponse(
        status="processed",
        messageId=message_id,
        processedAt=processed_at,
    )


@app.get(
    "/api/admin/contact-settings",
    response_model=AdminContactSettingsResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_get_contact_settings() -> AdminContactSettingsResponse:
    settings = read_contact_settings()
    return to_contact_settings_response(settings)


@app.put(
    "/api/admin/contact-settings",
    response_model=AdminContactSettingsResponse,
    dependencies=[Depends(require_admin_api_key)],
)
async def admin_update_contact_settings(payload: AdminContactSettingsUpdateRequest) -> AdminContactSettingsResponse:
    current = read_contact_settings()

    next_settings = {
        "contactPlaceholderMode": bool(payload.contactPlaceholderMode),
        "smtpHost": sanitize_optional_text(payload.smtpHost, limit=200),
        "smtpPort": payload.smtpPort,
        "smtpUseSsl": bool(payload.smtpUseSsl),
        "smtpUseStarttls": bool(payload.smtpUseStarttls),
        "smtpUser": sanitize_optional_text(payload.smtpUser, limit=200),
        "smtpPass": current.get("smtpPass", ""),
        "mailFrom": sanitize_optional_text(payload.mailFrom, limit=200),
        "mailTo": sanitize_optional_text(payload.mailTo, limit=200),
        "mailSubjectPrefix": sanitize_optional_text(payload.mailSubjectPrefix, limit=200),
    }

    if payload.smtpPass is not None and payload.smtpPass.strip():
        next_settings["smtpPass"] = payload.smtpPass.strip()

    next_settings = normalize_contact_settings(next_settings)
    if next_settings["smtpPort"] < 1 or next_settings["smtpPort"] > 65535:
        raise HTTPException(status_code=422, detail="smtpPort must be between 1 and 65535.")

    write_contact_settings(next_settings)
    return to_contact_settings_response(next_settings)


@app.post("/api/contact", response_model=ContactResponse)
async def submit_contact(request: Request) -> ContactResponse:
    raw_payload = getattr(request.state, "contact_payload", None)
    if raw_payload is None:
        raise HTTPException(status_code=400, detail="Missing contact payload.")

    try:
        payload = ContactRequest.model_validate(raw_payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=json.loads(error.json())) from error

    received_at = datetime.now(timezone.utc).isoformat()
    settings = read_contact_settings()
    client_meta = normalize_client_meta(payload)
    client_ip = get_client_ip(request)
    request_context = collect_request_context(request, client_meta, client_ip)

    # 蜜罐命中时直接接受，避免给脚本明确反馈。
    if payload.website and payload.website.strip():
        return ContactResponse(status="accepted", message="Message accepted.", receivedAt=received_at)

    content = resolve_contact_content(payload)
    name = sanitize_optional_text(payload.name, limit=80)
    email = sanitize_optional_text(payload.email, limit=120)
    phone = sanitize_optional_text(payload.phone, limit=32)
    user_agent = sanitize_header_value(request.headers.get("user-agent"))
    ip_hash = hash_client_ip(client_ip)
    identity_key, network_key = build_submission_identity(ip_hash, user_agent, client_meta)
    dedupe_seed = "|".join([ip_hash, email, phone, content])
    dedupe_key = hashlib.sha256(dedupe_seed.encode("utf-8")).hexdigest()
    security_signals = detect_suspicious_patterns(content, name, email, phone)
    sensitive_words = find_sensitive_words(name, email, phone, content)

    if email and not looks_like_email(email):
        raise HTTPException(status_code=422, detail="Email format is invalid.")

    if phone and not looks_like_phone(phone):
        raise HTTPException(status_code=422, detail="Phone format is invalid.")

    if payload.wantReply and not email and not phone:
        raise HTTPException(status_code=422, detail="Email or phone is required when reply is requested.")

    if sensitive_words:
        write_structured_contact_log(
            "sensitive_content_blocked",
            {
                "ipHash": ip_hash,
                "userAgent": user_agent,
                "matchedWords": sensitive_words,
                "requestContext": request_context,
            },
        )
        raise HTTPException(status_code=422, detail="Message contains prohibited content.")

    allowed, rate_limit_reason = allow_contact_submission(identity_key, network_key, dedupe_key)
    if not allowed:
        detail = "Duplicate message detected. Please wait before sending the same content again."
        if rate_limit_reason != "duplicate":
            detail = "Too many messages from this device and network. Please try again later."

        write_structured_contact_log(
            "rate_limited_contact",
            {
                "reason": rate_limit_reason,
                "ipHash": ip_hash,
                "userAgent": user_agent,
                "email": email or "-",
                "phone": phone or "-",
                "requestContext": request_context,
            },
        )
        raise HTTPException(status_code=429, detail=detail)

    message_record = {
        "id": build_message_id(),
        "createdAt": received_at,
        "status": "new",
        "processedAt": None,
        "name": name,
        "email": email,
        "phone": phone,
        "wantReply": bool(payload.wantReply),
        "content": content,
        "ipHash": ip_hash,
        "userAgent": user_agent,
        "securitySignals": security_signals,
        "sensitiveWords": sensitive_words,
        "sanitizedFields": build_sanitized_contact_snapshot(name, email, phone, content),
        "requestContext": request_context,
    }

    try:
        CONTACT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONTACT_LOG_PATH.open("a", encoding="utf-8") as file_obj:
            file_obj.write(
                f"{received_at}\t{name or '-'}\t{email or '-'}\t{phone or '-'}\t{sanitize_for_log(content)}\n"
            )

        append_message_record(message_record)
    except OSError as error:
        logger.exception("contact persistence failed")
        write_structured_contact_log(
            "contact_persist_error",
            {
                "messageId": message_record["id"],
                "errorCode": "5002",
                "errorMessage": str(error),
                "requestContext": request_context,
            },
        )
        raise HTTPException(status_code=500, detail="5002: Message persistence failed.") from error

    trigger_notification_email(message_record)

    if bool(settings.get("contactPlaceholderMode", True)):
        response_message = "Message accepted in placeholder mode. Email notification is disabled."
    else:
        response_message = "Message received. Thank you for reaching out."

    return ContactResponse(status="accepted", message=response_message, receivedAt=received_at)
