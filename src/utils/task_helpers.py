import boto3
import os
import subprocess
import tempfile
from dotenv import load_dotenv
from pathlib import Path
import librosa
import numpy as np
from src.core.database import mongodb
from datetime import datetime
import logging
from src.core.database_sync import mongodb_sync

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[2]
# Load .env only if it exists
dotenv_path = BASE_DIR / ".env.development"
print("Loading .env from:", dotenv_path)
if dotenv_path.exists():
    load_dotenv(dotenv_path)


# Safety check (optional but recommended)
required_vars = ["AWS_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    raise RuntimeError(f"Missing env vars: {missing}")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)



def download_video_from_s3(s3_key: str) -> str:
    """
    Downloads video from S3 using s3_key
    Returns local file path
    """
    BUCKET_NAME = os.getenv("AWS_S3_BUCKET")
    print("S3 Bucket Name in task_helpers:", BUCKET_NAME)
    if not BUCKET_NAME:
        raise RuntimeError("AWS_S3_BUCKET is not set")
    if not s3_key:
        raise RuntimeError("s3_key is missing")
    
    tmp_dir = tempfile.mkdtemp()
    local_path = os.path.join(tmp_dir, os.path.basename(s3_key))
    
    s3_client.download_file(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Filename=local_path
    )

    return local_path



def extract_audio_from_video(video_path: str) -> str:
    """
    Extracts audio from a video file using FFmpeg.

    Args:
        video_path (str): Local path to the downloaded video file

    Returns:
        str: Local path to the extracted audio (.wav)

    Raises:
        RuntimeError: If ffmpeg fails or video path is invalid
    """

    # ---------- Validation ----------
    if not video_path:
        raise RuntimeError("Video path is missing")

    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file does not exist: {video_path}")

    # ---------- Output audio path ----------
    # Example:
    # video_path = /tmp/tmp123/video.mp4
    # audio_path = /tmp/tmp123/video.wav
    base, _ = os.path.splitext(video_path)
    audio_path = f"{base}.wav"

    # ---------- FFmpeg command ----------
    # -y        → overwrite output if exists
    # -i        → input file
    # -vn       → disable video recording
    # -acodec   → audio codec (PCM = uncompressed, ML-friendly)
    # -ar       → sample rate (44.1 kHz is standard)
    # -ac       → number of channels (mono is better for ML)
    command = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        audio_path
    ]

    # ---------- Run FFmpeg ----------
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True
        )
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode()
        raise RuntimeError(f"FFmpeg failed to extract audio: {error_msg}")

    # ---------- Final verification ----------
    if not os.path.exists(audio_path):
        raise RuntimeError("Audio extraction failed, output file not found")

    return audio_path




def analyze_audio_features(audio_path: str) -> dict:
    """
    Analyzes core musical features from an audio file.

    Args:
        audio_path (str): Path to extracted WAV audio file

    Returns:
        dict: Dictionary containing extracted audio features
    """

    # ---------- Validation ----------
    if not audio_path:
        raise RuntimeError("Audio path is missing")

    if not os.path.exists(audio_path):
        raise RuntimeError(f"Audio file not found: {audio_path}")

    # ---------- Load audio ----------
    # y  → audio time series
    # sr → sampling rate
    y, sr = librosa.load(audio_path, sr=None, mono=True)

    # ---------- Tempo & rhythm ----------
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)

    # ---------- Onset detection (strumming / attacks) ----------
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_times = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        units="time"
    )

    # ---------- Spectral features ----------
    spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
    spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)

    # ---------- Energy & dynamics ----------
    rms_energy = librosa.feature.rms(y=y)
    zero_crossing_rate = librosa.feature.zero_crossing_rate(y)

    # ---------- Pitch / harmony (for chords) ----------
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)

    # ---------- Aggregate features ----------
    # ML models prefer summarized statistics over raw frames
    features = {
        "duration_sec": float(librosa.get_duration(y=y, sr=sr)),
        "tempo_bpm": float(tempo),

        "onset_count": int(len(onset_times)),

        "rms_energy_mean": float(np.mean(rms_energy)),
        "rms_energy_std": float(np.std(rms_energy)),

        "spectral_centroid_mean": float(np.mean(spectral_centroid)),
        "spectral_rolloff_mean": float(np.mean(spectral_rolloff)),

        "zero_crossing_rate_mean": float(np.mean(zero_crossing_rate)),

        # Chroma → 12 pitch classes (C, C#, D, ...)
        "chroma_mean": np.mean(chroma, axis=1).tolist()
    }

    return features



def detect_chords(features):
    # Extract mean chroma vector (12 pitch class energies)
    chroma = np.array(features.get("chroma_mean", []))

    # Ensure chroma has exactly 12 values
    if chroma.size != 12:
        return {"error": "Invalid chroma_mean; expected 12 pitch classes"}

    # Normalize chroma to reduce volume influence
    chroma = chroma / (np.linalg.norm(chroma) + 1e-6)

    # Define pitch class names in chromatic order
    pitch_classes = ["C", "C#", "D", "D#", "E", "F",
                     "F#", "G", "G#", "A", "A#", "B"]

    # Define chord templates using pitch class activation
    chord_templates = {
        "major": np.array([1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0]),
        "minor": np.array([1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0]),
    }

    detected_chords = []

    # Iterate over all 12 possible root notes
    for i, root in enumerate(pitch_classes):
        for chord_type, template in chord_templates.items():
            # Rotate chord template to match current root note
            rotated_template = np.roll(template, i)

            # Compute similarity score between chroma and chord template
            confidence = float(np.dot(chroma, rotated_template))

            # Store chord candidate with confidence score
            detected_chords.append({
                "chord": f"{root} {chord_type}",
                "confidence": round(confidence, 4)
            })

    # Sort chord candidates by confidence (highest first)
    detected_chords.sort(key=lambda x: x["confidence"], reverse=True)

    # Return top chord and top-N alternatives
    return {
        "top_chord": detected_chords[0],
        "alternatives": detected_chords[:5]
    }


