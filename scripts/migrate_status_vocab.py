"""
One-off migration: normalize the legacy `status` vocabulary and backfill the
new `thumbnail_status` field.

Background: video docs historically stored several inconsistent status strings
("PENDING_UPLOAD", "READY", "analyzed", "pending", ...) in a single `status`
field that conflated the thumbnail lifecycle with the processing lifecycle.
The codebase now uses two canonical enums:

    status           -> pending_upload | processing | processed | failed
    thumbnail_status -> pending | ready | failed

This script rewrites existing rows to match. It is idempotent — running it
again is a no-op.

Usage:
    python -m scripts.migrate_status_vocab            # apply
    python -m scripts.migrate_status_vocab --dry-run  # preview only
"""
import sys

from src.core.database_sync import mongodb_sync


# legacy processing-status -> canonical processing-status
STATUS_MAP = {
    "PENDING_UPLOAD": "pending_upload",
    "pending_upload": "pending_upload",
    "pending": "pending_upload",
    "processing": "processing",
    "analyzed": "processed",
    "processed": "processed",
    "READY": "pending_upload",   # thumbnail was ready; analysis not yet done
    "failed": "failed",
}


def migrate(dry_run: bool = False) -> None:
    mongodb_sync.connect()
    videos = mongodb_sync.db["videos"]

    total = 0
    updated = 0

    for doc in videos.find({}):
        total += 1
        legacy_status = doc.get("status")
        has_thumb = bool(doc.get("thumbnail_s3_key"))
        has_analysis = doc.get("analysis") is not None

        # ── processing status ────────────────────────────────────────────
        new_status = STATUS_MAP.get(legacy_status, legacy_status)
        # A doc that already has analysis is definitively processed.
        if has_analysis:
            new_status = "processed"

        # ── thumbnail status ─────────────────────────────────────────────
        # A thumbnail key present is authoritative — it means the thumbnail is
        # ready, regardless of any stale "pending" left in the field.
        if has_thumb or legacy_status == "READY":
            new_thumb = "ready"
        elif doc.get("thumbnail_status") == "failed" or legacy_status == "failed":
            # A failed doc with no thumbnail was most likely a thumbnail failure.
            new_thumb = "failed"
        else:
            new_thumb = "pending"

        changes = {}
        if new_status != legacy_status:
            changes["status"] = new_status
        if doc.get("thumbnail_status") != new_thumb:
            changes["thumbnail_status"] = new_thumb

        if not changes:
            continue

        updated += 1
        print(
            f"{doc['_id']}: status {legacy_status!r} -> {changes.get('status', legacy_status)!r}"
            f" | thumbnail_status {doc.get('thumbnail_status')!r} -> {new_thumb!r}"
        )
        if not dry_run:
            videos.update_one({"_id": doc["_id"]}, {"$set": changes})

    verb = "would update" if dry_run else "updated"
    print(f"\nDone. Scanned {total} videos, {verb} {updated}.")


if __name__ == "__main__":
    migrate(dry_run="--dry-run" in sys.argv)
