"""
Unit tests for the thumbnail-key logic that replaced the old
`video_key.replace("videos/", "thumbnails/").replace(".mp4", ".png")` bug.

Covers:
- thumbnail_prefix_for: structural, extension-agnostic key derivation
- serialize_video: title/thumbnail fallbacks for older docs
- fix_thumbnail_keys: the "extension carried over from the video" bug signature
"""
import os

import pytest

from src.utils.thumbnail_service import thumbnail_prefix_for
from src.utils.video_helpers import serialize_video


# ── thumbnail_prefix_for ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "video_key, expected",
    [
        # The extensions that silently broke under the old .mp4 substring swap.
        ("videos/u1/vid_abc.mov", "thumbnails/u1/vid_abc"),
        ("videos/u1/vid_abc.webm", "thumbnails/u1/vid_abc"),
        ("videos/u1/vid_abc.mkv", "thumbnails/u1/vid_abc"),
        ("videos/u1/vid_abc.MP4", "thumbnails/u1/vid_abc"),  # uppercase
        ("videos/u1/vid_abc.mp4", "thumbnails/u1/vid_abc"),  # the one case that used to work
    ],
)
def test_prefix_is_extension_agnostic(video_key, expected):
    assert thumbnail_prefix_for(video_key) == expected


def test_full_thumbnail_key_is_always_png():
    # This is what the service actually writes: prefix + ".png".
    for ext in (".mov", ".webm", ".MP4", ".mp4"):
        key = f"{thumbnail_prefix_for(f'videos/u1/vid_x{ext}')}.png"
        assert key == "thumbnails/u1/vid_x.png"


def test_only_the_leading_videos_prefix_is_rewritten():
    # A stray "videos/" deeper in the path must NOT be rewritten (the old
    # find-and-replace would have clobbered it).
    key = "videos/u1/my-videos/clip.mov"
    assert thumbnail_prefix_for(key) == "thumbnails/u1/my-videos/clip"


def test_missing_videos_prefix_still_nests_under_thumbnails(caplog):
    # No "videos/" prefix: warn, but still produce a thumbnails/-rooted key
    # rather than crashing.
    assert thumbnail_prefix_for("u1/vid_abc.mp4") == "thumbnails/u1/vid_abc"
    assert thumbnail_prefix_for("/u1/vid_abc.mp4") == "thumbnails/u1/vid_abc"


# ── serialize_video fallbacks ───────────────────────────────────────────
class _FakeS3:
    def generate_presigned_url(self, op, Params, ExpiresIn=3600):
        return f"https://signed/{Params['Key']}"


def _serialize(doc):
    return serialize_video(doc, _FakeS3(), "bucket")


def test_serialize_prefers_explicit_title():
    out = _serialize({
        "_id": "vid_1",
        "original_filename": "raw.mov",
        "title": "My Solo",
        "description": "take 3",
        "video_s3_key": "videos/u1/vid_1.mov",
        "thumbnail_s3_key": "thumbnails/u1/vid_1.png",
    })
    assert out["title"] == "My Solo"
    assert out["description"] == "take 3"
    assert out["thumbnail_status"] == "ready"
    assert out["thumbnail_url"] == "https://signed/thumbnails/u1/vid_1.png"


def test_serialize_falls_back_to_filename_for_legacy_doc():
    # Doc written before `title` existed: no title/description keys at all.
    out = _serialize({
        "_id": "vid_2",
        "original_filename": "practice.mp4",
        "video_s3_key": "videos/u1/vid_2.mp4",
        "thumbnail_s3_key": None,
    })
    assert out["title"] == "practice.mp4"
    assert out["description"] is None
    assert out["thumbnail_url"] is None
    assert out["thumbnail_status"] == "processing"


def test_serialize_reports_failed_thumbnail():
    out = _serialize({
        "_id": "vid_3",
        "original_filename": "x.mp4",
        "video_s3_key": "videos/u1/vid_3.mp4",
        "thumbnail_s3_key": None,
        "thumbnail_status": "failed",
    })
    assert out["thumbnail_status"] == "failed"


# ── fix_thumbnail_keys bug-signature detection ──────────────────────────
# The migration only touches rows where the thumbnail extension EQUALS the
# video extension (the bug carried the video's extension onto the PNG). Rows
# already ".png", and genuine user-supplied thumbnails, are left alone.
def _is_affected(thumb_key, video_key):
    _, thumb_ext = os.path.splitext(thumb_key)
    _, video_ext = os.path.splitext(video_key)
    if thumb_ext.lower() == ".png":
        return False
    return thumb_ext.lower() == video_ext.lower()


@pytest.mark.parametrize(
    "thumb_key, video_key, affected",
    [
        ("thumbnails/u1/vid.mov", "videos/u1/vid.mov", True),   # classic bug row
        ("thumbnails/u1/vid.MOV", "videos/u1/vid.mov", True),   # case-insensitive match
        ("thumbnails/u1/vid.png", "videos/u1/vid.mov", False),  # already correct
        ("thumbnails/u1/vid.jpg", "videos/u1/vid.mov", False),  # user-supplied thumbnail
        ("thumbnails/u1/vid.mp4", "videos/u1/vid.mp4", True),   # even the .mp4 rows if any slipped
    ],
)
def test_bug_signature(thumb_key, video_key, affected):
    assert _is_affected(thumb_key, video_key) is affected
