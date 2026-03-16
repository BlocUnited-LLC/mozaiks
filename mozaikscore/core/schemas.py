# ==============================================================================
# FILE: mozaikscore/core/schemas.py
# DESCRIPTION: Pydantic request/response models for mozaikscore API routes.
# ==============================================================================
from typing import Any, Optional
from pydantic import BaseModel, Field


class ChangeThemeRequest(BaseModel):
    theme_name: str = Field(..., min_length=1, max_length=64)


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    avatar_url: Optional[str] = Field(None, max_length=512)
    bio: Optional[str] = Field(None, max_length=1024)

    class Config:
        extra = "allow"  # Accept app-specific profile fields


class UpdateSubscriptionRequest(BaseModel):
    new_plan: str = Field(..., min_length=1, max_length=64)


class NotificationPreferencesRequest(BaseModel):
    class Config:
        extra = "allow"  # Preferences are dynamic per-module


class ModuleExecuteRequest(BaseModel):
    action: Optional[str] = None

    class Config:
        extra = "allow"  # Module payloads are free-form
