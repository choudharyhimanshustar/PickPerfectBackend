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
    video_s3_key: str                     # was wrongly named s3_key
    thumbnail_s3_key: Optional[str] = None

    status: str = Field(default="pending")  # your db uses "processed", not "READY"

    analysis: Optional[Any] = None        # was missing
    analyzed_at: Optional[datetime] = None  # was missing
    thumbnail_requested_at: Optional[datetime] = None 
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # removed: mime_type, file_size, duration, resolution, fps (not in your db)

    model_config = {"populate_by_name": True}  # allows both _id and id
    
class VideoStatus(str, Enum):
    pending_upload = "pending_upload"   # video uploading to S3
    pending        = "pending"          # uploaded, awaiting processing  
    processed      = "processed"        # celery done
    failed         = "failed"           # timed out / lost
    ready          = "READY"            # thumbnail ready
    
async def get_video(video_id: str, user_id: str) -> VideoMetadata:
    raw = await mongodb.db["videos"].find_one({"_id": video_id, "user_id": user_id})
    if not raw:
        raise HTTPException(status_code=404, detail="Video not found")
    return VideoMetadata(**raw)