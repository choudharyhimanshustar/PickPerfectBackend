from src.app_celery.celery_app import celery_app
import time
import logging
from src.core.database_sync import mongodb_sync
from src.utils.thumbnail_service import generate_thumbnail_service
from src.core.database import mongodb
import boto3
from botocore.exceptions import NoCredentialsError
import os 
from datetime import datetime, timezone, timedelta
import json
from src.utils.ws_progress import publish_progress

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
bucket_name = os.getenv("AWS_S3_BUCKET")
# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)
sqs_client = boto3.client("sqs", region_name=os.getenv("AWS_REGION"))

DLQ_URL = os.getenv("SQS_DLQ_URL")
MAIN_QUEUE_URL = os.getenv("SQS_QUEUE_URL")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
MAX_REQUEUE_BATCH = 10

STALE_THRESHOLD_MINUTES = 15

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

def _extract_video_id_from_body(body: dict) -> str | None:
    """Extract video_id from SQS message body using the S3 key path."""
    try:
        video_s3_key = body["data"]["video_s3_key"]
        # Format: videos/{user_id}/{video_id}.mp4
        filename = video_s3_key.split("/")[-1]          # vid_abc123.mp4
        video_id = filename.rsplit(".", 1)[0]            # vid_abc123
        return video_id if video_id.startswith("vid_") else None
    except (KeyError, IndexError):
        return None
    
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

    # skip generation if a thumbnail already exists (user-provided, or an
    # idempotent re-run) — but still mark it ready, otherwise thumbnail_status
    # stays "pending" forever and the UI never shows it.
    if video.get("thumbnail_s3_key"):
        print("Thumbnail already exists, marking ready and skipping generation...")
        if video.get("thumbnail_status") != "ready":
            mongodb_sync.db["videos"].update_one(
                {"video_s3_key": video_data["video_s3_key"]},
                {"$set": {"thumbnail_status": "ready", "updated_at": datetime.now(timezone.utc)}},
            )
        return video_data

    try:
        result = generate_thumbnail_service(video, bucket_name, s3_client, mongodb_sync)
    except Exception:
        # Mark the thumbnail (not the whole video) failed so the UI can offer retry.
        mongodb_sync.db["videos"].update_one(
            {"video_s3_key": video_data["video_s3_key"]},
            {"$set": {"thumbnail_status": "failed", "updated_at": datetime.now(timezone.utc)}},
        )
        raise

    receipt_handle = video_data.get("receipt_handle")
    if receipt_handle:
        try:
            sqs_client.delete_message(
                QueueUrl=MAIN_QUEUE_URL,
                ReceiptHandle=receipt_handle
            )
            logger.info("Deleted SQS message after successful thumbnail generation")
        except Exception as e:
            logger.warning("Failed to delete SQS message (non-fatal): %s", e)
    return {
        **video_data,
        "thumbnail_s3_key": result["thumbnail_s3_key"]
    }
    
