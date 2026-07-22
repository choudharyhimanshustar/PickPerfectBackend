from fastapi import FastAPI,Depends
import boto3
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv
from uuid import uuid4
from datetime import datetime
from src.core.database import connect_to_mongo, close_mongo_connection
from src.api.routes_videos import router as videos_router
from uuid import uuid4
from datetime import datetime
from src.core.database import connect_to_mongo, close_mongo_connection
from src.api.routes_videos import router as videos_router
from src.api.routes_auth import router as auth_router
from src.database.schemas.auth import get_current_user
from src.core.database import mongodb
from src.utils.video_helpers import get_presigned_download_url, serialize_video
import asyncio
from contextlib import asynccontextmanager
from src.api.redis_subscriber import redis_subscriber
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await connect_to_mongo()                         
    task = asyncio.create_task(redis_subscriber())
    yield
    # shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await close_mongo_connection()                    

app = FastAPI(lifespan=lifespan)

# Allow CORS (for frontend)
app.add_middleware(
    CORSMiddleware,
     allow_origins=[
        "http://localhost:3000",
    ],  # Replace with your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request, call_next):
    logger.info(">>> %s %s | Origin: %s", request.method, request.url.path, request.headers.get("origin"))
    response = await call_next(request)
    logger.info("<<< %s", response.status_code)
    return response

app.include_router(videos_router, prefix="/videos", tags=["videos"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env.development"))

bucket_name = os.getenv("AWS_S3_BUCKET")

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
async def get_all_videos(user_id: str = Depends(get_current_user)):

    # Exclude docs whose upload was never confirmed — they'd otherwise show as
    # broken cards. They are promoted by /videos/{id}/confirm-upload or GC'd.
    videos = await mongodb.db["videos"].find(
        {"user_id": user_id, "status": {"$ne": "awaiting_upload"}}
    ).to_list(length=None)

    video_list = [serialize_video(video, s3_client, bucket_name) for video in videos]

    return {"videos": video_list}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
