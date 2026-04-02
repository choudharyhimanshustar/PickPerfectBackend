import os
import subprocess

def generate_thumbnail_service(video, bucket_name, s3_client, mongodb):
    video_key = video["video_s3_key"]
    video_filename = os.path.basename(video_key)
    video_name = os.path.splitext(video_filename)[0]

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

        # 3. Upload
        thumbnail_key = video_key.replace("videos/", "thumbnails/").replace(".mp4", ".png")

        s3_client.upload_file(
            thumb_local,
            bucket_name,
            thumbnail_key,
            ExtraArgs={"ContentType": "image/png"}
        )

        # 4. Update DB
        mongodb.db["videos"].update_one(
            {"video_s3_key": video_key},
            {
                "$set": {
                    "thumbnail_s3_key": thumbnail_key,
                    "status": "READY"
                }
            }
        )

        return {
            "video": video,
            "thumbnail_s3_key": thumbnail_key
        }

    finally:
        if os.path.exists(video_local):
            os.remove(video_local)
        if os.path.exists(thumb_local):
            os.remove(thumb_local)