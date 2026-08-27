from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from . import auth, db, tenancy
from .config import Settings, get_settings
from .model_catalog import CONTENT_MODEL_CHOICES

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError("Not a valid email address.")
    return value.lower()


class SignupRequest(BaseModel):
    company_name: str = Field(min_length=1)
    email: str
    password: str = Field(min_length=8)

    _validate_email = field_validator("email")(_validate_email)


class LoginRequest(BaseModel):
    email: str
    password: str

    _validate_email = field_validator("email")(_validate_email)


class ScheduleRequest(BaseModel):
    enabled: bool
    hour_utc: int | None = Field(default=None, ge=0, le=23)
    minute_utc: int | None = Field(default=None, ge=0, le=59)


class BrandVoiceRequest(BaseModel):
    brand_voice: str = Field(default="", max_length=2000)


class ContentModelRequest(BaseModel):
    content_model: str = Field(default="")

    @field_validator("content_model")
    @classmethod
    def _validate_content_model(cls, value: str) -> str:
        if value and value not in CONTENT_MODEL_CHOICES:
            raise ValueError(f"Unknown content_model: {value!r}.")
        return value


IMAGE_SIZE_CHOICES = ("1024x1024", "1024x1536", "1536x1024", "auto")
IMAGE_QUALITY_CHOICES = ("low", "medium", "high", "auto")


class ContentSettingsRequest(BaseModel):
    content_language: str = Field(default="", max_length=100)
    target_duration_seconds: int | None = Field(default=None, ge=10, le=180)
    image_size: str = Field(default="")
    image_quality: str = Field(default="")

    @field_validator("image_size")
    @classmethod
    def _validate_image_size(cls, value: str) -> str:
        if value and value not in IMAGE_SIZE_CHOICES:
            raise ValueError(f"Unknown image_size: {value!r}.")
        return value

    @field_validator("image_quality")
    @classmethod
    def _validate_image_quality(cls, value: str) -> str:
        if value and value not in IMAGE_QUALITY_CHOICES:
            raise ValueError(f"Unknown image_quality: {value!r}.")
        return value


class InviteRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    role: str = "member"

    _validate_email = field_validator("email")(_validate_email)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        if value not in {"member", "owner"}:
            raise ValueError("role must be 'member' or 'owner'.")
        return value


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=settings.session_ttl_days * 86400,
    )


def _company_payload(company_row) -> dict:
    return {
        "slug": company_row["slug"],
        "name": company_row["name"],
        "daily_run_enabled": bool(company_row["daily_run_enabled"]),
        "daily_run_hour_utc": company_row["daily_run_hour_utc"],
        "daily_run_minute_utc": company_row["daily_run_minute_utc"],
        "brand_voice": company_row["brand_voice"],
        "content_model": company_row["content_model"],
        "content_language": company_row["content_language"],
        "target_duration_seconds": company_row["target_duration_seconds"],
        "image_size": company_row["image_size"],
        "image_quality": company_row["image_quality"],
    }


