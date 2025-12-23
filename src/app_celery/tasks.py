from src.app_celery.celery_app import celery_app
import time
import logging
from src.core.database_sync import mongodb_sync

logger = logging.getLogger(__name__)

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

@celery_app.task(name="process_music_video")
def process_music_video(s3_key: str):
    # 🔥 GUARANTEED DB INIT
    mongodb_sync.connect()
    video_path = download_video_from_s3(s3_key)
    logger.info("Downloaded video to:", video_path)
    
    audio_path = extract_audio_from_video(video_path)
    logger.info("Extracted audio to:", audio_path)
    
    features = analyze_audio_features(audio_path)
    logger.info("Analyzed audio features.", features)
    logger.info("Feature keys: %s", list(features.keys()))
    logger.info("Feature summary: %s", {k: type(v) for k, v in features.items()})

    chord_result = detect_chords(features)
    logger.info(f"Detected chords. Result: {chord_result}")
    
    rhythm_result = detect_rhythm(features)
    logger.info(f"Detected rhythm. Result: {rhythm_result}")
    
    performance_score = evaluate_performance(
        chord_result,
        rhythm_result
    )
    logger.info(f"Evaluated performance. Score: {performance_score}")
    
    save_analysis_result(
        s3_key,
        chord_result,
        rhythm_result,
        performance_score
    )

    update_video_status(s3_key, "processed")
    logger.info(f"Updated video status to 'processed' for {s3_key}")
    
    return {"s3_key": s3_key, "status": "completed"}

