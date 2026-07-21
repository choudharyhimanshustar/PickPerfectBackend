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
import subprocess
from typing import List
import asyncio
import json 
from sse_starlette.sse import EventSourceResponse
from src.utils.video_helpers import get_presigned_download_url
from src.database.schemas.metadata import get_video
from src.database.schemas.metadata import VideoStatus, ThumbnailStatus
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
            # Not yet uploaded — the client confirms via /confirm-upload once the
            # S3 PUT succeeds. Until then this doc is hidden from the grid and
            # gets garbage-collected if the upload is abandoned.
            "status": VideoStatus.awaiting_upload.value,
            "thumbnail_status": ThumbnailStatus.pending.value,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        await mongodb.db["videos"].insert_one(metadata_doc)

        # 🔹 Generate video presigned URL.
        # Do NOT pin ContentType here: it would become a signed header, and the
        # browser sends the file's real type (e.g. video/quicktime for .mov),
        # which then mismatches the signature and S3 returns 403. Leaving it
        # unsigned lets any video type upload; S3 still stores the Content-Type
        # the browser sends as object metadata.
        video_presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket_name,
                "Key": video_s3_key,
            },
            ExpiresIn=3600
        )

        response = {
            "video_id": video_id,
            "video": {
                "key": video_s3_key,
                "url": video_presigned_url
            }
        }

        # 🔹 Generate thumbnail presigned URL if needed.
        # Same reasoning as the video URL: don't sign ContentType, or a mismatch
        # between the signed type and the browser-sent type causes a 403.
        if thumbnail_s3_key:
            thumb_presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": bucket_name,
                    "Key": thumbnail_s3_key,
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


@router.post("/{video_id}/confirm-upload")
async def confirm_upload(
    video_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Called by the client after the S3 PUT succeeds. Promotes the doc from
    awaiting_upload -> pending_upload so it becomes visible and eligible for
    processing. If the upload failed, the client never calls this and the doc
    is garbage-collected by the stale-marker.
    """
    now = datetime.utcnow()
    result = await mongodb.db["videos"].update_one(
        {
            "_id": video_id,
            "user_id": user_id,
            "status": VideoStatus.awaiting_upload.value,
        },
        {"$set": {
            "status": VideoStatus.pending_upload.value,
            "thumbnail_requested_at": now,  # start the processing clock at upload time
            "updated_at": now,
        }},
    )

    if result.matched_count == 0:
        # not found, not owned, or already confirmed
        raise HTTPException(
            status_code=404,
            detail="No awaiting-upload video found to confirm",
        )

    return {"video_id": video_id, "status": VideoStatus.pending_upload.value}


@router.get("/{video_id}/analysis")
async def get_video_analysis(video_id: str, user_id: str = Depends(get_current_user)):
    video = await get_video(video_id, user_id)

    if video.status != VideoStatus.processed.value:
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

                # Do NOT filter on thumbnail_s3_key here — a failed thumbnail has
                # no key, and filtering it out would make failures undetectable.
                videos = await mongodb.db["videos"].find({
                    "_id": {"$in": list(pending_ids)},
                    "user_id": user_id,
                }).to_list(length=None)

                for video in videos:
                    vid_id = str(video["_id"])
                    thumb_status = video.get("thumbnail_status", ThumbnailStatus.pending.value)
                    logger.info("SSE checking video %s: thumbnail_status=%s", vid_id, thumb_status)

                    if thumb_status == ThumbnailStatus.failed.value:
                        # emit failed event — frontend shows retry button
                        yield {
                            "event": "thumbnail_failed",
                            "data": json.dumps({
                                "video_id": vid_id,
                                "thumbnail_status": "failed",
                            })
                        }
                        pending_ids.discard(vid_id)

                    elif thumb_status == ThumbnailStatus.ready.value and video.get("thumbnail_s3_key"):
                        thumbnail_url = get_presigned_download_url(
                            s3_client, bucket_name, video["thumbnail_s3_key"]
                        )
                        logger.info("Thumbnail ready for video %s", vid_id)
                        yield {
                            "event": "thumbnail_ready",
                            "data": json.dumps({
                                "video_id": vid_id,
                                "thumbnail_url": thumbnail_url,
                                "thumbnail_status": "ready",
                            })
                        }
                        pending_ids.discard(vid_id)
                    # else: still pending — keep watching until ready/failed

                logger.info("[SSE] %d videos still pending", len(pending_ids))

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

    if video.thumbnail_status != ThumbnailStatus.failed.value:
        raise HTTPException(
            status_code=400,
            detail=f"Only failed thumbnails can be retried. Current thumbnail_status: {video.thumbnail_status}"
        )

    now = datetime.utcnow()

    await mongodb.db["videos"].update_one(
        {"_id": video_id},
        {"$set": {
            "thumbnail_status": ThumbnailStatus.pending.value,
            "thumbnail_requested_at": now,   # ← resets the 15min clock
            "updated_at": now,
        }}
    )

    # re-enqueue celery task
    generate_thumbnail_task.delay({
        "video_s3_key": video.video_s3_key,
        "user_id": user_id,
    })

    return {"video_id": video_id, "thumbnail_status": ThumbnailStatus.pending.value}



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