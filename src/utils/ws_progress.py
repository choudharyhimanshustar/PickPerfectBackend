import os
import json
import logging
import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Sync Redis client — Celery tasks run in sync context.
_redis_client = None

def _get_client():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

def publish_progress(
    video_id: str,
    step: str,
    message: str,
    percent: int,
    event_type: str = "progress",
    error: str | None = None,
):
    """
    Publish a progress event to the Redis channel for this video.
    Called from inside Celery tasks. The FastAPI WebSocket endpoint
    subscribes to the same channel and forwards the message to clients.
    """
    if not video_id:
        logger.warning("publish_progress called with empty video_id, skipping")
        return

    payload = {
        "type": event_type,
        "video_id": video_id,
        "step": step,
        "message": message,
        "percent": percent,
    }
    if error:
        payload["error"] = error

    channel = f"ws:progress:{video_id}"
    try:
        result = _get_client().publish(channel, json.dumps(payload))
        logger.info("📡 Published to %s — %d subscribers", channel, result)
    except Exception as e:
        # Never let progress publishing break the actual pipeline
        logger.warning("Failed to publish progress for %s: %s", video_id, e)
