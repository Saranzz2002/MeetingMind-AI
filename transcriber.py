"""
transcriber.py
==============
Local, free speech-to-text using Faster Whisper, with automatic audio
extraction from video containers via FFmpeg.

Design notes
------------
- The Faster Whisper model is loaded lazily and cached on the instance
  so repeated transcriptions within a single Streamlit session don't
  pay the model-load cost more than once.
- Video files are always normalized to 16kHz mono WAV before being
  handed to Whisper; this is the format Whisper's feature extractor
  expects and avoids relying on faster-whisper's own decoding of
  exotic containers.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import SUPPORTED_MEDIA_EXTENSIONS, TEMP_DIR, logger, settings

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a"}


class TranscriptionError(Exception):
    """Raised when audio extraction or transcription fails."""


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration_seconds: float
    segments: list[TranscriptSegment]


class Transcriber:
    """Wraps faster-whisper for local, CPU-only transcription."""

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ) -> None:
        self.model_size = model_size or settings.whisper_model_size
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type
        self._model = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------
    def _get_model(self):
        """Lazily download (if needed) and load the Faster Whisper model."""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "Loading Faster Whisper model '%s' (device=%s, compute_type=%s)...",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            logger.info("Faster Whisper model loaded.")
        return self._model

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @staticmethod
    def validate_extension(filename: str) -> str:
        """Return the lowercase extension if supported, else raise."""
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED_MEDIA_EXTENSIONS:
            raise TranscriptionError(
                f"Unsupported file format '{ext}'. Supported formats: "
                f"{', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}."
            )
        return ext

    # ------------------------------------------------------------------
    # FFmpeg audio extraction
    # ------------------------------------------------------------------
    def _extract_audio(self, input_path: Path) -> Path:
        """Convert any supported video/audio input to a 16kHz mono WAV
        file in TEMP_DIR using FFmpeg, and return its path."""
        output_path = TEMP_DIR / f"{input_path.stem}_16k_mono.wav"

        command = [
            "ffmpeg",
            "-y",  # overwrite
            "-i", str(input_path),
            "-ac", "1",       # mono
            "-ar", "16000",   # 16kHz sample rate, whisper's native rate
            "-vn",            # drop any video stream
            str(output_path),
        ]

        logger.info("Extracting audio: %s -> %s", input_path.name, output_path.name)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError as exc:
            raise TranscriptionError(
                "FFmpeg is not installed or not on PATH. Install FFmpeg to "
                "enable audio/video transcription."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError(
                "Audio extraction timed out after 10 minutes."
            ) from exc

        if result.returncode != 0:
            raise TranscriptionError(
                f"FFmpeg failed to extract audio: {result.stderr[-500:]}"
            )

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise TranscriptionError("FFmpeg produced no output audio file.")

        return output_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def transcribe_file(self, file_path: str | Path) -> TranscriptionResult:
        """Transcribe an uploaded audio or video file end-to-end.

        Steps
        -----
        1. Validate the extension.
        2. If it's a video (or a non-WAV audio format), normalize with
           FFmpeg first.
        3. Run Faster Whisper over the resulting WAV.
        4. Clean up any intermediate temp file.
        """
        input_path = Path(file_path)
        if not input_path.exists():
            raise TranscriptionError(f"File not found: {input_path}")

        ext = self.validate_extension(input_path.name)

        intermediate_wav: Optional[Path] = None
        try:
            if ext == ".wav":
                audio_path = input_path
            else:
                intermediate_wav = self._extract_audio(input_path)
                audio_path = intermediate_wav

            model = self._get_model()
            logger.info("Transcribing %s ...", audio_path.name)
            segments_iter, info = model.transcribe(str(audio_path), beam_size=5)

            segments = [
                TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
                for seg in segments_iter
            ]
            full_text = " ".join(s.text for s in segments).strip()

            if not full_text:
                raise TranscriptionError(
                    "Transcription produced no text. The audio may be silent, "
                    "too short, or in an unsupported language."
                )

            logger.info(
                "Transcription complete: %d segments, language=%s, duration=%.1fs",
                len(segments), info.language, info.duration,
            )

            return TranscriptionResult(
                text=full_text,
                language=info.language,
                duration_seconds=info.duration,
                segments=segments,
            )
        except TranscriptionError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as a domain error
            logger.exception("Unexpected transcription failure.")
            raise TranscriptionError(f"Transcription failed: {exc}") from exc
        finally:
            if intermediate_wav is not None and intermediate_wav.exists():
                intermediate_wav.unlink(missing_ok=True)
