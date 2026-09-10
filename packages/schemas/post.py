"""Post / comment API schemas."""

from __future__ import annotations

from datetime import datetime

from common.enums import PostVisibility
from pydantic import BaseModel, ConfigDict, Field


class PostCreate(BaseModel):
    author_id: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=5000)
    visibility: PostVisibility = PostVisibility.PUBLIC


class PostUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    visibility: PostVisibility | None = None


class PostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    content: str
    visibility: str
    created_at: datetime
    updated_at: datetime


class CommentCreate(BaseModel):
    user_id: int = Field(gt=0)
    content: str = Field(min_length=1, max_length=2000)


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    user_id: int
    content: str
    created_at: datetime
