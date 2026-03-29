from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "runtime" / "contact-messages.log"


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_origins(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


CONTACT_LOG_PATH = Path(os.getenv("CONTACT_LOG_PATH", str(DEFAULT_LOG_PATH))).expanduser()
CONTACT_PLACEHOLDER_MODE = parse_bool(os.getenv("CONTACT_PLACEHOLDER_MODE"), default=True)
ALLOW_ORIGINS = parse_origins(os.getenv("CORS_ALLOW_ORIGINS"))

if not ALLOW_ORIGINS:
    ALLOW_ORIGINS = ["http://127.0.0.1:1313", "http://localhost:1313"]

app = FastAPI(title="Personal Homepage API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
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


def looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]


def sanitize_for_log(value: str) -> str:
    return " ".join(value.strip().split())


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "personal-homepage-api"}


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
