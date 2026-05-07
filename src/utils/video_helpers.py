def get_presigned_download_url(s3_client, bucket_name: str, s3_key: str, expires_in: int = 3600) -> str:
    return s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket_name,
            "Key": s3_key,
        },
        ExpiresIn=expires_in,
    )


def serialize_video(video: dict, s3_client, bucket_name: str) -> dict:
    video_url = get_presigned_download_url(s3_client, bucket_name, video["video_s3_key"])

    has_thumbnail = bool(video.get("thumbnail_s3_key"))

    thumbnail_url = (
        get_presigned_download_url(s3_client, bucket_name, video["thumbnail_s3_key"])
        if has_thumbnail
        else None
    )
    

    if has_thumbnail:
        thumbnail_status = "ready"
    elif video.get("status") == "failed":
        thumbnail_status = "failed"
    else:
        thumbnail_status = "processing"

    return {
        "video_id": str(video["_id"]),
        "filename": video["original_filename"],
        "video_url": video_url,
        "thumbnail_url": thumbnail_url,
        "thumbnail_status": thumbnail_status,
    }