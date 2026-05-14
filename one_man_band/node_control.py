from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from typing import Any, Callable

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - depends on deployment extras
    mqtt = None


DEFAULT_BROKER_HOST = "192.168.0.153"
DEFAULT_BROKER_PORT = 1883
STATE_REQUEST_TOPIC = "parcadia/to_gmmy/state_request"
STATUS_TOPIC = "parcadia/to_gmmy/status"
STATE_TOPIC = "parcadia/to_gmmy/state"
GLOBAL_STATE_TOPIC = "parcadia/global/global_state"
WHO_IS_THERE_TOPIC = "parcadia/global/whoisthere"
ACCEPTED_STATES = {
    "active",
    "open",
    "quiet",
    "standby",
    "restart",
    "reboot",
    "shutdown",
}
STATE_ALIASES = {
    "open": "active",
    "standby": "quiet",
    "deactivate": "quiet",
}


class NodeControlMqttClient:
    def __init__(
        self,
        *,
        on_active: Callable[[], None],
        on_quiet: Callable[[], None],
        on_shutdown: Callable[[], None],
        on_cleanup: Callable[[], None],
        on_state_changed: Callable[[], None] | None = None,
    ) -> None:
        self.hostname = os.environ.get("OMB_MQTT_HOSTNAME", socket.gethostname())
        self.broker_host = os.environ.get("OMB_MQTT_BROKER_HOST", DEFAULT_BROKER_HOST)
        self.broker_port = int(os.environ.get("OMB_MQTT_BROKER_PORT", str(DEFAULT_BROKER_PORT)))
        self.enabled = os.environ.get("OMB_MQTT_ENABLED", "1") == "1"
        self.allow_system_commands = os.environ.get("OMB_MQTT_ALLOW_SYSTEM_COMMANDS", "0") == "1"
        self._on_active = on_active
        self._on_quiet = on_quiet
        self._on_shutdown = on_shutdown
        self._on_cleanup = on_cleanup
        self._on_state_changed = on_state_changed
        self._lock = threading.RLock()
        self._client = None
        self._state = os.environ.get("OMB_INITIAL_NODE_STATE", "active")
        self._message = "MQTT node control has not started"
        self._connected = False

    @property
    def node_topic(self) -> str:
        return f"parcadia/{self.hostname}/global_state"

    def start(self) -> None:
        if not self.enabled:
            self._set_status(self._state, False, "MQTT node control disabled")
            return
        if mqtt is None:
            self._set_status(self._state, False, "paho-mqtt is not installed")
            return
        if self._client is not None:
            return

        self._client = self._create_client()
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        try:
            self._client.connect_async(self.broker_host, self.broker_port, keepalive=30)
            self._client.loop_start()
            self._set_status(self._state, False, "MQTT node control connecting")
        except Exception as exc:
            self._set_status(self._state, False, f"MQTT connect failed: {exc}")

    def stop(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        self._set_status(self._state, False, "MQTT node control stopped")

    def payload(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "connected": self._connected,
                "hostname": self.hostname,
                "broker_host": self.broker_host,
                "broker_port": self.broker_port,
                "allow_system_commands": self.allow_system_commands,
                "state": self._state,
                "status": {
                    "code": 200 if self._connected else 503,
                    "message": self._message,
                },
            }

    def publish_status(self, message: str = "OK") -> None:
        payload = self.payload()
        payload["status"] = {"code": 200 if payload["connected"] else 503, "message": message}
        self._publish(STATUS_TOPIC, payload)
        self._publish(
            STATE_TOPIC,
            {
                "hostname": self.hostname,
                "state": {
                    "state": payload["state"],
                    "status": payload["status"],
                },
            },
        )

    def _create_client(self):
        client_id = f"one-man-band-{self.hostname}"
        if hasattr(mqtt, "CallbackAPIVersion"):
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        return mqtt.Client(client_id=client_id)

    def _handle_connect(self, client, _userdata, _flags, reason_code, _properties=None) -> None:
        if self._reason_code_value(reason_code) != 0:
            self._set_status(self._state, False, f"MQTT connect failed: {reason_code}")
            return
        self._set_status(self._state, True, "OK")
        client.subscribe(GLOBAL_STATE_TOPIC)
        client.subscribe(self.node_topic)
        client.subscribe(WHO_IS_THERE_TOPIC)
        self._publish(STATE_REQUEST_TOPIC, {"hostname": self.hostname})
        self.publish_status("OK")

    def _handle_disconnect(self, _client, _userdata, _flags=None, reason_code=None, _properties=None) -> None:
        message = "MQTT disconnected"
        if reason_code is not None and self._reason_code_value(reason_code) != 0:
            message = f"MQTT disconnected: {reason_code}"
        self._set_status(self._state, False, message)

    def _handle_message(self, _client, _userdata, message) -> None:
        topic = str(message.topic)
        if topic == WHO_IS_THERE_TOPIC:
            self.publish_status("OK")
            return
        if topic not in {GLOBAL_STATE_TOPIC, self.node_topic}:
            return

        try:
            payload = json.loads(message.payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._set_status(self._state, self._connected, f"Invalid MQTT payload: {exc}")
            self.publish_status("Invalid MQTT payload")
            return

        requested = str(payload.get("state") or "").strip().lower()
        self.handle_state(requested)

    def handle_state(self, requested: str) -> None:
        if requested not in ACCEPTED_STATES and requested not in STATE_ALIASES:
            self._set_status(self._state, self._connected, f"Ignored unknown state: {requested}")
            self.publish_status(f"Ignored unknown state: {requested}")
            return

        state = STATE_ALIASES.get(requested, requested)
        if state == "active":
            if not self._run_local_action(self._on_active):
                return
            self._set_status("active", self._connected, "OK")
            self.publish_status("OK")
            return
        if state == "quiet":
            if not self._run_local_action(self._on_quiet):
                return
            self._set_status("quiet", self._connected, "OK")
            self.publish_status("OK")
            return
        if state == "restart":
            if not self._system_commands_allowed(state):
                return
            self._transition_and_run("restarting", "Restarting app", self._restart_command())
            return
        if state == "reboot":
            if not self._system_commands_allowed(state):
                return
            self._transition_and_run("rebooting", "Rebooting OS", self._reboot_command())
            return
        if state == "shutdown":
            if not self._run_local_action(self._on_shutdown):
                return
            self._set_status("shutting_down", self._connected, "Shutdown command handled locally")
            self.publish_status("Shutdown command handled locally")

    def _transition_and_run(self, state: str, message: str, command: list[str]) -> None:
        self._set_status(state, self._connected, message)
        self.publish_status(message)
        timer = threading.Timer(0.75, self._run_system_command, args=(command,))
        timer.daemon = True
        timer.start()

    def _run_system_command(self, command: list[str]) -> None:
        try:
            self._on_cleanup()
        finally:
            subprocess.Popen(command)

    def _system_commands_allowed(self, state: str) -> bool:
        if self.allow_system_commands:
            return True
        message = f"Ignored {state}: set OMB_MQTT_ALLOW_SYSTEM_COMMANDS=1 to allow system commands"
        self._set_status(self._state, self._connected, message)
        self.publish_status(message)
        return False

    def _run_local_action(self, action: Callable[[], None]) -> bool:
        try:
            action()
        except Exception as exc:
            self._set_status(self._state, self._connected, f"Local state action failed: {exc}")
            self.publish_status(f"Local state action failed: {exc}")
            return False
        return True

    def _restart_command(self) -> list[str]:
        return os.environ.get(
            "OMB_RESTART_COMMAND",
            "sudo systemctl restart one-man-band.service",
        ).split()

    def _reboot_command(self) -> list[str]:
        return os.environ.get("OMB_REBOOT_COMMAND", "sudo systemctl reboot").split()

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.publish(topic, json.dumps(payload, sort_keys=True), qos=0, retain=False)
        except Exception as exc:
            self._set_status(self._state, self._connected, f"MQTT publish failed: {exc}")

    def _reason_code_value(self, reason_code) -> int:
        try:
            return int(reason_code)
        except (TypeError, ValueError):
            return int(getattr(reason_code, "value", 1))

    def _set_status(self, state: str, connected: bool, message: str) -> None:
        with self._lock:
            self._state = state
            self._connected = connected
            self._message = message
        if self._on_state_changed:
            self._on_state_changed()
