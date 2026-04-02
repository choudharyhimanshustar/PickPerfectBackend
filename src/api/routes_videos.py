from fastapi import APIRouter, logger
from src.core.database import mongodb
from uuid import uuid4
import os
from datetime import datetime
import boto3
from botocore.exceptions import NoCredentialsError
from pydantic import BaseModel
from src.app_celery.tasks import process_music_video
from src.database.schemas.auth import get_current_user
from fastapi import Depends, HTTPException
import mimetypes
import subprocess

router = APIRouter()
bucket_name = os.getenv("AWS_S3_BUCKET")

# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

class S3UploadEvent(BaseModel):
    status: str
    bucket: str
    key: str
    
@router.post("/generate-presigned-url")
async def generate_presigned_url(
    payload: dict,
    user_id: str = Depends(get_current_user)
):
    try:
        video_name = payload.get("videoName")
        thumbnail_name = payload.get("thumbnailName")

        if not video_name:
            raise HTTPException(status_code=400, detail="videoName required")

        video_id = f"vid_{uuid4().hex}"

        video_extension = video_name.split(".")[-1]
        video_s3_key = f"videos/{user_id}/{video_id}.{video_extension}"

        thumbnail_s3_key = None
        if thumbnail_name:
            thumb_extension = thumbnail_name.split(".")[-1]
            thumbnail_s3_key = f"thumbnails/{user_id}/{video_id}.{thumb_extension}"

        # 🔹 Create metadata doc (thumbnail may be None)
        metadata_doc = {
            "_id": video_id,
            "user_id": user_id,
            "original_filename": video_name,
            "video_s3_key": video_s3_key,
            "thumbnail_s3_key": thumbnail_s3_key,
            "status": "PENDING_UPLOAD",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        await mongodb.db["videos"].insert_one(metadata_doc)

        # 🔹 Generate video presigned URL
        video_presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": video_s3_key,
                "ContentType": "video/mp4"
            },
            ExpiresIn=3600
        )

        response = {
            "video": {
                "key": video_s3_key,
                "url": video_presigned_url
            }
        }

        # 🔹 Generate thumbnail presigned URL if needed
        if thumbnail_s3_key:
            content_type, _ = mimetypes.guess_type(thumbnail_s3_key)
            print(f"Generated content type for thumbnail: {content_type}")
            thumb_presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": bucket_name,
                    "Key": thumbnail_s3_key,
                    "ContentType": content_type or "image/jpeg"
                },
                ExpiresIn=3600
            )

            response["thumbnail"] = {
                "key": thumbnail_s3_key,
                "url": thumb_presigned_url
            }

        return response

    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not found")
    
@router.get("/{video_id}/analysis")    
async def get_video_analysis(video_id: str, user_id: str = Depends(get_current_user)):
    video = await mongodb.db["videos"].find_one(
        {"_id": video_id, "user_id": user_id}
    )

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if video.get("status") != "processed":
        raise HTTPException(
            status_code=400,
            detail=f"Analysis not ready. Current status: {video.get('status')}",
        )

    analysis = video.get("analysis")
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis data found for this video")

    return {
        "video_id": video_id,
        "original_filename": video.get("original_filename"),
        "status": video.get("status"),
        "analyzed_at": video.get("analyzed_at"),
        "analysis": analysis,
    }

    
