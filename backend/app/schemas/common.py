"""Shared pydantic response models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


class JobRef(BaseModel):
    job_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    result: Optional[Any] = None
    error: Optional[str] = None


class Ok(BaseModel):
    ok: bool = True
    detail: Optional[str] = None
