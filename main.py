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

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()

@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()

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
app.include_router(videos_router, prefix="/videos", tags=["videos"])
app.include_router(auth_router, prefix="/auth", tags=["auth"])
# load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env.development"))
load_dotenv()


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
    
    videos = await mongodb.db["videos"].find(
        {"user_id": user_id}
    ).to_list(length=None)

    video_urls = []

    for video in videos:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket_name,
                "Key": video["s3_key"]
            },
            ExpiresIn=3600
        )

        video_urls.append({
            "video_id": video["_id"],
            "filename": video["original_filename"],
            "url": url
        })

    return {"videos": video_urls}

@app.post("/webhook")
async def webhook(data: dict):
    print("Webhook received:", data)

    # do DB update here:
    # update video status from "uploading" to "uploaded"
    
    return {"received": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
