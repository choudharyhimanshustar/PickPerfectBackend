from fastapi import FastAPI
import boto3
from botocore.exceptions import NoCredentialsError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from dotenv import load_dotenv

app = FastAPI()
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env.development"))
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

@app.get("/generate-presigned-url")
def get_presigned_url(filename: str):
    try:
        
        print("Loaded region name:", os.getenv("AWS_REGION"))
        print("Loaded bucket name:", bucket_name)
        
        if not bucket_name:
                raise ValueError("AWS_S3_BUCKET is not set in environment variables.")

        presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': filename,
                    'ContentType': 'video/mp4'
                },
                ExpiresIn=3600
    )
        return {"url": presigned_url}
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not found")

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