from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

import tomli_w
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = PROJECT_ROOT / "runtime" / "contact-messages.log"
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
PUBLISH_STATE_LOCK = Lock()
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
CONTACT_PLACEHOLDER_MODE = parse_bool(os.getenv("CONTACT_PLACEHOLDER_MODE"), default=True)
ALLOW_ORIGINS = parse_origins(os.getenv("CORS_ALLOW_ORIGINS"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(DEFAULT_DATA_ROOT))).expanduser()
BACKUP_ROOT = Path(os.getenv("ADMIN_BACKUP_ROOT", str(DEFAULT_BACKUP_ROOT))).expanduser()
BACKUP_LIMIT = parse_int(os.getenv("ADMIN_BACKUP_LIMIT"), default=10)
CONTENT_PUBLISH_SCRIPT = Path(os.getenv("CONTENT_PUBLISH_SCRIPT", str(DEFAULT_PUBLISH_SCRIPT))).expanduser()
AUTO_PUBLISH_ON_SAVE = parse_bool(os.getenv("ADMIN_AUTO_PUBLISH_ON_SAVE"), default=False)

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
    name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., min_length=3, max_length=120)
    message: str = Field(..., min_length=10, max_length=2000)


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


def looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]


def sanitize_for_log(value: str) -> str:
    return " ".join(value.strip().split())


def truncate_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n... (truncated)"


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


@app.post("/api/contact", response_model=ContactResponse)
async def submit_contact(payload: ContactRequest) -> ContactResponse:
    name = sanitize_for_log(payload.name)
    email = sanitize_for_log(payload.email)
    message = sanitize_for_log(payload.message)

    if not looks_like_email(email):
        raise HTTPException(status_code=422, detail="Email format is invalid.")

    received_at = datetime.now(timezone.utc).isoformat()
    CONTACT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    with CONTACT_LOG_PATH.open("a", encoding="utf-8") as file_obj:
        file_obj.write(f"{received_at}\t{name}\t{email}\t{message}\n")

    if CONTACT_PLACEHOLDER_MODE:
        response_message = "Message received in placeholder mode. Delivery is not enabled yet."
    else:
        response_message = "Message received. Delivery workflow will be attached in your next iteration."

    return ContactResponse(status="accepted", message=response_message, receivedAt=received_at)