@celery_app.task(name="mark_stale_pending_as_failed")
def mark_stale_pending_as_failed():
    mongodb_sync.connect()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    # 1. Abandoned uploads (presigned URL issued, PUT never confirmed) have no
    #    real S3 object and no value — delete them outright.
    deleted = mongodb_sync.db["videos"].delete_many(
        {
            "status": "awaiting_upload",
            "thumbnail_requested_at": {"$lt": cutoff, "$exists": True},
        }
    )

    # 2. Confirmed uploads that never entered processing -> mark failed. A video
    #    mid-analysis is "processing" and is deliberately excluded here so we
    #    don't kill live jobs.
    result = mongodb_sync.db["videos"].update_many(
        {
            "status": "pending_upload",
            "thumbnail_requested_at": {"$lt": cutoff, "$exists": True},
        },
        {
            "$set": {
                "status": "failed",
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )

    logger.info(
        "[beat] Deleted %d abandoned uploads, marked %d stale videos as failed",
        deleted.deleted_count, result.modified_count,
    )
    return {
        "deleted_abandoned": deleted.deleted_count,
        "marked_failed": result.modified_count,
    }

@celery_app.task(name="requeue_from_dlq")
def requeue_from_dlq():
    print("Starting DLQ requeue task...")
    if not DLQ_URL or not MAIN_QUEUE_URL:
        logger.error("SQS_DLQ_URL or SQS_QUEUE_URL not set — skipping DLQ requeue")
        return

    sqs = boto3.client("sqs", region_name=AWS_REGION)
    
    requeued = 0
    errors = 0

    while True:
        response = sqs.receive_message(
            QueueUrl=DLQ_URL,
            MaxNumberOfMessages=MAX_REQUEUE_BATCH,
            WaitTimeSeconds=1,
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not messages:
            break

        for msg in messages:
            receipt_handle = msg["ReceiptHandle"]
            msg_id = msg.get("MessageId")
            logger.info("Processing DLQ message %s", msg_id)
            # Step 1: parse
            try:
                body = json.loads(msg["Body"])
                logger.info("Processing DLQ message %s with body: %s", msg_id, body)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("DLQ message %s has invalid body, discarding: %s", msg_id, e)
                _delete_from_dlq(sqs, receipt_handle, msg_id)
                continue

            video_id = _extract_video_id_from_body(body)
            if not video_id:
                logger.warning("DLQ message %s missing video_id, discarding", msg_id)
                _delete_from_dlq(sqs, receipt_handle, msg_id)
                continue

            # Step 2: update DB
            try:
                result = mongodb_sync.db["videos"].update_one(
                    {"_id": video_id},
                    {"$set": {
                        "status": "pending_upload",
                        "thumbnail_requested_at": datetime.now(timezone.utc),
                    }},
                )
            except Exception as e:
                logger.exception("DB update failed for video %s (msg %s), leaving in DLQ: %s", video_id, msg_id, e)
                errors += 1
                continue  # leave in DLQ — don't re-enqueue with stale DB state

            if result.matched_count == 0:
                logger.warning("video_id %s not found in DB (msg %s), discarding", video_id, msg_id)
                _delete_from_dlq(sqs, receipt_handle, msg_id)
                continue

            # Step 3: re-enqueue to main queue
            try:
                video = mongodb_sync.db["videos"].find_one({"_id": video_id})
                if not video:
                    logger.warning("video_id %s not found for re-enqueue, discarding", video_id)
                    _delete_from_dlq(sqs, receipt_handle, msg_id)
                    continue

                sqs.send_message(
                    QueueUrl=MAIN_QUEUE_URL,
                    MessageBody=json.dumps({
                        "data": {
                            "video_s3_key": video["video_s3_key"],
                            "user_id": video["user_id"],
                        }
                    }),
)
            except Exception as e:
                logger.exception("Failed to re-enqueue video %s (msg %s), leaving in DLQ: %s", video_id, msg_id, e)
                errors += 1
                continue  # leave in DLQ — safe, DB update will be corrected by staleness job

            # Step 4: delete from DLQ only after successful re-enqueue
            _delete_from_dlq(sqs, receipt_handle, msg_id)
            logger.info("Requeued video %s from DLQ → main queue (msg %s)", video_id, msg_id)
            requeued += 1

    logger.info("DLQ requeue complete: %d requeued, %d errors", requeued, errors)
    return {"requeued": requeued, "errors": errors}


def _delete_from_dlq(sqs_client, receipt_handle: str, msg_id: str = None):
    """Delete a message from the DLQ. Raises on failure — callers decide how to handle."""
    try:
        sqs_client.delete_message(QueueUrl=DLQ_URL, ReceiptHandle=receipt_handle)
        logger.info("Deleted DLQ message %s", msg_id) 
    except Exception as e:
        # Receipt handles expire after the visibility timeout — log and move on.
        # The message will reappear in DLQ and be processed next cycle.
        logger.error("Failed to delete DLQ message %s: %s", msg_id, e)
        
@celery_app.task(name="process_music_video")
def process_music_video(video_data: dict):
    # 🔥 GUARANTEED DB INIT
    mongodb_sync.connect()
    s3_key = video_data["video_s3_key"]

    # Extract video_id from s3_key for the WS channel
    # Format: videos/{user_id}/{video_id}.{ext}
    try:
        filename = s3_key.split("/")[-1]
        video_id = filename.rsplit(".", 1)[0]
    except Exception:
        video_id = ""

    try:
        # Mark the video as actively processing so the stale-marker leaves it
        # alone and the UI can distinguish "queued" from "in progress".
        update_video_status(s3_key, "processing")
        publish_progress(video_id, "video_received", "Video received, starting pipeline...", 5)

        publish_progress(video_id, "downloading", "Downloading video from S3...", 15)
        video_path = download_video_from_s3(s3_key)
        logger.info("Downloaded video to: %s", video_path)

        publish_progress(video_id, "extracting_audio", "Extracting audio track...", 30)
        audio_path = extract_audio_from_video(video_path)
        logger.info("Extracted audio to: %s", audio_path)

        publish_progress(video_id, "analyzing_features", "Analyzing audio features...", 45)
        features = analyze_audio_features(audio_path)
        logger.info("Analyzed audio features.")
        logger.info("Feature keys: %s", list(features.keys()))

        publish_progress(video_id, "detecting_chords", "Detecting chords...", 60)
        chord_result = detect_chords(features)
        logger.info("Detected chords. Result: %s", chord_result)

        publish_progress(video_id, "detecting_rhythm", "Detecting rhythm...", 70)
        rhythm_result = detect_rhythm(features)
        logger.info("Detected rhythm. Result: %s", rhythm_result)

        publish_progress(video_id, "evaluating", "Evaluating performance...", 80)
        performance_score = evaluate_performance(chord_result, rhythm_result)
        logger.info("Evaluated performance. Score: %s", performance_score)

        publish_progress(video_id, "saving_results", "Saving analysis results...", 90)
        save_analysis_result(s3_key, chord_result, rhythm_result, performance_score)

        update_video_status(s3_key, "processed")
        logger.info("Updated video status to 'processed' for %s", s3_key)

        # Terminal event: `type` must match the canonical status ("processed"),
        # while `step` ("processed") drives the final stepper row on the client.
        publish_progress(video_id, "processed", "Processing complete!", 100, event_type="processed")

        return {"s3_key": s3_key, "status": "processed"}

    except Exception as e:
        logger.exception("process_music_video failed for %s", s3_key)
        publish_progress(
            video_id,
            "failed",
            "Processing failed",
            0,
            event_type="failed",
            error=str(e),
        )
        raise  # Re-raise so Celery retry / DLQ logic still applies
