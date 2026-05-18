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
    output: str = "both"
    error: str = ""

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


class AudioManager:
    def __init__(self, base_dir: Path) -> None:
        self._lock = threading.Lock()
        self._audio_dir = base_dir / "uploads" / "audio"
        self._audio_dir.mkdir(parents=True, exist_ok=True)
        self._status = AudioStatus(available=pygame is not None)
        self._channel = None

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
            audio_driver = os.environ.get("OMB_AUDIO_DRIVER")
            if audio_driver:
                os.environ.setdefault("SDL_AUDIODRIVER", audio_driver)

            init_args = {
                "frequency": int(os.environ.get("OMB_AUDIO_RATE", "44100")),
                "size": int(os.environ.get("OMB_AUDIO_SIZE", "-16")),
                "channels": int(os.environ.get("OMB_AUDIO_CHANNELS", "2")),
                "buffer": int(os.environ.get("OMB_AUDIO_BUFFER", "1024")),
            }
            audio_device = os.environ.get("OMB_AUDIO_DEVICE")
            if audio_device:
                init_args["devicename"] = audio_device

            pygame.mixer.init(**init_args)
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
        busy = self._channel.get_busy() if self._channel is not None else pygame.mixer.music.get_busy()
        if self._status.paused:
            self._status.playing = False
            return
        if not busy:
            self._status.playing = False
            self._status.current_track = None
            self._channel = None

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

    def delete_track(self, filename: str) -> AudioStatus:
        filename = secure_filename(filename or "")
        if not filename:
            raise ValueError("Audio file not found")

        path = self._audio_dir / filename
        if path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS or not path.is_file():
            raise ValueError(f"Audio file not found: {filename}")

        with self._lock:
            if self._status.current_track == path.name:
                if self._status.initialized and pygame is not None:
                    pygame.mixer.music.stop()
                    if self._channel is not None:
                        self._channel.stop()
                        self._channel = None
                self._status.playing = False
                self._status.paused = False
                self._status.current_track = None
            path.unlink()
            return AudioStatus(**asdict(self._status))

    def play(self, filename: str, speaker: str = "both") -> AudioStatus:
        with self._lock:
            if not self._ensure_mixer():
                return AudioStatus(**asdict(self._status))

            path = self._audio_dir / secure_filename(filename)
            if not path.is_file():
                raise ValueError(f"Audio file not found: {filename}")

            output = speaker if speaker in {"left", "right", "both"} else "both"
            try:
                pygame.mixer.music.stop()
                if self._channel is not None:
                    self._channel.stop()
                    self._channel = None

                if output == "both":
                    pygame.mixer.music.load(str(path))
                    pygame.mixer.music.set_volume(self._status.volume)
                    pygame.mixer.music.play()
                else:
                    sound = pygame.mixer.Sound(str(path))
                    channel = pygame.mixer.find_channel(True)
                    if channel is None:
                        raise RuntimeError("No pygame audio channel is available")
                    if output == "right":
                        channel.set_volume(0.0, self._status.volume)
                    else:
                        channel.set_volume(self._status.volume, 0.0)
                    channel.play(sound)
                    self._channel = channel

                self._status.playing = True
                self._status.paused = False
                self._status.current_track = path.name
                self._status.output = output
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
                if self._channel is not None:
                    self._channel.stop()
                    self._channel = None
            self._status.playing = False
            self._status.paused = False
            self._status.current_track = None
            return AudioStatus(**asdict(self._status))

    def close(self) -> None:
        with self._lock:
            if self._status.initialized and pygame is not None:
                pygame.mixer.music.stop()
                if self._channel is not None:
                    self._channel.stop()
                    self._channel = None
                pygame.mixer.quit()
            self._status.initialized = False
            self._status.playing = False
            self._status.paused = False
            self._status.current_track = None

    def pause(self) -> AudioStatus:
        with self._lock:
            if self._status.initialized and pygame is not None:
                if self._channel is not None:
                    self._channel.pause()
                else:
                    pygame.mixer.music.pause()
                self._status.paused = True
                self._status.playing = False
            return AudioStatus(**asdict(self._status))

    def resume(self) -> AudioStatus:
        with self._lock:
            if self._status.initialized and pygame is not None:
                if self._channel is not None:
                    self._channel.unpause()
                else:
                    pygame.mixer.music.unpause()
                self._status.paused = False
                self._status.playing = True
            return AudioStatus(**asdict(self._status))

    def set_volume(self, value: float) -> AudioStatus:
        with self._lock:
            clamped = max(0.0, min(1.0, value))
            self._status.volume = clamped
            if self._status.initialized and pygame is not None:
                if self._channel is not None:
                    if self._status.output == "right":
                        self._channel.set_volume(0.0, clamped)
                    elif self._status.output == "left":
                        self._channel.set_volume(clamped, 0.0)
                    else:
                        self._channel.set_volume(clamped)
                else:
                    pygame.mixer.music.set_volume(clamped)
            return AudioStatus(**asdict(self._status))
