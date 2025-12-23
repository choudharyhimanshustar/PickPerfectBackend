from fastapi import APIRouter
from src.core.database import mongodb
from uuid import uuid4
import os
from datetime import datetime
import boto3
from botocore.exceptions import NoCredentialsError
from pydantic import BaseModel
from src.app_celery.tasks import process_music_video


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
async def get_presigned_url(filename: str):
    try:
        
        print("Loaded region name:", os.getenv("AWS_REGION"))
        print("Loaded bucket name:", bucket_name)
        
        video_id = f"vid_{uuid4().hex}"
        s3_key = f"videos/{video_id}.mp4"

        metadata_doc = {
                "_id": video_id,
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