from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime
from src.core.database import mongodb
from fastapi import Depends, HTTPException
from enum import Enum

class VideoMetadata(BaseModel):
    id: str = Field(..., alias="_id")
    user_id: str                          # was missing
    original_filename: str
    title: Optional[str] = None           # user-supplied; defaults to filename
    description: Optional[str] = None      # user-supplied; optional
    video_s3_key: str                     # was wrongly named s3_key
    thumbnail_s3_key: Optional[str] = None

    # Processing lifecycle (analysis pipeline). See VideoStatus.
    status: str = Field(default="pending_upload")
    # Thumbnail lifecycle — tracked independently of processing. See ThumbnailStatus.
    thumbnail_status: str = Field(default="pending")

    analysis: Optional[Any] = None        # was missing
    analyzed_at: Optional[datetime] = None  # was missing
    thumbnail_requested_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # removed: mime_type, file_size, duration, resolution, fps (not in your db)

    model_config = {"populate_by_name": True}  # allows both _id and id

# ── Canonical status vocabularies — the single source of truth ────────────
# Two INDEPENDENT concerns, one field each. Every write in the codebase must
# go through these enums so string comparisons never silently miss.

class VideoStatus(str, Enum):
    """Processing / analysis lifecycle (the `status` field)."""
    awaiting_upload = "awaiting_upload"  # presigned URL issued, S3 upload not yet confirmed
    pending_upload  = "pending_upload"   # upload confirmed, awaiting processing
    processing      = "processing"       # analysis pipeline running
    processed       = "processed"        # analysis complete
    failed          = "failed"           # processing failed / timed out

class ThumbnailStatus(str, Enum):
    """Thumbnail lifecycle (the `thumbnail_status` field)."""
    pending = "pending"                 # not generated yet
    ready   = "ready"                   # generated + uploaded
    failed  = "failed"                  # generation failed (retryable)
    
async def get_video(video_id: str, user_id: str) -> VideoMetadata:
    raw = await mongodb.db["videos"].find_one({"_id": video_id, "user_id": user_id})
    if not raw:
        raise HTTPException(status_code=404, detail="Video not found")
    return VideoMetadata(**raw)