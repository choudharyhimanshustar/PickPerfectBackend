from src.app_celery.celery_app import celery_app
import time
import logging
from src.core.database_sync import mongodb_sync
from src.utils.thumbnail_service import generate_thumbnail_service
from src.core.database import mongodb
import boto3
from botocore.exceptions import NoCredentialsError
import os 
logger = logging.getLogger(__name__)
bucket_name = os.getenv("AWS_S3_BUCKET")
# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

from src.utils.task_helpers import (
    download_video_from_s3,
    extract_audio_from_video,
    analyze_audio_features,
    detect_chords,
    detect_rhythm,
    evaluate_performance,
    save_analysis_result,
    update_video_status
)

@celery_app.task(name="process_music_video")
def process_music_video(video_data: dict):
    # 🔥 GUARANTEED DB INIT
    mongodb_sync.connect()
    s3_key = video_data["video_s3_key"]
    video_path = download_video_from_s3(s3_key)
    logger.info("Downloaded video to:", video_path)
    
    audio_path = extract_audio_from_video(video_path)
    logger.info("Extracted audio to:", audio_path)
    
    features = analyze_audio_features(audio_path)
    logger.info("Analyzed audio features.", features)
    logger.info("Feature keys: %s", list(features.keys()))
    logger.info("Feature summary: %s", {k: type(v) for k, v in features.items()})

    chord_result = detect_chords(features)
    logger.info(f"Detected chords. Result: {chord_result}")
    
    rhythm_result = detect_rhythm(features)
    logger.info(f"Detected rhythm. Result: {rhythm_result}")
    
    performance_score = evaluate_performance(
        chord_result,
        rhythm_result
    )
    logger.info(f"Evaluated performance. Score: {performance_score}")
    
    save_analysis_result(
        s3_key,
        chord_result,
        rhythm_result,
        performance_score
    )

    update_video_status(s3_key, "processed")
    logger.info(f"Updated video status to 'processed' for {s3_key}")
    
    return {"s3_key": s3_key, "status": "completed"}

@celery_app.task(name="retry_failed_task", bind=True, max_retries=3, default_retry_delay=60)
def generate_thumbnail_task(self, video_data):
    """
    video_data → contains video_s3_key, user_id
    """
    mongodb_sync.connect()
    # 🔥 fetch latest video from DB (avoid stale data)
    print("Fetching video from DB for:", video_data)
    video = mongodb_sync.db["videos"].find_one({
        "video_s3_key": video_data["video_s3_key"],
        "user_id": video_data["user_id"]
    })
    
    if not video:
        raise ValueError(f"Video not found for {video_data}")
    
    # skip if already exists
    if video.get("thumbnail_s3_key"):
        print("Thumbnail already exists, skipping...")
        return video_data

    result = generate_thumbnail_service(video, bucket_name, s3_client, mongodb_sync)
    

    return {
        **video_data,
        "thumbnail_s3_key": result["thumbnail_s3_key"]
    }