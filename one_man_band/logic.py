from __future__ import annotations

import json
import re
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
TIMER_NAME_RE = re.compile(r"^[A-Za-z0-9]+$")


@dataclass(slots=True)
class LogicRule:
    id: int
    name: str
    enabled: bool
    cause: dict[str, Any]
    actions: list[dict[str, Any]]
    else_actions: list[dict[str, Any]]
    created_at: float
    updated_at: float

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CountdownTimer:
    name: str
    duration_seconds: float
    started_at: float
    generation: int
    ended: bool = False


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
                    else_actions_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(logic_rules)").fetchall()
            }
            if "else_actions_json" not in columns:
                connection.execute(
                    "ALTER TABLE logic_rules ADD COLUMN else_actions_json TEXT NOT NULL DEFAULT '[]'"
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
        else_actions = self._optional_actions_field(payload, "else_actions")
        rule_id = payload.get("id")

        with self._lock, self._connect() as connection:
            if rule_id:
                connection.execute(
                    """
                    UPDATE logic_rules
                    SET name = ?, enabled = ?, cause_json = ?, actions_json = ?, else_actions_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        int(enabled),
                        json.dumps(cause, sort_keys=True),
                        json.dumps(actions, sort_keys=True),
                        json.dumps(else_actions, sort_keys=True),
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
                    (name, enabled, cause_json, actions_json, else_actions_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    int(enabled),
                    json.dumps(cause, sort_keys=True),
                    json.dumps(actions, sort_keys=True),
                    json.dumps(else_actions, sort_keys=True),
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
            else_actions=json.loads(str(row["else_actions_json"])),
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

    def _optional_actions_field(self, payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = payload.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"Rule {key} must be a list")
        if not all(isinstance(action, dict) for action in value):
            raise ValueError(f"Rule {key} entries must be objects")
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
        self._candidate_branch: dict[int, str] = {}
        self._candidate_since: dict[int, float] = {}
        self._last_branch: dict[int, str] = {}
        self._last_fire: dict[int, float] = {}
        self._timers: dict[str, CountdownTimer] = {}
        self._timer_generation = 0
        self._timer_start_seen: set[tuple[int, str, int]] = set()
        self._timer_end_seen: set[tuple[int, str, int]] = set()
        self._timer_position_seen: set[tuple[int, str, int, int | None]] = set()
        self._boot_ran = False

    def metadata(self) -> dict[str, Any]:
        return {
            "logic_cause_types": [
                "boot",
                "gpio",
                "current_above",
                "timer_started",
                "timer_ended",
                "timer_position",
            ],
            "logic_action_types": [
                "audio_play",
                "timer_set",
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
            self._candidate_branch.pop(rule_id, None)
            self._candidate_since.pop(rule_id, None)
            self._last_branch.pop(rule_id, None)
            self._last_fire.pop(rule_id, None)
            self._timer_start_seen = {
                item for item in self._timer_start_seen if item[0] != rule_id
            }
            self._timer_end_seen = {
                item for item in self._timer_end_seen if item[0] != rule_id
            }
            self._timer_position_seen = {
                item for item in self._timer_position_seen if item[0] != rule_id
            }

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
        self._update_timers(now)
        gpio = {
            str(name): bool(value)
            for name, value in dict(state.get("gpio_inputs_map") or {}).items()
        }
        fired: list[str] = []

        for rule in self._store.list_rules():
            if not rule.enabled:
                continue
            branch = self._branch_to_fire(rule, state, gpio, now)
            if branch:
                self._fire(rule, branch)
                if branch == "then":
                    self._last_fire[rule.id] = now
                fired.append(rule.name)

        with self._lock:
            self._previous_gpio = gpio
        return fired

    def _branch_to_fire(
        self,
        rule: LogicRule,
        state: dict[str, Any],
        gpio: dict[str, bool],
        now: float,
    ) -> str | None:
        cause_type = rule.cause.get("type")
        if cause_type == "gpio":
            return self._gpio_branch_to_fire(rule, gpio, now)
        if cause_type == "current_above":
            return self._current_branch_to_fire(rule, state, now)
        if cause_type in {"timer_started", "timer_ended", "timer_position"}:
            return "then" if self._timer_event_should_fire(rule, now) else None
        return None

    def _gpio_branch_to_fire(self, rule: LogicRule, gpio: dict[str, bool], now: float) -> str | None:
        input_name = str(rule.cause.get("input") or "")
        expected = self._bool(rule.cause.get("state", True))
        debounce_seconds = max(0, int(rule.cause.get("debounce_ms", 50))) / 1000
        cooldown_seconds = max(0, int(rule.cause.get("cooldown_ms", 1000))) / 1000
        current = gpio.get(input_name, False)
        branch = "then" if current == expected else "else"

        with self._lock:
            if self._candidate_branch.get(rule.id) != branch:
                self._candidate_branch[rule.id] = branch
                self._candidate_since[rule.id] = now
                return None

            stable_since = self._candidate_since.get(rule.id, now)
            if now - stable_since < debounce_seconds:
                return None
            if self._last_branch.get(rule.id) == branch:
                return None
            if branch == "then" and now - self._last_fire.get(rule.id, 0) < cooldown_seconds:
                return None
            if branch == "else" and not rule.else_actions:
                self._last_branch[rule.id] = branch
                return None

            self._last_branch[rule.id] = branch
            return branch

    def _current_branch_to_fire(self, rule: LogicRule, state: dict[str, Any], now: float) -> str | None:
        channel = str(rule.cause.get("channel") or "")
        threshold = int(rule.cause.get("threshold_ma", 1000))
        cooldown_seconds = max(0, int(rule.cause.get("cooldown_ms", 5000))) / 1000
        currents = dict(state.get("ina_current_map") or {})
        current = int(currents.get(channel, 0) or 0)
        branch = "then" if current >= threshold else "else"
        with self._lock:
            if self._last_branch.get(rule.id) == branch:
                return None
            if branch == "then" and now - self._last_fire.get(rule.id, 0) < cooldown_seconds:
                return None
            if branch == "else" and not rule.else_actions:
                self._last_branch[rule.id] = branch
                return None
            self._last_branch[rule.id] = branch
            return branch

    def _timer_event_should_fire(self, rule: LogicRule, now: float) -> bool:
        timer_name = self._timer_name(rule.cause.get("timer_name") or "")
        if not timer_name:
            return False

        with self._lock:
            timer = self._timers.get(timer_name)
            if timer is None:
                return False

            cause_type = rule.cause.get("type")
            if cause_type == "timer_started":
                key = (rule.id, timer.name, timer.generation)
                if key in self._timer_start_seen:
                    return False
                self._timer_start_seen.add(key)
                return True

            if cause_type == "timer_ended":
                key = (rule.id, timer.name, timer.generation)
                if not timer.ended or key in self._timer_end_seen:
                    return False
                self._timer_end_seen.add(key)
                return True

            if cause_type == "timer_position":
                percent = self._optional_percent(rule.cause.get("percent"))
                key = (rule.id, timer.name, timer.generation, percent)
                if key in self._timer_position_seen:
                    return False
                if timer.ended:
                    return False
                if percent is None:
                    self._timer_position_seen.add(key)
                    return True

                elapsed = now - timer.started_at
                progress = (elapsed / timer.duration_seconds) * 100 if timer.duration_seconds else 100
                if progress < percent:
                    return False
                self._timer_position_seen.add(key)
                return True

        return False

    def _update_timers(self, now: float) -> None:
        with self._lock:
            for timer in self._timers.values():
                if not timer.ended and now - timer.started_at >= timer.duration_seconds:
                    timer.ended = True

    def _set_timer(self, name: str, duration_ms: int) -> None:
        timer_name = self._timer_name(name)
        if not timer_name:
            raise ValueError("Timer name must use only A-Z, a-z, and 0-9")
        duration_seconds = max(0, duration_ms) / 1000
        now = time.monotonic()
        with self._lock:
            self._timer_generation += 1
            self._timers[timer_name] = CountdownTimer(
                name=timer_name,
                duration_seconds=duration_seconds,
                started_at=now,
                generation=self._timer_generation,
            )

    def _fire(self, rule: LogicRule, branch: str = "then") -> None:
        actions = rule.actions if branch == "then" else rule.else_actions
        for action in actions:
            self._run_action(action)
        if self._on_action:
            self._on_action()

    def _run_action(self, action: dict[str, Any]) -> None:
        action_type = action.get("type")
        if action_type == "audio_play":
            filename = str(action.get("filename") or "").strip()
            if filename:
                self._audio_manager.play(filename, str(action.get("speaker") or "both"))
        elif action_type == "timer_set":
            self._set_timer(
                str(action.get("timer_name") or ""),
                int(action.get("duration_ms", 1000)),
            )
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

    def _timer_name(self, value: Any) -> str:
        name = str(value or "").strip()
        return name if TIMER_NAME_RE.match(name) else ""

    def _optional_percent(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        return max(0, min(100, int(value)))
