from __future__ import annotations

from pathlib import Path

import os
import threading
import time
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

from .animations import AnimationStore
from .audio import AudioManager
from .device import DeviceController
from .logic import LogicEngine, LogicStore, publish_dmx_fade_payload
from .node_control import NodeControlMqttClient


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
controller = DeviceController()
base_dir = Path(__file__).resolve().parent.parent
audio_manager = AudioManager(base_dir)
logic_store = LogicStore(base_dir / "data" / "logic.sqlite3")
animation_store = AnimationStore(base_dir / "data" / "animations.sqlite3")
_poller_started = False
_shutdown_event = threading.Event()
STATE_POLL_SECONDS = 0.1
NODE_TITLE = os.environ.get("OMB_NODE_TITLE", "Overworld Bar")


class AnimationRunner:
    def __init__(
        self,
        store: AnimationStore,
        controller: DeviceController,
        audio_manager: AudioManager,
        on_update,
    ) -> None:
        self._store = store
        self._controller = controller
        self._audio_manager = audio_manager
        self._on_update = on_update
        self._lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}

    def play(self, name: str, interrupt: bool = False) -> None:
        animation = self._animation_by_name(name)
        if animation is None:
            return
        if interrupt:
            self.stop("")
        self.stop(animation.name)

        stop_event = threading.Event()
        active = {"animation": animation, "timers": [], "stop_event": stop_event}
        with self._lock:
            self._active[animation.name] = active

        start = time.monotonic()
        timeline = animation.timeline or {}
        tracks = [track for track in timeline.get("tracks", []) if isinstance(track, dict)]
        duration_seconds = max(0, animation.duration_ms) / 1000

        for track in tracks:
            if track.get("type") == "servo":
                continue
            for keyframe in self._sorted_keyframes(track):
                delay = max(0, (int(keyframe.get("time_ms") or 0) / 1000))
                timer = threading.Timer(delay, self._apply_keyframe, args=(track, keyframe, stop_event))
                timer.daemon = True
                timer.start()
                active["timers"].append(timer)

        if any(track.get("type") == "servo" for track in tracks):
            servo_thread = threading.Thread(
                target=self._run_servo_tracks,
                args=(tracks, animation.duration_ms, start, stop_event),
                daemon=True,
            )
            servo_thread.start()
            active["timers"].append(servo_thread)

        finish_timer = threading.Timer(duration_seconds, self._finish, args=(animation.name,))
        finish_timer.daemon = True
        finish_timer.start()
        active["timers"].append(finish_timer)
        self._on_update()

    def stop(self, name: str) -> None:
        with self._lock:
            if name:
                items = [(name, self._active.pop(name, None))]
            else:
                items = list(self._active.items())
                self._active.clear()

        for _animation_name, active in items:
            if not active:
                continue
            stop_event = active["stop_event"]
            stop_event.set()
            for timer in active["timers"]:
                if isinstance(timer, threading.Timer):
                    timer.cancel()
            animation = active["animation"]
            self._set_animation_relays_low(animation)
            self._stop_animation_audio(animation)
        self._on_update()

    def _finish(self, name: str) -> None:
        with self._lock:
            active = self._active.pop(name, None)
        if not active:
            return
        active["stop_event"].set()
        animation = active["animation"]
        self._set_animation_relays_low(animation)
        self._stop_animation_audio(animation)
        self._on_update()

    def _animation_by_name(self, name: str):
        for animation in self._store.list_animations():
            if animation.published and animation.name == name:
                return animation
        return None

    def _apply_keyframe(
        self, track: dict[str, Any], keyframe: dict[str, Any], stop_event: threading.Event
    ) -> None:
        if stop_event.is_set():
            return
        track_type = track.get("type")
        target = track.get("target") or {}
        if track_type == "solenoid" and target.get("name"):
            self._controller.set_solenoid(str(target["name"]), self._bool(keyframe.get("value")))
        elif track_type == "pixels":
            payload = self._pixel_animation_payload(track, keyframe)
            self._controller.animate_pixels(**payload)
        elif track_type == "dmx":
            publish_dmx_fade_payload(self._dmx_payload(track, keyframe))
        elif track_type == "audio" and target.get("filename"):
            self._audio_manager.play(str(target["filename"]), str(target.get("speaker") or "both"))
        self._on_update()

    def _run_servo_tracks(
        self,
        tracks: list[dict[str, Any]],
        duration_ms: int,
        started_at: float,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if elapsed_ms > duration_ms:
                break
            for track in tracks:
                if track.get("type") != "servo":
                    continue
                value = self._servo_value_at_time(track, elapsed_ms)
                target = track.get("target") or {}
                if value is not None and "channel" in target:
                    self._controller.set_servo_value(int(target["channel"]), int(round(value)))
            time.sleep(0.05)

    def _pixel_animation_payload(self, track: dict[str, Any], keyframe: dict[str, Any]) -> dict[str, Any]:
        target = track.get("target") or {}
        rails = self._pixel_rails(target)
        keyframes = self._sorted_keyframes(track)
        index = keyframes.index(keyframe) if keyframe in keyframes else -1
        next_keyframe = keyframes[index + 1] if index >= 0 and index + 1 < len(keyframes) else None
        duration_ms = (
            max(0, int(next_keyframe.get("time_ms") or 0) - int(keyframe.get("time_ms") or 0))
            if next_keyframe
            else 100
        )
        return {
            "rail_mask": self._rail_mask(rails),
            "start": self._pixel_start(target),
            "count": self._pixel_count(target),
            "start_rgb": self._hex_to_rgb(keyframe.get("color") or "#000000"),
            "end_rgb": self._pixel_end_rgb(track, keyframe, next_keyframe),
            "duration_ms": duration_ms,
            "animation_id": int(keyframe.get("animation_id", track.get("animation_id", 0)) or 0),
        }

    def _dmx_payload(self, track: dict[str, Any], keyframe: dict[str, Any]) -> dict[str, Any]:
        target = track.get("target") or {}
        brightness = keyframe.get("brightness", "default")
        if brightness != "default":
            brightness = max(0, min(255, int(brightness)))
        return {
            "hostname": os.uname().nodename,
            "source": f"one_man_band.animation.{track.get('id', 'track')}",
            "fixture_group_slug": str(target.get("fixture_group_slug") or "bar-dragon").strip(),
            "duration_ms": max(0, int(keyframe.get("duration_ms", 1000) or 0)),
            "state": str(keyframe.get("state") or "on"),
            "brightness": brightness,
            "color": str(keyframe.get("color") or "default").strip() or "default",
        }

    def _set_animation_relays_low(self, animation) -> None:
        for track in (animation.timeline or {}).get("tracks", []):
            target = track.get("target") or {}
            if track.get("type") == "solenoid" and target.get("name") and not track.get("persist"):
                self._controller.set_solenoid(str(target["name"]), False)
            if track.get("type") == "pixels" and not track.get("persist"):
                self._clear_pixels(track)

    def _stop_animation_audio(self, animation) -> None:
        if any(track.get("type") == "audio" for track in (animation.timeline or {}).get("tracks", [])):
            self._audio_manager.stop()

    def _servo_value_at_time(self, track: dict[str, Any], time_ms: int) -> float | None:
        keyframes = self._sorted_keyframes(track)
        if not keyframes:
            return None
        next_index = next(
            (index for index, keyframe in enumerate(keyframes) if int(keyframe.get("time_ms") or 0) >= time_ms),
            -1,
        )
        current = keyframes[0] if next_index <= 0 else keyframes[next_index - 1]
        next_keyframe = keyframes[0] if next_index <= 0 else keyframes[next_index] if next_index < len(keyframes) else current
        span = max(1, int(next_keyframe.get("time_ms") or 0) - int(current.get("time_ms") or 0))
        raw_t = 1 if current is next_keyframe else (time_ms - int(current.get("time_ms") or 0)) / span
        t = self._ease(max(0, min(1, raw_t)), str(current.get("easing") or "linear"))
        return float(current.get("value") or 0) + (float(next_keyframe.get("value") or 0) - float(current.get("value") or 0)) * t

    def _sorted_keyframes(self, track: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            [keyframe for keyframe in track.get("keyframes", []) if isinstance(keyframe, dict)],
            key=lambda keyframe: int(keyframe.get("time_ms") or 0),
        )

    def _pixel_rails(self, target: dict[str, Any]) -> list[str]:
        if target.get("scope") == "all":
            return list((self._controller.metadata().get("pixel_rails") or []))
        return [str(target.get("line") or "1")]

    def _pixel_start(self, target: dict[str, Any]) -> int:
        return 0 if target.get("scope") in {"line", "all"} else int(target.get("start") or 0)

    def _pixel_count(self, target: dict[str, Any]) -> int:
        if target.get("scope") == "pixel":
            return 1
        if target.get("scope") == "range":
            return int(target.get("count") or 1)
        return 0

    def _pixel_end_rgb(
        self,
        track: dict[str, Any],
        keyframe: dict[str, Any],
        next_keyframe: dict[str, Any] | None,
    ) -> tuple[int, int, int]:
        animation_id = int(keyframe.get("animation_id", track.get("animation_id", 0)) or 0)
        if animation_id == 1:
            return (
                max(0, min(255, int(keyframe.get("hue_variation", 32) or 0))),
                max(0, min(255, int(keyframe.get("seed", 17) or 0))),
                max(0, min(255, int(keyframe.get("target_intensity", 255) or 0))),
            )
        return self._hex_to_rgb((next_keyframe or keyframe).get("color") or "#000000")

    def _clear_pixels(self, track: dict[str, Any]) -> None:
        target = track.get("target") or {}
        self._controller.animate_pixels(
            rail_mask=self._rail_mask(self._pixel_rails(target)),
            start=self._pixel_start(target),
            count=self._pixel_count(target),
            start_rgb=(0, 0, 0),
            end_rgb=(0, 0, 0),
            duration_ms=0,
            animation_id=0,
        )

    def _rail_mask(self, rails: list[str]) -> int:
        mask = 0
        for rail in rails:
            mask |= 1 << (int(rail) - 1)
        return mask

    def _hex_to_rgb(self, value: Any) -> tuple[int, int, int]:
        color = str(value or "#000000").lstrip("#")
        if len(color) != 6:
            color = "000000"
        return tuple(int(color[index : index + 2], 16) for index in (0, 2, 4))

    def _ease(self, t: float, easing: str) -> float:
        if easing == "ease-in":
            return t * t
        if easing == "ease-out":
            return 1 - ((1 - t) ** 2)
        if easing == "ease":
            return t * t * (3 - 2 * t)
        return t

    def _bool(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "low", "off", "no"}
        return bool(value)


animation_runner = AnimationRunner(animation_store, controller, audio_manager, on_update=lambda: _broadcast_state())
logic_engine = LogicEngine(
    logic_store,
    controller,
    audio_manager,
    on_action=lambda: _broadcast_state(),
    on_animation_start=animation_runner.play,
    on_animation_stop=animation_runner.stop,
)
node_control = NodeControlMqttClient(
    on_active=lambda: _activate_node(),
    on_quiet=lambda: _quiet_node(),
    on_shutdown=lambda: _shutdown_node(),
    on_cleanup=lambda: shutdown(),
    on_state_changed=lambda: _broadcast_state(),
)


def _metadata() -> dict[str, object]:
    return {
        **controller.metadata(),
        **logic_engine.metadata(),
        "node_title": NODE_TITLE,
        "audio_extensions": [".wav", ".mp3", ".ogg"],
    }


def _combined_state(process_logic: bool = False, refresh_hardware: bool = False) -> dict[str, object]:
    if refresh_hardware:
        device_state = controller.read_state().to_payload()
    else:
        device_state = controller.read_cached_state(refresh_gpio=True).to_payload()
    if process_logic:
        logic_engine.process_state(device_state)
    return {
        **device_state,
        "audio_status": audio_manager.status().to_payload(),
        "logic_timers": logic_engine.timers_payload(),
        "logic_action_events": logic_engine.action_events_payload(),
        "node_control": node_control.payload(),
    }


def create_app() -> Flask:
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_dir.parent / "templates"),
        static_folder=str(package_dir.parent / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("OMB_SECRET_KEY", "one-man-band-local")

    @app.get("/")
    def index() -> str:
        return render_template("index.html", metadata=_metadata())

    @app.get("/api/state")
    def get_state():
        return jsonify(_combined_state(refresh_hardware=request.args.get("refresh") == "1"))

    @app.get("/api/health")
    def health():
        state = controller.read_state()
        return jsonify(
            {
                "ok": state.connected,
                "backend": state.backend,
                "error": state.error,
            }
        )

    @app.get("/api/audio")
    def audio_library():
        return jsonify(
            {
                "tracks": audio_manager.list_tracks(),
                "status": audio_manager.status().to_payload(),
            }
        )

    @app.get("/api/audio/files/<path:filename>")
    def audio_file(filename: str):
        return send_from_directory(audio_manager.audio_dir, filename)

    @app.get("/api/logic")
    def logic_rules():
        return jsonify(logic_engine.rules_payload())

    @app.get("/api/animations")
    def animations_list():
        return jsonify({"animations": [animation.to_payload() for animation in animation_store.list_animations()]})

    @app.post("/api/animations")
    def animations_create():
        try:
            animation = animation_store.upsert_animation(request.get_json(force=True) or {})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "animation": animation.to_payload()})

    @app.put("/api/animations/<int:animation_id>")
    def animations_update(animation_id: int):
        payload = request.get_json(force=True) or {}
        payload["id"] = animation_id
        try:
            animation = animation_store.upsert_animation(payload)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "animation": animation.to_payload()})

    @app.delete("/api/animations/<int:animation_id>")
    def animations_delete(animation_id: int):
        animation_store.delete_animation(animation_id)
        return jsonify({"ok": True})

    @app.post("/api/logic")
    def logic_create_rule():
        try:
            rule = logic_engine.save_rule(request.get_json(force=True) or {})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "rule": rule.to_payload()})

    @app.put("/api/logic/<int:rule_id>")
    def logic_update_rule(rule_id: int):
        payload = request.get_json(force=True) or {}
        payload["id"] = rule_id
        try:
            rule = logic_engine.save_rule(payload)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "rule": rule.to_payload()})

    @app.delete("/api/logic/<int:rule_id>")
    def logic_delete_rule(rule_id: int):
        logic_engine.delete_rule(rule_id)
        return jsonify({"ok": True})

    @app.post("/api/logic/<int:rule_id>/run")
    def logic_run_rule(rule_id: int):
        try:
            rule = logic_engine.run_rule_now(rule_id)
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "rule": rule.to_payload()})

    @app.post("/api/audio/upload")
    def audio_upload():
        file = request.files.get("file")
        if file is None:
            return jsonify({"ok": False, "error": "No file was uploaded"}), 400
        try:
            filename = audio_manager.save_upload(file)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify(
            {
                "ok": True,
                "filename": filename,
                "tracks": audio_manager.list_tracks(),
                "status": audio_manager.status().to_payload(),
            }
        )

    @app.delete("/api/audio/files/<path:filename>")
    def audio_delete(filename: str):
        try:
            status = audio_manager.delete_track(filename)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        payload = {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}
        socketio.emit("audio:update", payload)
        return jsonify({"ok": True, **payload})

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app


