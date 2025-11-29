from fastapi import FastAPI
import boto3
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
from uuid import uuid4
from datetime import datetime
from src.core.database import connect_to_mongo, close_mongo_connection
from src.api.routes_videos import router as videos_router

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()

app.include_router(videos_router, prefix="/videos", tags=["videos"])
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env.development"))
load_dotenv()

bucket_name = os.getenv("AWS_S3_BUCKET")
# Allow CORS (for frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)



@app.get("/")
async def root():
    return {"message": "Hello World"}



@app.get("/all-videos")
def get_all_videos():
    # List everything in bucket root (no folders)
    response = s3_client.list_objects_v2(Bucket=bucket_name)

    if "Contents" not in response:
        return {"videos": []}

    video_urls = []

    for obj in response["Contents"]:
        key = obj["Key"]

        # skip non-video files (optional)
        if not key.lower().endswith((".mp4", ".mov", ".avi", ".webm", ".mkv")):
            continue

        # generate pre-signed URL
        url = s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=3600
        )

        video_urls.append({
            "key": key,
            "url": url
        })

    return {"videos": video_urls}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