@router.post("/signup")
def signup(
    body: SignupRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    if db.get_user_by_email(settings.database_path, body.email) is not None:
        raise HTTPException(status_code=400, detail="Email is already registered.")

    slug = tenancy.slugify(settings.database_path, body.company_name)
    company_id = db.insert_company(settings.database_path, slug=slug, name=body.company_name)

    password_hash, salt = auth.hash_password(body.password, iterations=settings.pbkdf2_iterations)
    user_id = db.insert_user(
        settings.database_path,
        company_id=company_id,
        email=body.email,
        password_hash=password_hash,
        password_salt=salt,
        role="owner",
    )

    token, _ = auth.create_session(settings, user_id=user_id, company_id=company_id)
    _set_session_cookie(response, settings, token)
    company = db.get_company_by_id(settings.database_path, company_id)
    return {"email": body.email, "role": "owner", "company": _company_payload(company)}


@router.post("/login")
def login(
    body: LoginRequest, response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    user = db.get_user_by_email(settings.database_path, body.email)
    if user is None or not auth.verify_password(
        body.password,
        hash_hex=user["password_hash"],
        salt_hex=user["password_salt"],
        iterations=settings.pbkdf2_iterations,
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    token, _ = auth.create_session(settings, user_id=user["id"], company_id=user["company_id"])
    _set_session_cookie(response, settings, token)
    company = db.get_company_by_id(settings.database_path, user["company_id"])
    return {"email": user["email"], "role": user["role"], "company": _company_payload(company)}


@router.post("/logout")
def logout(
    response: Response,
    request_user: auth.CurrentUser | None = Depends(auth.get_current_user_optional),
    settings: Settings = Depends(get_settings),
) -> dict:
    response.delete_cookie(settings.session_cookie_name)
    return {"ok": True}


@router.get("/me")
def me(
    current: auth.CurrentUser | None = Depends(auth.get_current_user_optional),
    settings: Settings = Depends(get_settings),
) -> dict | None:
    if current is None:
        return None
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return {
        "email": current.email,
        "role": current.role,
        "company": _company_payload(company),
    }


@router.put("/me/schedule")
def update_schedule(
    body: ScheduleRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    if body.enabled and (body.hour_utc is None or body.minute_utc is None):
        raise HTTPException(
            status_code=400, detail="hour_utc and minute_utc are required when enabling scheduling."
        )
    db.update_company_schedule(
        settings.database_path,
        current.company_id,
        enabled=body.enabled,
        hour_utc=body.hour_utc,
        minute_utc=body.minute_utc,
    )
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return _company_payload(company)


@router.put("/me/brand-voice")
def update_brand_voice(
    body: BrandVoiceRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    db.update_company_brand_voice(
        settings.database_path, current.company_id, body.brand_voice.strip() or None
    )
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return _company_payload(company)


@router.get("/content-model-choices")
def content_model_choices(current: auth.CurrentUser = Depends(auth.get_current_user)) -> dict:
    """Any authenticated user - the curated OpenRouter model picker options
    for `/auth/me/content-model`."""
    return CONTENT_MODEL_CHOICES


@router.put("/me/content-model")
def update_content_model(
    body: ContentModelRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    db.update_company_content_model(
        settings.database_path, current.company_id, body.content_model or None
    )
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return _company_payload(company)


@router.get("/content-settings-choices")
def content_settings_choices(
    current: auth.CurrentUser = Depends(auth.get_current_user),
) -> dict:
    """Any authenticated user - the valid `image_size`/`image_quality`
    values for `/auth/me/content-settings`."""
    return {"image_size": list(IMAGE_SIZE_CHOICES), "image_quality": list(IMAGE_QUALITY_CHOICES)}


@router.put("/me/content-settings")
def update_content_settings(
    body: ContentSettingsRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    db.update_company_content_settings(
        settings.database_path,
        current.company_id,
        content_language=body.content_language.strip() or None,
        target_duration_seconds=body.target_duration_seconds,
        image_size=body.image_size or None,
        image_quality=body.image_quality or None,
    )
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return _company_payload(company)


@router.get("/team")
def team(
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    rows = db.list_users_for_company(settings.database_path, current.company_id)
    return [
        {"email": row["email"], "role": row["role"], "created_at": row["created_at"]} for row in rows
    ]


@router.post("/invite")
def invite(
    body: InviteRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Owner-only, rough scaffold: no email is sent (this app has no mail
    integration) - the owner sets a password directly and shares it with
    the teammate out of band. A real invite-link/email flow is a natural
    follow-up once the app needs it."""
    if db.get_user_by_email(settings.database_path, body.email) is not None:
        raise HTTPException(status_code=400, detail="Email is already registered.")

    password_hash, salt = auth.hash_password(body.password, iterations=settings.pbkdf2_iterations)
    db.insert_user(
        settings.database_path,
        company_id=current.company_id,
        email=body.email,
        password_hash=password_hash,
        password_salt=salt,
        role=body.role,
    )
    return {"email": body.email, "role": body.role}


__all__ = ["router"]