def _broadcast_state() -> None:
    socketio.emit("state:update", _combined_state())


def _broadcast_i2c_write_state(state) -> None:
    socketio.emit(
        "state:update",
        {
            **state.to_payload(),
            "audio_status": audio_manager.status().to_payload(),
            "logic_timers": logic_engine.timers_payload(),
            "logic_action_events": logic_engine.action_events_payload(),
            "node_control": node_control.payload(),
        },
    )


def _emit_state_without_i2c_write(state, include_audio: bool = False) -> None:
    if state.backend == "i2c" and state.connected:
        return
    payload = state.to_payload()
    if include_audio:
        payload = {**payload, "audio_status": audio_manager.status().to_payload()}
    emit("state:update", payload, broadcast=True)


controller.set_i2c_write_listener(_broadcast_i2c_write_state)


def _poll_state_forever() -> None:
    while not _shutdown_event.is_set():
        socketio.sleep(STATE_POLL_SECONDS)
        if _shutdown_event.is_set():
            break
        socketio.emit("state:update", _combined_state(process_logic=True))


def shutdown() -> None:
    _shutdown_event.set()
    animation_runner.stop("")
    node_control.stop()
    audio_manager.close()
    controller.close()


def _activate_node() -> None:
    logic_engine.process_global_state("active")


