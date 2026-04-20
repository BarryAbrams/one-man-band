from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from werkzeug.utils import secure_filename

try:
    import pygame
except ImportError:  # pragma: no cover - depends on target hardware
    pygame = None


ALLOWED_AUDIO_EXTENSIONS: Final[set[str]] = {".wav", ".mp3", ".ogg"}


@dataclass(slots=True)
class AudioStatus:
    available: bool = False
    initialized: bool = False
    playing: bool = False
    paused: bool = False
    current_track: str | None = None
    volume: float = 1.0
    error: str = ""

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


class AudioManager:
    def __init__(self, base_dir: Path) -> None:
        self._lock = threading.Lock()
        self._audio_dir = base_dir / "uploads" / "audio"
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        self._status = AudioStatus(available=pygame is not None)

    @property
    def audio_dir(self) -> Path:
        return self._audio_dir

    def _ensure_mixer(self) -> bool:
        if pygame is None:
            self._status.error = "pygame is not installed in this Python environment"
            self._status.available = False
            return False

        if self._status.initialized:
            return True

        try:
            pygame.mixer.init(
                frequency=int(os.environ.get("OMB_AUDIO_RATE", "44100")),
                size=int(os.environ.get("OMB_AUDIO_SIZE", "-16")),
                channels=int(os.environ.get("OMB_AUDIO_CHANNELS", "2")),
                buffer=int(os.environ.get("OMB_AUDIO_BUFFER", "1024")),
            )
            pygame.mixer.music.set_volume(self._status.volume)
            self._status.available = True
            self._status.initialized = True
            self._status.error = ""
            return True
        except Exception as exc:  # pragma: no cover - depends on target hardware
            self._status.error = str(exc)
            self._status.available = False
            self._status.initialized = False
            return False

    def _refresh_status_locked(self) -> None:
        if not self._status.initialized or pygame is None:
            return
        busy = pygame.mixer.music.get_busy()
        if self._status.paused:
            self._status.playing = False
            return
        if not busy:
            self._status.playing = False
            self._status.current_track = None

    def status(self) -> AudioStatus:
        with self._lock:
            self._refresh_status_locked()
            return AudioStatus(**asdict(self._status))

    def list_tracks(self) -> list[dict[str, object]]:
        tracks: list[dict[str, object]] = []
        for path in sorted(self._audio_dir.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
                continue
            tracks.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "extension": path.suffix.lower().lstrip("."),
                }
            )
        return tracks

    def save_upload(self, storage) -> str:
        filename = secure_filename(storage.filename or "")
        if not filename:
            raise ValueError("No audio file selected")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_AUDIO_EXTENSIONS:
            raise ValueError("Supported formats: .wav, .mp3, .ogg")
        target = self._audio_dir / filename
        storage.save(target)
        return filename

    def play(self, filename: str) -> AudioStatus:
        with self._lock:
            if not self._ensure_mixer():
                return AudioStatus(**asdict(self._status))

            path = self._audio_dir / secure_filename(filename)
            if not path.is_file():
                raise ValueError(f"Audio file not found: {filename}")

            try:
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.play()
                self._status.playing = True
                self._status.paused = False
                self._status.current_track = path.name
                self._status.error = ""
            except Exception as exc:  # pragma: no cover - depends on target hardware
                self._status.error = str(exc)
                self._status.playing = False
                self._status.paused = False
            return AudioStatus(**asdict(self._status))

    def stop(self) -> AudioStatus:
        with self._lock:
            if self._status.initialized and pygame is not None:
                pygame.mixer.music.stop()
            self._status.playing = False
            self._status.paused = False
            self._status.current_track = None
            return AudioStatus(**asdict(self._status))

    def pause(self) -> AudioStatus:
        with self._lock:
            if self._status.initialized and pygame is not None:
                pygame.mixer.music.pause()
                self._status.paused = True
                self._status.playing = False
            return AudioStatus(**asdict(self._status))

    def resume(self) -> AudioStatus:
        with self._lock:
            if self._status.initialized and pygame is not None:
                pygame.mixer.music.unpause()
                self._status.paused = False
                self._status.playing = True
            return AudioStatus(**asdict(self._status))

    def set_volume(self, value: float) -> AudioStatus:
        with self._lock:
            clamped = max(0.0, min(1.0, value))
            self._status.volume = clamped
            if self._status.initialized and pygame is not None:
                pygame.mixer.music.set_volume(clamped)
            return AudioStatus(**asdict(self._status))
