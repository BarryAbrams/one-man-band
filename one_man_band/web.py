from __future__ import annotations

from pathlib import Path

import os
import threading

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

from .animations import AnimationStore
from .audio import AudioManager
from .device import DeviceController
from .logic import LogicEngine, LogicStore
from .node_control import NodeControlMqttClient


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
controller = DeviceController()
base_dir = Path(__file__).resolve().parent.parent
audio_manager = AudioManager(base_dir)
logic_store = LogicStore(base_dir / "data" / "logic.sqlite3")
logic_engine = LogicEngine(logic_store, controller, audio_manager, on_action=lambda: _broadcast_state())
animation_store = AnimationStore(base_dir / "data" / "animations.sqlite3")
_poller_started = False
_shutdown_event = threading.Event()
STATE_POLL_SECONDS = 0.1
BOOT_ENABLED_RAILS = ["12V_B", "12V_C"]
NODE_TITLE = os.environ.get("OMB_NODE_TITLE", "Overworld Bar")
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


def _combined_state(process_logic: bool = False) -> dict[str, object]:
    device_state = controller.read_state().to_payload()
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
        return jsonify(_combined_state())

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


def _poll_state_forever() -> None:
    while not _shutdown_event.is_set():
        socketio.sleep(STATE_POLL_SECONDS)
        if _shutdown_event.is_set():
            break
        socketio.emit("state:update", _combined_state(process_logic=True))


def shutdown() -> None:
    _shutdown_event.set()
    node_control.stop()
    audio_manager.close()
    controller.close()


def _initialize_boot_hardware() -> None:
    controller.set_rails_enabled(BOOT_ENABLED_RAILS, True)


def _activate_node() -> None:
    _initialize_boot_hardware()


def _quiet_node() -> None:
    audio_manager.stop()
    controller.clear_solenoids()
    controller.set_all_servos_enabled(False)
    controller.set_all_rails(False)


def _shutdown_node() -> None:
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
        _initialize_boot_hardware()
        logic_engine.run_boot_rules()
        node_control.start()
        socketio.start_background_task(_poll_state_forever)
        _poller_started = True

    return socketio


@socketio.on("connect")
def handle_connect():
    emit("meta:init", _metadata())
    emit("state:update", _combined_state())


@socketio.on("state:refresh")
def handle_refresh():
    emit("state:update", _combined_state())


@socketio.on("rail:toggle")
def handle_rail_toggle(payload: dict[str, str]):
    try:
        state = controller.toggle_rail(payload["name"])
        emit("state:update", state.to_payload(), broadcast=True)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("rails:set_all")
def handle_rails_set_all(payload: dict[str, bool]):
    state = controller.set_all_rails(bool(payload["enabled"]))
    emit("state:update", state.to_payload(), broadcast=True)


@socketio.on("solenoid:toggle")
def handle_solenoid_toggle(payload: dict[str, str]):
    try:
        state = controller.toggle_solenoid(payload["name"])
        emit("state:update", state.to_payload(), broadcast=True)
    except (KeyError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("solenoid:set")
def handle_solenoid_set(payload: dict[str, object]):
    try:
        state = controller.set_solenoid(str(payload["name"]), bool(payload["enabled"]))
        emit("state:update", state.to_payload(), broadcast=True)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("solenoids:clear")
def handle_solenoids_clear():
    state = controller.clear_solenoids()
    emit("state:update", state.to_payload(), broadcast=True)


@socketio.on("servos:set_all")
def handle_servos_set_all(payload: dict[str, bool]):
    state = controller.set_all_servos_enabled(bool(payload["enabled"]))
    emit("state:update", state.to_payload(), broadcast=True)


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
        emit("state:update", state.to_payload(), broadcast=True)
    except (KeyError, TypeError, ValueError) as exc:
        emit("server:error", {"message": str(exc)})


@socketio.on("servo:set_value")
def handle_servo_set_value(payload: dict[str, object]):
    try:
        channel = int(payload["channel"])
        value = int(payload["value"])
        state = controller.set_servo_value(channel, value)
        emit("state:update", state.to_payload(), broadcast=True)
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
        emit("state:update", {**state.to_payload(), "audio_status": audio_manager.status().to_payload()}, broadcast=True)
    except (KeyError, TypeError, ValueError) as exc:
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
