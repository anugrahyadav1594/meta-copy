"""User / profile / follow API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)


class UserUpdate(BaseModel):
    # Minimal demo update surface; passwords are never updated via this DTO.
    username: str | None = Field(default=None, min_length=3, max_length=50)
    email: EmailStr | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    created_at: datetime
    updated_at: datetime


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    display_name: str
    bio: str | None
    avatar_media_id: int | None


class FollowerRead(BaseModel):
    """One row of a followers list (follower edge, enriched for the API)."""

    model_config = ConfigDict(from_attributes=True)

    follower_id: int
    following_id: int
    created_at: datetime
