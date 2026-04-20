from __future__ import annotations

from pathlib import Path

import os

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from .audio import AudioManager
from .device import DeviceController


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
controller = DeviceController()
audio_manager = AudioManager(Path(__file__).resolve().parent.parent)
_poller_started = False


def _metadata() -> dict[str, object]:
    return {
        **controller.metadata(),
        "audio_extensions": [".wav", ".mp3", ".ogg"],
    }


def _combined_state() -> dict[str, object]:
    return {
        **controller.read_state().to_payload(),
        "audio_status": audio_manager.status().to_payload(),
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

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app


def _broadcast_state() -> None:
    socketio.emit("state:update", _combined_state())


def _poll_state_forever() -> None:
    while True:
        socketio.sleep(0.5)
        _broadcast_state()


def create_socketio(app: Flask) -> SocketIO:
    global _poller_started
    socketio.init_app(app)

    if not _poller_started:
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


@socketio.on("solenoids:clear")
def handle_solenoids_clear():
    state = controller.clear_solenoids()
    emit("state:update", state.to_payload(), broadcast=True)


@socketio.on("servos:set_all")
def handle_servos_set_all(payload: dict[str, bool]):
    state = controller.set_all_servos_enabled(bool(payload["enabled"]))
    emit("state:update", state.to_payload(), broadcast=True)


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


@socketio.on("audio:play")
def handle_audio_play(payload: dict[str, str]):
    try:
        status = audio_manager.play(payload["filename"])
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
