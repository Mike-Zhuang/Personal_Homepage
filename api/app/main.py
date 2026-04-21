from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
import hashlib
import json
import re
import smtplib
import ssl
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from threading import Lock, Thread
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import tomli_w
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = PROJECT_ROOT / "runtime" / "contact-messages.log"
DEFAULT_MESSAGES_PATH = PROJECT_ROOT / "runtime" / "messages" / "messages.jsonl"
DEFAULT_CONTACT_SETTINGS_PATH = PROJECT_ROOT / "runtime" / "messages" / "contact-settings.json"
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
RATE_LIMIT_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
PHONE_PATTERN = re.compile(r"^[0-9+\-\s()]{6,32}$")
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
CONTACT_SETTINGS_PATH = Path(os.getenv("CONTACT_SETTINGS_PATH", str(DEFAULT_CONTACT_SETTINGS_PATH))).expanduser()
CONTACT_PLACEHOLDER_MODE = parse_bool(os.getenv("CONTACT_PLACEHOLDER_MODE"), default=True)
CONTACT_RATE_LIMIT_WINDOW_SECONDS = parse_int(os.getenv("CONTACT_RATE_LIMIT_WINDOW_SECONDS"), default=300)
CONTACT_RATE_LIMIT_MAX_REQUESTS = parse_int(os.getenv("CONTACT_RATE_LIMIT_MAX_REQUESTS"), default=6)
CONTACT_IP_HASH_SALT = os.getenv("CONTACT_IP_HASH_SALT", "")
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

app = FastAPI(title="Personal Homepage API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


class ContactRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    wantReply: bool = False
    content: str | None = Field(default=None, max_length=2000)
    # 兼容旧请求字段，后续将由 content 统一承载留言正文
    message: str | None = Field(default=None, max_length=2000)
    # 蜜罐字段：真实用户页面不会填写
    website: str | None = Field(default=None, max_length=120)


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
    return "@" in value and "." in value.split("@")[-1]


def looks_like_phone(value: str) -> bool:
    return bool(PHONE_PATTERN.fullmatch(value))


def sanitize_for_log(value: str) -> str:
    return " ".join(value.strip().split())


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
    content = sanitize_optional_text(candidate, limit=2000)

    if len(content) < 3:
        raise HTTPException(status_code=422, detail="Message content is required.")

    return content


def build_message_id() -> str:
    now_part = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    token_part = secrets.token_hex(4)
    return f"msg_{now_part}_{token_part}"


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def hash_client_ip(ip_value: str) -> str:
    salt = CONTACT_IP_HASH_SALT or ADMIN_API_KEY or "contact-ip-salt"
    digest = hashlib.sha256(f"{salt}:{ip_value}".encode("utf-8")).hexdigest()
    return digest[:20]


def allow_contact_submission(client_ip: str) -> bool:
    now_ts = time.time()

    with RATE_LIMIT_LOCK:
        bucket = RATE_LIMIT_BUCKETS[client_ip]
        threshold = now_ts - CONTACT_RATE_LIMIT_WINDOW_SECONDS

        while bucket and bucket[0] < threshold:
            bucket.popleft()

        if len(bucket) >= CONTACT_RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now_ts)
        return True


def read_message_records() -> list[dict[str, Any]]:
    if not CONTACT_MESSAGES_PATH.exists() or not CONTACT_MESSAGES_PATH.is_file():
        return []

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


def write_message_records(messages: list[dict[str, Any]]) -> None:
    CONTACT_MESSAGES_PATH.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(CONTACT_MESSAGES_PATH.parent),
            delete=False,
            prefix=f".{CONTACT_MESSAGES_PATH.name}.",
            suffix=".tmp",
        ) as temp_file:
            for item in messages:
                temp_file.write(json.dumps(item, ensure_ascii=False))
                temp_file.write("\n")

            temp_path = Path(temp_file.name)

        os.replace(temp_path, CONTACT_MESSAGES_PATH)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def append_message_record(record: dict[str, Any]) -> None:
    CONTACT_MESSAGES_PATH.parent.mkdir(parents=True, exist_ok=True)

    with MESSAGE_WRITE_LOCK:
        with CONTACT_MESSAGES_PATH.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False))
            file_obj.write("\n")


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
    for record in read_message_records():
        if str(record.get("id")) == message_id:
            return record
    return None


def mark_message_processed(message_id: str) -> str | None:
    with MESSAGE_WRITE_LOCK:
        messages = read_message_records()
        target_index = -1

        for index, record in enumerate(messages):
            if str(record.get("id")) == message_id:
                target_index = index
                break

        if target_index < 0:
            return None

        current = messages[target_index]
        if str(current.get("status")) == "processed" and current.get("processedAt"):
            return str(current.get("processedAt"))

        processed_at = datetime.now(timezone.utc).isoformat()
        current["status"] = "processed"
        current["processedAt"] = processed_at
        messages[target_index] = current
        write_message_records(messages)
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
            print(f"[contact] email send failed: {error}")

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
async def submit_contact(payload: ContactRequest, request: Request) -> ContactResponse:
    received_at = datetime.now(timezone.utc).isoformat()
    settings = read_contact_settings()

    # 蜜罐命中时直接接受，避免给脚本明确反馈。
    if payload.website and payload.website.strip():
        return ContactResponse(status="accepted", message="Message accepted.", receivedAt=received_at)

    content = resolve_contact_content(payload)
    name = sanitize_optional_text(payload.name, limit=80)
    email = sanitize_optional_text(payload.email, limit=120)
    phone = sanitize_optional_text(payload.phone, limit=32)

    if email and not looks_like_email(email):
        raise HTTPException(status_code=422, detail="Email format is invalid.")

    if phone and not looks_like_phone(phone):
        raise HTTPException(status_code=422, detail="Phone format is invalid.")

    client_ip = get_client_ip(request)
    if not allow_contact_submission(client_ip):
        raise HTTPException(status_code=429, detail="Too many messages. Please try again later.")

    CONTACT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONTACT_LOG_PATH.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{received_at}\t{name or '-'}\t{email or '-'}\t{phone or '-'}\t{content}\n")

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
        "ipHash": hash_client_ip(client_ip),
        "userAgent": sanitize_optional_text(request.headers.get("user-agent"), limit=280),
    }

    append_message_record(message_record)
    trigger_notification_email(message_record)

    if bool(settings.get("contactPlaceholderMode", True)):
        response_message = "Message accepted in placeholder mode. Email notification is disabled."
    else:
        response_message = "Message received. Thank you for reaching out."

    return ContactResponse(status="accepted", message=response_message, receivedAt=received_at)
