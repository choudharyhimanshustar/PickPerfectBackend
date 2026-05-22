from fastapi import WebSocket
import json
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections, keyed by video_id.
    A user may have multiple tabs open watching the same video,
    so each video_id maps to a list of sockets.
    """

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, video_id: str):
        # NOTE: websocket.accept() must be called BEFORE this method.
        # We don't accept here because auth has to happen first.
        if video_id not in self.active_connections:
            self.active_connections[video_id] = []
        self.active_connections[video_id].append(websocket)
        logger.info(
            "WS connected for video %s (total: %d)",
            video_id,
            len(self.active_connections[video_id]),
        )

    def disconnect(self, websocket: WebSocket, video_id: str):
        if video_id in self.active_connections:
            try:
                self.active_connections[video_id].remove(websocket)
            except ValueError:
                pass
            if not self.active_connections[video_id]:
                del self.active_connections[video_id]
        logger.info("WS disconnected for video %s", video_id)

    async def send_to_video(self, video_id: str, message: dict):
        """Send a message to every socket watching this video_id."""
        if video_id not in self.active_connections:
            return

        payload = json.dumps(message)
        dead_sockets = []

        for ws in self.active_connections[video_id]:
            try:
                await ws.send_text(payload)
            except Exception as e:
                logger.warning("Failed to send to WS, marking dead: %s", e)
                dead_sockets.append(ws)

        for ws in dead_sockets:
            self.disconnect(ws, video_id)


manager = ConnectionManager()
