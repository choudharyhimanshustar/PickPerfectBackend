"""
Integration test for the title/description handling added to
POST /videos/generate-presigned-url (commit d543a66).

Verifies the normalization rules against the real route handler:
- blank title -> filename with extension stripped
- explicit title trimmed
- title capped at MAX_TITLE_LEN, description at MAX_DESCRIPTION_LEN
- blank description -> None
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.routes_videos as rv
from src.database.schemas.auth import get_current_user


class _FakeVideos:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()


class _FakeDB:
    def __init__(self):
        self.videos = _FakeVideos()

    def __getitem__(self, name):
        assert name == "videos"
        return self.videos


class _FakeMongo:
    def __init__(self):
        self.db = _FakeDB()


class _FakeS3:
    def generate_presigned_url(self, op, Params, ExpiresIn=3600):
        return f"https://signed/{Params['Key']}"


@pytest.fixture
def client(monkeypatch):
    fake_mongo = _FakeMongo()
    monkeypatch.setattr(rv, "mongodb", fake_mongo)
    monkeypatch.setattr(rv, "s3_client", _FakeS3())
    monkeypatch.setattr(rv, "bucket_name", "test-bucket")

    app = FastAPI()
    app.include_router(rv.router, prefix="/videos")
    app.dependency_overrides[get_current_user] = lambda: "user-123"

    c = TestClient(app)
    c._videos = fake_mongo.db.videos  # expose inserted docs to the test
    return c


def _post(client, **body):
    body.setdefault("videoName", "solo.mov")
    resp = client.post("/videos/generate-presigned-url", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json(), client._videos.inserted[-1]


def test_blank_title_falls_back_to_filename_without_extension(client):
    _, doc = _post(client, videoName="My Practice.mov")
    assert doc["title"] == "My Practice"
    assert doc["description"] is None


def test_explicit_title_and_description_are_trimmed(client):
    _, doc = _post(client, title="  Blues in A  ", description="  take two  ")
    assert doc["title"] == "Blues in A"
    assert doc["description"] == "take two"


def test_whitespace_only_title_falls_back_to_filename(client):
    _, doc = _post(client, videoName="riff.webm", title="   ")
    assert doc["title"] == "riff"


def test_title_and_description_are_length_capped(client):
    _, doc = _post(
        client,
        title="T" * 500,
        description="D" * 5000,
    )
    assert len(doc["title"]) == rv.MAX_TITLE_LEN
    assert len(doc["description"]) == rv.MAX_DESCRIPTION_LEN


def test_keys_and_response_shape(client):
    resp, doc = _post(client, videoName="clip.mov", thumbnailName="cover.jpg")
    assert doc["video_s3_key"] == f"videos/user-123/{doc['_id']}.mov"
    assert resp["video"]["key"] == doc["video_s3_key"]
    assert resp["thumbnail"]["key"] == f"thumbnails/user-123/{doc['_id']}.jpg"


def test_missing_video_name_is_rejected(client):
    resp = client.post("/videos/generate-presigned-url", json={"videoName": ""})
    assert resp.status_code == 400
