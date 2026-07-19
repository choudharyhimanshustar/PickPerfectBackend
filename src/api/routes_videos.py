from fastapi import APIRouter, logger,Query
from src.core.database import mongodb
from uuid import uuid4
import os
from datetime import datetime
import boto3
from botocore.exceptions import NoCredentialsError
from pydantic import BaseModel
from src.database.schemas.auth import get_current_user
from fastapi import Depends, HTTPException
import mimetypes
import subprocess
from typing import List
import asyncio
import json 
from sse_starlette.sse import EventSourceResponse
from src.utils.video_helpers import get_presigned_download_url
from src.database.schemas.metadata import get_video
from src.database.schemas.metadata import VideoStatus
import logging
from src.app_celery.tasks import  generate_thumbnail_task
from fastapi import WebSocket, WebSocketDisconnect
from src.database.schemas.auth import get_current_user_ws
from src.api.websocket_manager import manager
import redis.asyncio as aioredis

router = APIRouter()
bucket_name = os.getenv("AWS_S3_BUCKET")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ALLOWED_ORIGINS = ["http://localhost:3000"]
MAX_MESSAGE_BYTES = 256        # ping is only 19 bytes, 256 is generous
MAX_MESSAGES_PER_MINUTE = 60  # one per second on average is plenty
ALLOWED_MESSAGE_TYPES = {"__ping__"}

logger = logging.getLogger(__name__)
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
            "thumbnail_requested_at": datetime.utcnow(), 
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
    video = await get_video(video_id, user_id)

    if video.status != "processed":
        raise HTTPException(400, detail=f"Analysis not ready. Current status: {video.status}")

    return {
        "video_id": video.id,
        "original_filename": video.original_filename,
        "status": video.status,
        "analyzed_at": video.analyzed_at,
        "analysis": video.analysis,
    }

   
@router.get("/thumbnails/progress")
async def thumbnail_progress(
    video_ids: List[str] = Query(...),
    user_id: str = Depends(get_current_user),
):
    async def event_stream():
        pending_ids = set(video_ids)

        yield {"event": "ping", "data": json.dumps({"message": "stream connected"})}

        try:
            while pending_ids:
                await asyncio.sleep(3)

                videos = await mongodb.db["videos"].find({
                    "_id": {"$in": list(pending_ids)},
                    "user_id": user_id,
                    "thumbnail_s3_key": {"$exists": True, "$ne": None}
                }).to_list(length=None)

                for video in videos:
                    vid_id = str(video["_id"])
                    status = video["status"]
                    print(f"Checking video {vid_id}: status={status}")
                    if status == VideoStatus.failed:
                        # emit failed event — frontend shows retry button
                        yield {
                            "event": "thumbnail_failed",
                            "data": json.dumps({
                                "video_id": vid_id,
                                "thumbnail_status": "failed",
                            })
                        }

                    elif status == VideoStatus.ready and video.get("thumbnail_s3_key"):
                        thumbnail_url = get_presigned_download_url(
                            s3_client, bucket_name, video["thumbnail_s3_key"]
                        )
                        print(f"Thumbnail ready for video {vid_id}, emitting event with URL: {thumbnail_url}")
                        # logger.info(f"Thumbnail ready for video {vid_id}, emitting event with URL: {thumbnail_url}")
                        yield {
                            "event": "thumbnail_ready",
                            "data": json.dumps({
                                "video_id": vid_id,
                                "thumbnail_url": thumbnail_url,
                                "thumbnail_status": "ready",
                            })
                        }

                    pending_ids.remove(vid_id)  # remove regardless — ready or failed

                print(f"[SSE] {len(pending_ids)} videos still pending")

            yield {"event": "done", "data": json.dumps({"message": "all videos settled"})}

        except Exception as e:
            print(f"ERROR in event_stream: {e!r}")
            yield {"event": "error", "data": json.dumps({"message": "stream error"})}

    return EventSourceResponse(
        event_stream(),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "http://localhost:3000",
            "Access-Control-Allow-Credentials": "true",
        }
    )
    
@router.post("/{video_id}/retry-thumbnail")
async def retry_thumbnail(
    video_id: str,
    user_id: str = Depends(get_current_user)
):
    video = await get_video(video_id, user_id)

    if video.status != VideoStatus.failed:
        raise HTTPException(
            status_code=400,
            detail=f"Only failed videos can be retried. Current status: {video.status}"
        )

    now = datetime.utcnow()

    await mongodb.db["videos"].update_one(
        {"_id": video_id},
        {"$set": {
            "status": VideoStatus.pending,
            "thumbnail_requested_at": now,   # ← resets the 15min clock
            "updated_at": now,
        }}
    )

    # re-enqueue celery task
    generate_thumbnail_task.delay({
        "video_s3_key": video.video_s3_key,
        "user_id": user_id,
    })

    return {"video_id": video_id, "status": VideoStatus.pending}



@router.websocket("/ws/progress/{video_id}")
async def video_progress_ws(websocket: WebSocket, video_id: str):
    origin = websocket.headers.get("origin")
    logger.info("WebSocket connection attempt for video %s | Origin: %s", video_id, origin)
    if origin not in ALLOWED_ORIGINS:
        await websocket.close(code=4003)
        return
    # 1. Authenticate
    user_id = await get_current_user_ws(websocket)
    if user_id is None:
        return
    
    # 2. Authorize
    video = await mongodb.db["videos"].find_one(
        {"_id": video_id, "user_id": user_id}
    )
    if not video:
        await websocket.close(code=4003)
        return

    # 3. Accept + register
    await websocket.accept()
    await manager.connect(websocket, video_id)
    
    # Rate limiting state — per connection, resets every minute
    message_count = 0
    window_start = asyncio.get_event_loop().time()


    try:
        # Keep receiving messages in a loop
        while True:
            text = await websocket.receive_text()

            if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
                logger.warning(
                    "Oversized message from user %s on video %s (%d bytes) — closing",
                    user_id, video_id, len(text.encode("utf-8"))
                )
                await websocket.close(code=1008)  # 1008 = policy violation
                return
            
            now = asyncio.get_event_loop().time()
            if now - window_start > 60:
                # reset window every 60 seconds
                message_count = 0
                window_start = now
            message_count += 1
            if message_count > MAX_MESSAGES_PER_MINUTE:
                logger.warning(
                    "Rate limit exceeded by user %s on video %s — closing",
                    user_id, video_id
                )
                await websocket.close(code=1008)
                return
            
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.warning(
                    "Malformed JSON from user %s on video %s — closing",
                    user_id, video_id
                )
                await websocket.close(code=1008)
                return
            
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                logger.warning(
                    "Malformed JSON from user %s on video %s — closing",
                    user_id, video_id
                )
                await websocket.close(code=1008)
                return
            
            if not isinstance(data, dict) or "type" not in data or not isinstance(data["type"], str):
                logger.warning(
                    "Invalid message structure from user %s on video %s — closing",
                    user_id, video_id
                )
                await websocket.close(code=1008)
                return

            if data["type"] not in ALLOWED_MESSAGE_TYPES:
                logger.warning(
                    "Unknown message type '%s' from user %s on video %s — closing",
                    data["type"], user_id, video_id
                )
                await websocket.close(code=1008)
                return

            # Handle heartbeat ping from frontend
            if data.get("type") == "__ping__":
                await websocket.send_json({"type": "__pong__"})

            # Ignore anything else — frontend doesn't send other messages
    except WebSocketDisconnect:
        logger.info("Client disconnected from video %s", video_id)
    finally:
        manager.disconnect(websocket, video_id)
        try:
            await websocket.close()
        except Exception:
            pass