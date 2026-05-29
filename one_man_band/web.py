from __future__ import annotations

from pathlib import Path

import os

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from .diagnostics import CanSensorReader, DiagnosticService, DiagnosticStore, MqttWarningPublisher


socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
base_dir = Path(__file__).resolve().parent.parent
store = DiagnosticStore(base_dir / "data" / "compressor_diagnostics.sqlite3")
service = DiagnosticService(store, CanSensorReader(), MqttWarningPublisher())
_poller_started = False

NODE_TITLE = os.environ.get("BAC_NODE_TITLE", "Backstage Air Compressor")


def _metadata() -> dict[str, object]:
    return {
        "node_title": NODE_TITLE,
        "sensor_labels": {
            "temperature_c": "Temperature",
            "humidity_pct": "Humidity",
            "vibration_g": "Vibration",
            "float_switch": "Float Switch",
        },
        "units": {
            "temperature_c": "F",
            "humidity_pct": "%",
            "vibration_g": "g",
            "current_a": "A",
        },
        "history_windows": [15, 60, 240, 1440],
    }


def create_app() -> Flask:
    package_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(package_dir.parent / "templates"),
        static_folder=str(package_dir.parent / "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("BAC_SECRET_KEY", "compressor-diagnostics-local")

    @app.get("/")
    def index() -> str:
        return render_template("index.html", metadata=_metadata())

    @app.get("/api/state")
    def get_state():
        minutes = int(request.args.get("minutes", "60"))
        return jsonify(service.payload(history_minutes=minutes))

    @app.get("/api/health")
    def health():
        payload = service.payload(history_minutes=1)
        return jsonify(
            {
                "ok": payload["connected"],
                "backend": payload["backend"],
                "error": payload["error"],
                "mqtt": payload["mqtt"],
            }
        )

    @app.post("/api/warnings/<code>/ack")
    def acknowledge_warning(code: str):
        warnings = service.acknowledge_warning(code)
        socketio.emit("warnings:update", warnings)
        return jsonify({"ok": True, "warnings": warnings})

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app


def _broadcast_state() -> None:
    socketio.emit("state:update", service.payload())


def create_socketio(app: Flask) -> SocketIO:
    global _poller_started
    socketio.init_app(app)
    if not _poller_started:
        service.start(on_update=_broadcast_state)
        _poller_started = True
    return socketio


def shutdown() -> None:
    service.stop()


@socketio.on("connect")
def handle_connect():
    emit("meta:init", _metadata())
    emit("state:update", service.payload())


@socketio.on("state:refresh")
def handle_refresh(payload: dict[str, object] | None = None):
    minutes = 60
    if payload:
        minutes = int(payload.get("minutes") or minutes)
    emit("state:update", service.payload(history_minutes=minutes))
