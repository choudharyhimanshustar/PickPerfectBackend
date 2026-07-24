import os
import subprocess
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
VIDEO_PREFIX = "videos/"
THUMBNAIL_PREFIX = "thumbnails/"


def thumbnail_prefix_for(video_key: str) -> str:
    """
    videos/{user_id}/{video_id}.{ext} -> thumbnails/{user_id}/{video_id}

    Extension-agnostic: os.path.splitext drops whatever the real extension is
    (.mov, .webm, .MP4) instead of matching one hardcoded string. The caller
    appends the extension of whatever it actually wrote.
    """
    base, _ = os.path.splitext(video_key)
    if base.startswith(VIDEO_PREFIX):
        return THUMBNAIL_PREFIX + base[len(VIDEO_PREFIX):]
    logger.warning("Video key %r has no %r prefix; nesting under it anyway", video_key, VIDEO_PREFIX)
    return THUMBNAIL_PREFIX + base.lstrip("/")


def generate_thumbnail_service(video, bucket_name, s3_client, mongodb):
    video_key = video["video_s3_key"]
    logger.info(f"Generating thumbnail for video: {video_key}")
    video_filename = os.path.basename(video_key)
    logger.info(f"Extracted video filename: {video_filename}")
    video_name = os.path.splitext(video_filename)[0]
    logger.info(f"Video name without extension: {video_name}")
    video_local = f"/tmp/{video_filename}"
    thumb_local = f"/tmp/{video_name}.png"

    try:
        # 1. Download
        s3_client.download_file(bucket_name, video_key, video_local)

        # 2. Generate thumbnail
        subprocess.run([
            "ffmpeg",
            "-ss", "00:00:01",
            "-i", video_local,
            "-frames:v", "1",
            "-update", "1",
            "-q:v", "2",
            thumb_local
        ], check=True)

        # 3. Upload — rebuild the key structurally rather than find-and-replacing
        #    inside it. Substring swaps silently no-op on the extensions they
        #    don't know about (the old ".mp4" -> ".png" left .mov/.webm keys
        #    describing a PNG as a video) and would rewrite a stray "videos/"
        #    anywhere in the path.
        thumbnail_key = f"{thumbnail_prefix_for(video_key)}.png"

        s3_client.upload_file(
            thumb_local,
            bucket_name,
            thumbnail_key,
            ExtraArgs={"ContentType": "image/png"}
        )

        # 4. Update DB — mark thumbnail ready (independent of processing status)
        mongodb.db["videos"].update_one(
            {"video_s3_key": video_key},
            {
                "$set": {
                    "thumbnail_s3_key": thumbnail_key,
                    "thumbnail_status": "ready",
                }
            }
        )

        return {
            "video": video,
            "thumbnail_s3_key": thumbnail_key
        }
    except Exception as e:
        logger.error(f"Error in thumbnail generation: {str(e)}")
        raise e
    finally:
        if os.path.exists(video_local):
            os.remove(video_local)
        if os.path.exists(thumb_local):
            os.remove(thumb_local)