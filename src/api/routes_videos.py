from fastapi import APIRouter, logger,Query
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
from typing import List
import asyncio
import json 
from sse_starlette.sse import EventSourceResponse
from src.utils.video_helpers import get_presigned_download_url

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
    
 
@router.get("/thumbnails/progress")
async def thumbnail_progress(
    video_ids: List[str] = Query(...),  # frontend sends only the "processing" ones
    user_id: str = Depends(get_current_user),
):
    async def event_stream():
        pending_ids = set(video_ids)
        
        # ✅ ADD PING HERE — before the while loop, before any sleep
        yield {"event": "ping", "data": json.dumps({"message": "stream connected"})}
        try:
            while pending_ids:
                await asyncio.sleep(3)  # server-side check every 3 seconds

                # Single DB query for all still-pending videos
                newly_ready = await mongodb.db["videos"].find({
                    "_id": {"$in": list(pending_ids)},
                    "user_id": user_id,                          # security: scoped to user
                    "thumbnail_s3_key": {"$exists": True, "$ne": None}
                }).to_list(length=None)

                print(f"Checked thumbnail status for {len(pending_ids)} videos, {len(newly_ready)} newly ready")
                for video in newly_ready:
                    vid_id = str(video["_id"])
                    thumbnail_url = get_presigned_download_url(
                        s3_client, bucket_name, video["thumbnail_s3_key"]
                    )

                    yield {
                        "event": "thumbnail_ready",
                        "data": json.dumps({
                            "video_id": vid_id,
                            "thumbnail_url": thumbnail_url,
                            "thumbnail_status": "ready"
                        })
                    }

                    pending_ids.remove(vid_id)  # drop from pending, shrinks every cycle

            # All thumbnails are ready, signal the frontend to close the connection
            yield {"event": "done", "data": json.dumps({"message": "all thumbnails ready"})}

        except Exception as e:
            print(f"ERROR in event_stream loop: {e!r}")
            raise HTTPException(status_code=500, detail="Internal server error in thumbnail progress stream")

    return EventSourceResponse(
        event_stream(),
        headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",          # ← kills nginx/proxy buffering
        "Access-Control-Allow-Origin": "http://localhost:3000",
        "Access-Control-Allow-Credentials": "true",
        }
    )

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

   