def _quiet_node() -> None:
    animation_runner.stop("")
    audio_manager.stop()
    controller.clear_solenoids()
    controller.set_all_servos_enabled(False)
    logic_engine.process_global_state("quiet")


def _shutdown_node() -> None:
    logic_engine.process_global_state("shutdown")
    controller.animate_pixels(
        rail_mask=0x0F,
        start=0,
        count=0,
        start_rgb=(0, 0, 0),
        end_rgb=(0, 0, 0),
        duration_ms=0,
        animation_id=0,
    )


def create_socketio(app: Flask) -> SocketIO:
    global _poller_started
    socketio.init_app(app)

    if not _poller_started:
        logic_engine.run_boot_rules()
        node_control.start()
        # socketio.start_background_task(_poll_state_forever)
        # _poller_started = True

    return socketio


@socketio.on("connect")
def handle_connect():
    emit("meta:init", _metadata())
    emit("state:update", _combined_state())


@socketio.on("state:refresh")
def handle_refresh():
    emit("state:update", _combined_state(refresh_hardware=True))


@socketio.on("rail:toggle")
def handle_rail_toggle(payload: dict[str, str]):
    try:
        state = controller.toggle_rail(payload["name"])
        _emit_state_without_i2c_write(state)
        print(f"Toggled rail {payload['name']}")
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("rails:set_all")
def handle_rails_set_all(payload: dict[str, bool]):
    state = controller.set_all_rails(bool(payload["enabled"]))
    _emit_state_without_i2c_write(state)


