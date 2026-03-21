import boto3
import json
import os
from src.app_celery.tasks import process_music_video

QUEUE_URL = os.environ.get("SQS_QUEUE_URL")
AWS_REGION = os.environ.get("AWS_REGION", "eu-north-1")

if not QUEUE_URL:
    raise ValueError("SQS_QUEUE_URL env variable not set")

sqs = boto3.client("sqs", region_name=AWS_REGION)

def poll_queue():
    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10,
        )

        messages = response.get("Messages", [])

        for msg in messages:
            body = json.loads(msg["Body"])

            print("Received from SQS:", body)

            try:
                # ✅ Extract s3 key
                s3_key = body["key"]

                # ✅ Call your actual task
                process_music_video.delay(s3_key)

                # ✅ delete after success
                sqs.delete_message(
                    QueueUrl=QUEUE_URL,
                    ReceiptHandle=msg["ReceiptHandle"]
                )

            except Exception as e:
                print("Error processing message:", str(e))
                # ❗ don't delete → retry hoga

if __name__ == "__main__":
    poll_queue()