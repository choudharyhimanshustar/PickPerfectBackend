#!/usr/bin/env python3
"""
Usage:
    python send_fake_dlq_message.py --video-id 507f1f77bcf86cd799439011
    python send_fake_dlq_message.py --fake-id
    python send_fake_dlq_message.py --missing-id
    python send_fake_dlq_message.py --video-id 507f1f77bcf86cd799439011 --count 3
    python send_fake_dlq_message.py --create-and-send
"""

from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
dotenv_path = BASE_DIR / ".env.development"
print("Loading .env from:", dotenv_path)
if dotenv_path.exists():
    load_dotenv(dotenv_path)

import argparse
import json
import uuid
import os
import boto3
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

DLQ_URL = os.getenv("SQS_DLQ_URL")
MAIN_QUEUE_URL = os.getenv("SQS_QUEUE_URL")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
MONGODB_URI = os.getenv("MONGO_URI")
MONGODB_DB = os.getenv("MONGO_DB", "PickPerfect")

print("MONGO_URI:", MONGODB_URI)
print("DLQ_URL:", DLQ_URL)

if not DLQ_URL:
    raise SystemExit("ERROR: SQS_DLQ_URL is not set — check your .env.development file")

if not MONGODB_URI:
    raise SystemExit("ERROR: MONGO_URI is not set — check your .env.development file")

# ── DB ────────────────────────────────────────────────────────────────────────

def get_db():
    from pymongo import MongoClient
    client = MongoClient(MONGODB_URI)
    return client[MONGODB_DB]


def create_fake_video_in_db() -> str:
    db = get_db()
    # Match your real _id format: "vid_" + 32 hex chars
    video_id = f"vid_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"
    fake_video = {
        "_id": video_id,  # ← explicitly set _id to match your format
        "video_s3_key": f"fake/test-video-{uuid.uuid4().hex[:8]}.mp4",
        "user_id": "test_user",
        "status": "pending",
        "thumbnail_requested_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
    }
    result = db["videos"].insert_one(fake_video)
    print(f"  Created DB entry → _id={video_id}")
    return video_id


# ── Message builders ──────────────────────────────────────────────────────────

def body_happy(video_id: str) -> dict:
    return {"video_id": video_id}

def body_fake_id() -> dict:
    return {"video_id": uuid.uuid4().hex[:24]}

def body_missing_id() -> dict:
    return {"source": "fake_test", "reason": "testing missing video_id path"}


# ── Core ──────────────────────────────────────────────────────────────────────

def send(sqs, body: dict, label: str, index: int, total: int):
    resp = sqs.send_message(
        QueueUrl=DLQ_URL,
        MessageBody=json.dumps(body),
    )
    print(f"  [{index}/{total}] {label} — MessageId={resp['MessageId']}  body={json.dumps(body)}")


def main():
    parser = argparse.ArgumentParser(description="Send fake messages to the thumbnail DLQ")

    id_group = parser.add_mutually_exclusive_group()
    id_group.add_argument("--video-id", help="Real video _id from DB (happy path)")
    id_group.add_argument("--fake-id", action="store_true", help="Random non-existent id (matched_count == 0)")
    id_group.add_argument("--missing-id", action="store_true", help="Omit video_id entirely")
    id_group.add_argument("--create-and-send", action="store_true",
                          help="Create a real DB entry with status=pending then send its id to DLQ")

    parser.add_argument("--count", type=int, default=1, help="Number of messages to send (default: 1)")
    args = parser.parse_args()

    if not any([args.video_id, args.fake_id, args.missing_id, args.create_and_send]):
        parser.error("Provide one of --video-id, --fake-id, --missing-id, or --create-and-send")

    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=AWS_REGION,
    )
    sqs = session.client("sqs")

    print(f"\nSending {args.count} message(s) to DLQ: {DLQ_URL}\n")

    for i in range(1, args.count + 1):
        if args.video_id:
            send(sqs, body_happy(args.video_id), "happy path", i, args.count)

        elif args.fake_id:
            send(sqs, body_fake_id(), "fake id (no DB match)", i, args.count)

        elif args.missing_id:
            send(sqs, body_missing_id(), "missing video_id", i, args.count)

        elif args.create_and_send:
            video_id = create_fake_video_in_db()
            send(sqs, body_happy(video_id), "create-and-send (real DB entry)", i, args.count)

    print("\nDone.")


if __name__ == "__main__":
    main()