@socketio.on("solenoid:toggle")
def handle_solenoid_toggle(payload: dict[str, str]):
    try:
        state = controller.toggle_solenoid(payload["name"])
        _emit_state_without_i2c_write(state)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("solenoid:set")
def handle_solenoid_set(payload: dict[str, object]):
    try:
        state = controller.set_solenoid(str(payload["name"]), bool(payload["enabled"]))
        _emit_state_without_i2c_write(state)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("solenoids:clear")
def handle_solenoids_clear():
    state = controller.clear_solenoids()
    _emit_state_without_i2c_write(state)


@socketio.on("servos:set_all")
def handle_servos_set_all(payload: dict[str, bool]):
    state = controller.set_all_servos_enabled(bool(payload["enabled"]))
    _emit_state_without_i2c_write(state)


@socketio.on("gpio:override_toggle")
def handle_gpio_override_toggle(payload: dict[str, str]):
    try:
        state = controller.toggle_gpio_override(payload["name"])
        emit("state:update", {**state.to_payload(), "audio_status": audio_manager.status().to_payload()}, broadcast=True)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("gpio:override_clear")
def handle_gpio_override_clear(payload: dict[str, str]):
    try:
        state = controller.clear_gpio_override(payload["name"])
        emit("state:update", {**state.to_payload(), "audio_status": audio_manager.status().to_payload()}, broadcast=True)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("gpio:override_clear_all")