def detect_rhythm(features):
    # Extract required rhythm-related features
    tempo = features.get("tempo_bpm", 0.0)
    onset_count = features.get("onset_count", 0)
    duration = features.get("duration_sec", 1.0)
    rms_mean = features.get("rms_energy_mean", 0.0)
    rms_std = features.get("rms_energy_std", 0.0)

    # Avoid division by zero for very short or invalid audio
    if duration <= 0:
        return {"error": "Invalid audio duration"}

    # Calculate average strums per second
    strums_per_second = onset_count / duration

    # Calculate energy consistency (lower std → more consistent rhythm)
    if rms_mean > 0:
        energy_consistency = 1 - min(rms_std / rms_mean, 1.0)
    else:
        energy_consistency = 0.0

    # Normalize tempo score assuming common guitar tempos (60–180 BPM)
    if 60 <= tempo <= 180:
        tempo_score = 1.0
    else:
        tempo_score = 0.5  # still acceptable but unstable

    # Combine rhythm factors into a final rhythm score
    rhythm_score = round(
        (0.4 * energy_consistency) +
        (0.3 * tempo_score) +
        (0.3 * min(strums_per_second / 4, 1.0)),
        3
    )

    # Classify rhythm quality based on score
    if rhythm_score >= 0.8:
        rhythm_quality = "steady"
    elif rhythm_score >= 0.5:
        rhythm_quality = "moderate"
    else:
        rhythm_quality = "unstable"

    # Return rhythm analysis result
    return {
        "tempo_bpm": round(tempo, 2),
        "strums_per_second": round(strums_per_second, 2),
        "energy_consistency": round(energy_consistency, 3),
        "rhythm_score": rhythm_score,
        "rhythm_quality": rhythm_quality
    }


def evaluate_performance(chord_result, rhythm_result):
    # Extract chord confidence score
    chord_confidence = chord_result.get("top_chord", {}).get("confidence", 0.0)

    # Extract rhythm score
    rhythm_score = rhythm_result.get("rhythm_score", 0.0)

    # Clamp scores to valid range
    chord_confidence = min(max(chord_confidence, 0.0), 1.0)
    rhythm_score = min(max(rhythm_score, 0.0), 1.0)

    # Compute weighted performance score
    final_score = (0.6 * chord_confidence) + (0.4 * rhythm_score)

    # Convert to percentage
    final_score_percent = round(final_score * 100, 1)

    # Assign performance grade based on score
    if final_score_percent >= 85:
        grade = "Excellent"
        feedback = "Strong chord accuracy with very steady rhythm."
    elif final_score_percent >= 70:
        grade = "Good"
        feedback = "Mostly correct chords with decent rhythm stability."
    elif final_score_percent >= 50:
        grade = "Fair"
        feedback = "Chords are recognizable but rhythm needs improvement."
    else:
        grade = "Needs Practice"
        feedback = "Work on both chord accuracy and rhythmic consistency."

    # Return final performance evaluation
    return {
        "score": final_score_percent,
        "grade": grade,
        "feedback": feedback,
        "details": {
            "chord_confidence": round(chord_confidence, 3),
            "rhythm_score": round(rhythm_score, 3)
        }
    }

def save_analysis_result(s3_key, chord_result, rhythm_result, performance_score):
    if mongodb_sync.db is None:
        raise RuntimeError("MongoDB (sync) not initialized")

    update_payload = {
        "analysis": {
            "chords": chord_result,
            "rhythm": rhythm_result,
            "performance_score": performance_score,
        },
        "status": "analyzed",
        "analyzed_at": datetime.utcnow(),
    }

    result = mongodb_sync.db["videos"].update_one(
        {"s3_key": s3_key},
        {"$set": update_payload}
    )

    if result.matched_count == 0:
        logger.warning(f"No video found for s3_key={s3_key}")
    else:
        logger.info(f"Analysis saved for s3_key={s3_key}")

def update_video_status(
    s3_key: str,
    status: str
):
    """
    Updates processing status of a video in the database.

    Parameters:
    - db: MongoDB database instance
    - s3_key (str): Unique identifier of the video in S3
    - status (str): Current processing status
      (e.g. uploaded, processing, analyzed, failed)
    """

    try:
        result = mongodb_sync.db["videos"].update_one(
            {"s3_key": s3_key},
            {
                "$set": {
                    "status": status,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        if result.matched_count == 0:
            logger.warning(f"No video found for s3_key={s3_key}")
        else:
            logger.info(
                f"Video status updated | s3_key={s3_key} | status={status}"
            )

    except Exception:
        logger.exception(
            f"Failed to update video status for s3_key={s3_key}"
        )
        raise