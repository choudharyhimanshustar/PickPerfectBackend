"""
One-off migration: repair thumbnail keys left behind by the ".mp4" -> ".png"
find-and-replace bug in thumbnail_service.

Background: the thumbnail key used to be derived with

    video_key.replace("videos/", "thumbnails/").replace(".mp4", ".png")

The second replace is a substring swap, so it only fired for lowercase ".mp4".
Every other container (.mov, .webm, .mkv, uppercase .MP4) kept the *video's*
extension, and the PNG bytes were uploaded — and recorded in Mongo — under a
key like `thumbnails/{user}/{video_id}.mov`. Nothing raised, so those rows sit
at thumbnail_status "ready" with a key whose extension lies about its contents.

This script copies each affected object to the correct `.png` key and repoints
the doc at it. It is idempotent — running it again is a no-op.

Only rows where the thumbnail extension EQUALS the video extension are touched.
That is the bug's signature (the extension was carried over from the video). A
user-supplied thumbnail has an image extension of its own and is left alone.

Usage:
    python -m scripts.fix_thumbnail_keys              # apply
    python -m scripts.fix_thumbnail_keys --dry-run    # preview only
    python -m scripts.fix_thumbnail_keys --delete-old # also remove stale objects
"""
import os
import sys

from botocore.exceptions import ClientError

from src.core.database_sync import mongodb_sync
from src.utils.task_helpers import s3_client


def migrate(dry_run: bool = False, delete_old: bool = False) -> None:
    bucket = os.getenv("AWS_S3_BUCKET")
    if not bucket:
        sys.exit("AWS_S3_BUCKET is not set")

    mongodb_sync.connect()
    videos = mongodb_sync.db["videos"]

    scanned = 0
    affected = 0
    repaired = 0
    skipped_missing = 0

    for doc in videos.find({"thumbnail_s3_key": {"$ne": None}}):
        scanned += 1
        thumb_key = doc.get("thumbnail_s3_key")
        video_key = doc.get("video_s3_key")
        if not thumb_key or not video_key:
            continue

        thumb_base, thumb_ext = os.path.splitext(thumb_key)
        _, video_ext = os.path.splitext(video_key)

        if thumb_ext.lower() == ".png":
            continue                       # already correct
        if thumb_ext.lower() != video_ext.lower():
            continue                       # user-supplied thumbnail, not the bug

        affected += 1
        correct_key = f"{thumb_base}.png"
        print(f"{doc['_id']}: {thumb_key} -> {correct_key}")

        if dry_run:
            continue

        # The object may already have been copied by an earlier partial run, or
        # the original may be gone entirely. Neither should abort the batch.
        try:
            s3_client.head_object(Bucket=bucket, Key=correct_key)
            already_copied = True
        except ClientError:
            already_copied = False

        if not already_copied:
            try:
                s3_client.copy_object(
                    Bucket=bucket,
                    CopySource={"Bucket": bucket, "Key": thumb_key},
                    Key=correct_key,
                    ContentType="image/png",
                    MetadataDirective="REPLACE",
                )
            except ClientError as exc:
                print(f"  ! source object missing or unreadable, leaving doc alone: {exc}")
                skipped_missing += 1
                continue

        videos.update_one(
            {"_id": doc["_id"]},
            {"$set": {"thumbnail_s3_key": correct_key}},
        )
        repaired += 1

        if delete_old:
            try:
                s3_client.delete_object(Bucket=bucket, Key=thumb_key)
            except ClientError as exc:
                print(f"  ! could not delete stale object: {exc}")

    print(
        f"\nscanned={scanned} affected={affected} repaired={repaired} "
        f"skipped_missing={skipped_missing}"
        + (" (dry run — nothing written)" if dry_run else "")
    )


if __name__ == "__main__":
    migrate(
        dry_run="--dry-run" in sys.argv,
        delete_old="--delete-old" in sys.argv,
    )
