import asyncio
import json
import logging
import os
import redis.asyncio as aioredis
from src.api.websocket_manager import manager

logger = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def redis_subscriber():
    """
    Single long-running background task that subscribes to all
    ws:progress:* channels and routes messages to the right WebSocket.
    Starts once at FastAPI startup.
    """
    while True:  # outer loop: reconnect if Redis drops
        redis_conn = None
        pubsub = None
        try:
            redis_conn = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub = redis_conn.pubsub()
            await pubsub.psubscribe("ws:progress:*")  # pattern match all video channels
            logger.info("Redis subscriber started — listening on ws:progress:*")

            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue  # skip subscription confirmations

                try:
                    # channel is e.g. "ws:progress:vid_abc123"
                    channel = message["channel"]
                    video_id = channel.removeprefix("ws:progress:")
                    data = json.loads(message["data"])
                    await manager.send_to_video(video_id, data)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Bad message on channel %s: %s", message.get("channel"), e)

        except Exception as e:
            logger.exception("Redis subscriber crashed, reconnecting in 3s: %s", e)
            await asyncio.sleep(3)  # wait before reconnecting
        finally:
            try:
                if pubsub:
                    await pubsub.punsubscribe("ws:progress:*")
                    await pubsub.close()
                if redis_conn:
                    await redis_conn.aclose()
            except Exception:
                pass