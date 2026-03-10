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
        raise HTTPException(status_code=500, detail="AWS credentials not found")@router.get("/{video_id}/analysis")
    
    
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


@router.post("/webhook")
async def video_upload_webhook(event: S3UploadEvent):
    print("Webhook received:", event)

    await mongodb.db["videos"].update_one({"s3_key": event.key}, {"$set": {"status": event.status}})
    print(f"Updated video with key {event.key} to status {event.status}")
     #  Trigger Celery task (non-blocking)
    task = process_music_video.delay( event.key)
    print("Triggered Celery task:", task)
    # print(f"Celery task ID: {task.video_id}")

    return {
        "message": "Webhook processed, background processing started",
        # "task_id": task.video_id
    }
    
@router.post("/video-upload-complete")
async def video_upload_complete(
    payload: dict,
    user_id: str = Depends(get_current_user)
):
    video_id = payload.get("video_id")

    if not video_id:
        raise HTTPException(status_code=400, detail="video_id required")

    video = await mongodb.db["videos"].find_one({
        "video_s3_key": video_id,
        "user_id": user_id
    })

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # If thumbnail already uploaded, skip generation
    if video.get("thumbnail_s3_key"):
        return {"message": "Thumbnail already exists"}

    video_key = video["video_s3_key"]
    video_filename = os.path.basename(video_key)
    video_name = os.path.splitext(video_filename)[0]

    video_local = f"/tmp/{video_filename}"
    thumb_local = f"/tmp/{video_name}.png"


    try:

        # 1️⃣ download video from S3
        s3_client.download_file(bucket_name, video_key, video_local)

        # 2️⃣ generate thumbnail using ffmpeg
        subprocess.run([
            "ffmpeg",
            "-ss", "00:00:01",
            "-i", video_local,
            "-frames:v", "1",
            "-update", "1",
            "-q:v", "2",
            thumb_local
        ], check=True)

        # 3️⃣ upload thumbnail to S3
        thumbnail_key = video_key.replace("videos/", "thumbnails/").replace(".mp4", ".png")

        s3_client.upload_file(
            thumb_local,
            bucket_name,
            thumbnail_key,
            ExtraArgs={"ContentType": "image/png"}
        )

        # 4️⃣ update DB
        await mongodb.db["videos"].update_one(
            {"video_s3_key": video_key},
            {
                "$set": {
                    "thumbnail_s3_key": thumbnail_key,
                    "updated_at": datetime.utcnow(),
                    "status": "READY"
                }
            }
        )

        return {
            "message": "Thumbnail generated successfully",
            "thumbnail_key": thumbnail_key
        }

    finally:
        # cleanup temp files
        if os.path.exists(video_local):
            os.remove(video_local)

        if os.path.exists(thumb_local):
            os.remove(thumb_local)