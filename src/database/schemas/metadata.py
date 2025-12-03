from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class VideoMetadata(BaseModel):
    id: str = Field(..., alias="_id")
    original_filename: str
    s3_key: str

    status: str = Field(default="PENDING_UPLOAD")  # PENDING_UPLOAD | READY | FAILED
    mime_type: Optional[str] = None
    file_size: Optional[int] = None   # bytes
    duration: Optional[float] = None  # seconds
    resolution: Optional[str] = None  # "1280x720"
    fps: Optional[float] = None

    thumbnail_s3_key: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
