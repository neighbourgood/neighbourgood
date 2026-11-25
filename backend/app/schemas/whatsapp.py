"""Schemas for the WhatsApp integration."""

import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.auth import validate_password_strength


class WhatsAppLinkStart(BaseModel):
    code: str
    message: str            # text to send to the bot, e.g. "link K7Q2M9XD"
    bot_url: str | None     # wa.me link with the message prefilled, when the bot number is known
    expires_at: datetime.datetime


class WhatsAppSettingsUpdate(BaseModel):
    whatsapp_notify: bool


class CommunityWhatsAppStatus(BaseModel):
    configured: bool
    linked: bool
    digest: str


class CommunityWhatsAppUpdate(BaseModel):
    digest: Literal["off", "daily", "weekly"]


class BridgeStatus(BaseModel):
    configured: bool
    status: str
    bot_number: str | None = None


class BridgeQr(BaseModel):
    mimetype: str
    data: str


class ClaimInfo(BaseModel):
    display_name: str


class ClaimAccount(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        return validate_password_strength(v)
