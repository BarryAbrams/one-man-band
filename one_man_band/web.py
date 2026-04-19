from __future__ import annotations

from pathlib import Path

import os

from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit

from .device import DeviceController


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
controller = DeviceController()
_poller_started = False


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
        return render_template("index.html", metadata=controller.metadata())

    @app.get("/api/state")
    def get_state():
        return jsonify(controller.read_state().to_payload())

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

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app


def _broadcast_state() -> None:
    socketio.emit("state:update", controller.read_state().to_payload())


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
    emit("meta:init", controller.metadata())
    emit("state:update", controller.read_state().to_payload())


@socketio.on("state:refresh")
def handle_refresh():
    emit("state:update", controller.read_state().to_payload())


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
