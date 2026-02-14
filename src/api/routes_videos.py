from fastapi import APIRouter
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
    
@router.get("/generate-presigned-url")
async def get_presigned_url(filename: str,user_id: str = Depends(get_current_user)):
    try:
        
        print("Loaded region name:", os.getenv("AWS_REGION"))
        print("Loaded bucket name:", bucket_name)
        
        video_id = f"vid_{uuid4().hex}"
        s3_key = f"videos/{user_id}/{video_id}.mp4"

        metadata_doc = {
                "_id": video_id,
                "user_id": user_id,                 # 🔐 ownership
                "original_filename": filename,
                "s3_key": s3_key,
                "status": "PENDING_UPLOAD",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        
        result = await mongodb.db["videos"].insert_one(metadata_doc)
        print("Inserted ID:", result.inserted_id)

        if not bucket_name:
                raise ValueError("AWS_S3_BUCKET is not set in environment variables.")

        
        presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': s3_key,  
                    'ContentType': 'video/mp4'
                },
                ExpiresIn=3600
    )
        return {
            'Key': s3_key, 
            "url": presigned_url
        }
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