def handle_gpio_override_clear_all():
    state = controller.clear_all_gpio_overrides()
    emit("state:update", {**state.to_payload(), "audio_status": audio_manager.status().to_payload()}, broadcast=True)


@socketio.on("servo:set_enabled")
def handle_servo_set_enabled(payload: dict[str, object]):
    try:
        channel = int(payload["channel"])
        enabled = bool(payload["enabled"])
        state = controller.set_servo_enabled(channel, enabled)
        _emit_state_without_i2c_write(state)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("servo:set_value")
def handle_servo_set_value(payload: dict[str, object]):
    try:
        channel = int(payload["channel"])
        value = int(payload["value"])
        state = controller.set_servo_value(channel, value)
        _emit_state_without_i2c_write(state)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("pixels:animate")
def handle_pixels_animate(payload: dict[str, object]):
    try:
        rails = payload.get("rails", [])
        if not isinstance(rails, list):
            raise ValueError("Pixel rails must be a list")
        rail_mask = 0
        for rail in rails:
            rail_mask |= 1 << (int(rail) - 1)

        state = controller.animate_pixels(
            rail_mask=rail_mask,
            start=int(payload["start"]),
            count=int(payload["count"]),
            start_rgb=tuple(int(value) for value in payload["start_rgb"]),
            end_rgb=tuple(int(value) for value in payload["end_rgb"]),
            duration_ms=int(payload["duration_ms"]),
            animation_id=int(payload.get("animation_id", 0)),
        )
        _emit_state_without_i2c_write(state, include_audio=True)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("dmx:fade")
def handle_dmx_fade(payload: dict[str, object]):
    try:
        brightness = payload.get("brightness", "default")
        if brightness != "default":
            brightness = max(0, min(255, int(brightness)))
        publish_dmx_fade_payload(
            {
                "hostname": os.uname().nodename,
                "source": "one_man_band.animation_preview",
                "fixture_group_slug": str(payload.get("fixture_group_slug") or "bar-dragon").strip(),
                "duration_ms": max(0, int(payload.get("duration_ms", 1000) or 0)),
                "state": str(payload.get("state") or "on"),
                "brightness": brightness,
                "color": str(payload.get("color") or "default").strip() or "default",
            }
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("audio:play")
def handle_audio_play(payload: dict[str, str]):
    try:
        status = audio_manager.play(payload["filename"], payload.get("speaker", "both"))
        emit("audio:update", {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}, broadcast=True)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("audio:stop")
def handle_audio_stop():
    status = audio_manager.stop()
    emit("audio:update", {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}, broadcast=True)


@socketio.on("audio:pause")
def handle_audio_pause():
    status = audio_manager.pause()
    emit("audio:update", {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}, broadcast=True)


@socketio.on("audio:resume")
def handle_audio_resume():
    status = audio_manager.resume()
    emit("audio:update", {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}, broadcast=True)


@socketio.on("audio:set_volume")
def handle_audio_set_volume(payload: dict[str, object]):
    try:
        status = audio_manager.set_volume(float(payload["volume"]))
        emit("audio:update", {"status": status.to_payload(), "tracks": audio_manager.list_tracks()}, broadcast=True)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})
