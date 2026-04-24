from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .device import DeviceController

if TYPE_CHECKING:
    from .audio import AudioManager


ActionCallback = Callable[[], None]


@dataclass(slots=True)
class LogicRule:
    id: int
    name: str
    enabled: bool
    cause: dict[str, Any]
    actions: list[dict[str, Any]]
    created_at: float
    updated_at: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class LogicStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS logic_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    cause_json TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def list_rules(self) -> list[LogicRule]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM logic_rules ORDER BY id ASC"
            ).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def upsert_rule(self, payload: dict[str, Any]) -> LogicRule:
        now = time.time()
        name = str(payload.get("name") or "Untitled rule").strip() or "Untitled rule"
        enabled = bool(payload.get("enabled", True))
        cause = self._dict_field(payload, "cause")
        actions = self._actions_field(payload)
        rule_id = payload.get("id")

        with self._lock, self._connect() as connection:
            if rule_id:
                connection.execute(
                    """
                    UPDATE logic_rules
                    SET name = ?, enabled = ?, cause_json = ?, actions_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        int(enabled),
                        json.dumps(cause, sort_keys=True),
                        json.dumps(actions, sort_keys=True),
                        now,
                        int(rule_id),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM logic_rules WHERE id = ?", (int(rule_id),)
                ).fetchone()
                if row is None:
                    raise ValueError(f"Logic rule not found: {rule_id}")
                return self._row_to_rule(row)

            cursor = connection.execute(
                """
                INSERT INTO logic_rules
                    (name, enabled, cause_json, actions_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    int(enabled),
                    json.dumps(cause, sort_keys=True),
                    json.dumps(actions, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM logic_rules WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._row_to_rule(row)

    def delete_rule(self, rule_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM logic_rules WHERE id = ?", (rule_id,))

    def _row_to_rule(self, row: sqlite3.Row) -> LogicRule:
        return LogicRule(
            id=int(row["id"]),
            name=str(row["name"]),
            enabled=bool(row["enabled"]),
            cause=json.loads(str(row["cause_json"])),
            actions=json.loads(str(row["actions_json"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _dict_field(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"Rule {key} must be an object")
        return value

    def _actions_field(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        value = payload.get("actions")
        if not isinstance(value, list) or not value:
            raise ValueError("Rule must have at least one action")
        if not all(isinstance(action, dict) for action in value):
            raise ValueError("Rule actions must be objects")
        return value


class LogicEngine:
    def __init__(
        self,
        store: LogicStore,
        controller: DeviceController,
        audio_manager: "AudioManager",
        on_action: ActionCallback | None = None,
    ) -> None:
        self._store = store
        self._controller = controller
        self._audio_manager = audio_manager
        self._on_action = on_action
        self._lock = threading.RLock()
        self._previous_gpio: dict[str, bool] = {}
        self._stable_since: dict[int, float] = {}
        self._armed_state: dict[int, bool] = {}
        self._last_fire: dict[int, float] = {}
        self._timer_started: dict[int, float] = {}
        self._boot_ran = False

    def metadata(self) -> dict[str, Any]:
        return {
            "logic_cause_types": ["boot", "gpio", "current_above", "timer"],
            "logic_action_types": [
                "audio_play",
                "solenoid_set",
                "solenoid_pulse",
                "servo_set",
                "pixels_animate",
            ],
        }

    def rules_payload(self) -> dict[str, Any]:
        return {"rules": [rule.to_payload() for rule in self._store.list_rules()]}

    def save_rule(self, payload: dict[str, Any]) -> LogicRule:
        return self._store.upsert_rule(payload)

    def delete_rule(self, rule_id: int) -> None:
        self._store.delete_rule(rule_id)
        with self._lock:
            self._stable_since.pop(rule_id, None)
            self._armed_state.pop(rule_id, None)
            self._last_fire.pop(rule_id, None)
            self._timer_started.pop(rule_id, None)

    def run_boot_rules(self) -> list[str]:
        with self._lock:
            if self._boot_ran:
                return []
            self._boot_ran = True
        fired: list[str] = []
        for rule in self._store.list_rules():
            if rule.enabled and rule.cause.get("type") == "boot":
                self._fire(rule)
                fired.append(rule.name)
        return fired

    def process_state(self, state: dict[str, Any]) -> list[str]:
        now = time.monotonic()
        gpio = {
            str(name): bool(value)
            for name, value in dict(state.get("gpio_inputs_map") or {}).items()
        }
        fired: list[str] = []

        for rule in self._store.list_rules():
            if not rule.enabled:
                continue
            if self._should_fire(rule, state, gpio, now):
                self._fire(rule)
                self._last_fire[rule.id] = now
                fired.append(rule.name)

        with self._lock:
            self._previous_gpio = gpio
        return fired

    def _should_fire(
        self,
        rule: LogicRule,
        state: dict[str, Any],
        gpio: dict[str, bool],
        now: float,
    ) -> bool:
        cause_type = rule.cause.get("type")
        if cause_type == "gpio":
            return self._gpio_should_fire(rule, gpio, now)
        if cause_type == "current_above":
            return self._current_should_fire(rule, state, now)
        if cause_type == "timer":
            return self._timer_should_fire(rule, now)
        return False

    def _gpio_should_fire(self, rule: LogicRule, gpio: dict[str, bool], now: float) -> bool:
        input_name = str(rule.cause.get("input") or "")
        expected = self._bool(rule.cause.get("state", True))
        debounce_seconds = max(0, int(rule.cause.get("debounce_ms", 50))) / 1000
        cooldown_seconds = max(0, int(rule.cause.get("cooldown_ms", 1000))) / 1000
        current = gpio.get(input_name, False)
        previous = self._previous_gpio.get(input_name, current)

        with self._lock:
            if current != expected:
                self._stable_since.pop(rule.id, None)
                self._armed_state[rule.id] = False
                return False

            if previous != current and not self._armed_state.get(rule.id, False):
                self._stable_since[rule.id] = now

            stable_since = self._stable_since.setdefault(rule.id, now)
            last_fire = self._last_fire.get(rule.id, 0)
            if self._armed_state.get(rule.id, False):
                return False
            if now - stable_since < debounce_seconds:
                return False
            if now - last_fire < cooldown_seconds:
                return False

            self._armed_state[rule.id] = True
            return True

    def _current_should_fire(self, rule: LogicRule, state: dict[str, Any], now: float) -> bool:
        channel = str(rule.cause.get("channel") or "")
        threshold = int(rule.cause.get("threshold_ma", 1000))
        cooldown_seconds = max(0, int(rule.cause.get("cooldown_ms", 5000))) / 1000
        currents = dict(state.get("ina_current_map") or {})
        current = int(currents.get(channel, 0) or 0)
        last_fire = self._last_fire.get(rule.id, 0)
        return current >= threshold and now - last_fire >= cooldown_seconds

    def _timer_should_fire(self, rule: LogicRule, now: float) -> bool:
        seconds = max(0, float(rule.cause.get("seconds", 1)))
        repeats = self._bool(rule.cause.get("repeats", False))
        with self._lock:
            started = self._timer_started.setdefault(rule.id, now)
            last_fire = self._last_fire.get(rule.id)
            if last_fire is not None and not repeats:
                return False
            if now - started < seconds:
                return False
            self._timer_started[rule.id] = now
            return True

    def _fire(self, rule: LogicRule) -> None:
        for action in rule.actions:
            self._run_action(action)
        if self._on_action:
            self._on_action()

    def _run_action(self, action: dict[str, Any]) -> None:
        action_type = action.get("type")
        if action_type == "audio_play":
            filename = str(action.get("filename") or "").strip()
            if filename:
                self._audio_manager.play(filename, str(action.get("speaker") or "both"))
        elif action_type == "solenoid_set":
            self._controller.set_solenoid(
                str(action.get("name") or ""), self._bool(action.get("enabled", True))
            )
        elif action_type == "solenoid_pulse":
            name = str(action.get("name") or "")
            duration = max(0, int(action.get("duration_ms", 1000))) / 1000
            self._controller.set_solenoid(name, True)
            timer = threading.Timer(duration, self._finish_solenoid_pulse, args=(name,))
            timer.daemon = True
            timer.start()
        elif action_type == "servo_set":
            channel = int(action.get("channel", 0))
            if self._bool(action.get("enable", True)):
                self._controller.set_servo_enabled(channel, True)
            self._controller.set_servo_value(channel, int(action.get("value", 127)))
        elif action_type == "pixels_animate":
            rails = action.get("rails") or []
            rail_mask = 0
            for rail in rails:
                rail_mask |= 1 << (int(rail) - 1)
            self._controller.animate_pixels(
                rail_mask=rail_mask,
                start=int(action.get("start", 0)),
                count=int(action.get("count", 0)),
                start_rgb=self._rgb(action.get("start_rgb"), [0, 0, 0]),
                end_rgb=self._rgb(action.get("end_rgb"), [0, 0, 255]),
                duration_ms=int(action.get("duration_ms", 1000)),
                animation_id=int(action.get("animation_id", 0)),
            )

    def _finish_solenoid_pulse(self, name: str) -> None:
        self._controller.set_solenoid(name, False)
        if self._on_action:
            self._on_action()

    def _rgb(self, value: Any, default: list[int]) -> tuple[int, int, int]:
        if not isinstance(value, list) or len(value) != 3:
            value = default
        return tuple(max(0, min(255, int(channel))) for channel in value)

    def _bool(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "low", "off", "no"}
        return bool(value)
