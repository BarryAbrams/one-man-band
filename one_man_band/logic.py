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


@dataclass(slots=True)
class ActionEvent:
    id: int
    type: str
    label: str
    detail: str
    created_at: float

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
        cause = self._cause_field(payload)
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

    def _cause_field(self, payload: dict[str, Any]) -> dict[str, Any]:
        cause = self._dict_field(payload, "cause")
        if "conditions" not in cause:
            if not cause.get("type"):
                raise ValueError("Rule must have at least one condition")
            cause = {"conditions": [cause]}
        conditions = cause.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            raise ValueError("Rule must have at least one condition")
        if not all(isinstance(condition, dict) for condition in conditions):
            raise ValueError("Rule conditions must be objects")
        return {"conditions": conditions}

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
        self._audio_playlist_positions: dict[tuple[int, str, int], int] = {}
        self._running_animations: set[str] = set()
        self._action_event_id = 0
        self._action_events: list[ActionEvent] = []
        self._boot_ran = False

    def metadata(self) -> dict[str, Any]:
        return {
            "logic_cause_types": [
                "boot",
                "gpio",
                "timer",
            ],
            "logic_action_types": [
                "audio_play",
                "audio_stop",
                "animation_play",
                "animation_stop",
                "timer_set",
                "timer_end",
                "solenoid_set",
                "solenoid_pulse",
                "servo_set",
                "pixels_animate",
            ],
        }

    def rules_payload(self) -> dict[str, Any]:
        return {"rules": [rule.to_payload() for rule in self._store.list_rules()]}

    def timers_payload(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        self._update_timers(now)
        with self._lock:
            timers = []
            for timer in sorted(self._timers.values(), key=lambda item: item.name.lower()):
                elapsed = max(0.0, now - timer.started_at)
                remaining = max(0.0, timer.duration_seconds - elapsed)
                duration_ms = int(round(timer.duration_seconds * 1000))
                remaining_ms = int(round(remaining * 1000))
                progress = 1.0 if timer.duration_seconds <= 0 else min(1.0, elapsed / timer.duration_seconds)
                timers.append(
                    {
                        "name": timer.name,
                        "duration_ms": duration_ms,
                        "remaining_ms": remaining_ms,
                        "progress": progress,
                        "ended": timer.ended,
                    }
                )
            return timers

    def action_events_payload(self) -> list[dict[str, Any]]:
        cutoff = time.monotonic() - 10
        with self._lock:
            self._action_events = [
                event for event in self._action_events if event.created_at >= cutoff
            ]
            return [event.to_payload() for event in self._action_events]

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
            conditions = self._conditions(rule)
            if (
                rule.enabled
                and conditions
                and all(condition.get("type") == "boot" for condition in conditions)
            ):
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
        conditions = self._conditions(rule)
        if not conditions:
            return None
        if any(condition.get("type") == "boot" for condition in conditions):
            return None
        if self._conditions_are_satisfied(rule, conditions, gpio, now):
            with self._lock:
                if self._last_branch.get(rule.id) == "then":
                    return None
                if now - self._last_fire.get(rule.id, 0) < self._cooldown_seconds(conditions):
                    return None
                self._last_branch[rule.id] = "then"
            return "then"
        if self._conditions_support_else(conditions):
            with self._lock:
                if self._last_branch.get(rule.id) == "else":
                    return None
                if not rule.else_actions:
                    self._last_branch[rule.id] = "else"
                    return None
                self._last_branch[rule.id] = "else"
            return "else"
        return None

    def _conditions(self, rule: LogicRule) -> list[dict[str, Any]]:
        conditions = rule.cause.get("conditions")
        if isinstance(conditions, list):
            return [condition for condition in conditions if isinstance(condition, dict)]
        if rule.cause.get("type"):
            return [rule.cause]
        return []

    def _conditions_are_satisfied(
        self,
        rule: LogicRule,
        conditions: list[dict[str, Any]],
        gpio: dict[str, bool],
        now: float,
    ) -> bool:
        for condition in conditions:
            condition_type = condition.get("type")
            if condition_type == "gpio":
                input_name = str(condition.get("input") or "")
                expected = self._bool(condition.get("state", True))
                if gpio.get(input_name, False) != expected:
                    with self._lock:
                        self._candidate_since.pop(rule.id, None)
                    return False
                continue
            if condition_type in {"timer", "timer_started", "timer_ended", "timer_position"}:
                if not self._timer_event_should_fire(rule, condition, now):
                    return False
                continue
            if condition_type == "boot":
                continue
            return False

        debounce_seconds = self._debounce_seconds(conditions)
        with self._lock:
            stable_since = self._candidate_since.setdefault(rule.id, now)
            if now - stable_since < debounce_seconds:
                return False
        return True

    def _conditions_support_else(self, conditions: list[dict[str, Any]]) -> bool:
        return conditions and all(condition.get("type") == "gpio" for condition in conditions)

    def _debounce_seconds(self, conditions: list[dict[str, Any]]) -> float:
        return max(
            [
                max(0, int(condition.get("debounce_ms", 50))) / 1000
                for condition in conditions
                if condition.get("type") == "gpio"
            ]
            or [0]
        )

    def _cooldown_seconds(self, conditions: list[dict[str, Any]]) -> float:
        return max(
            [
                max(0, int(condition.get("cooldown_ms", 1000))) / 1000
                for condition in conditions
                if condition.get("type") == "gpio"
            ]
            or [0]
        )

    def _timer_event_should_fire(self, rule: LogicRule, condition: dict[str, Any], now: float) -> bool:
        timer_name = self._timer_name(condition.get("timer_name") or "")
        if not timer_name:
            return False

        with self._lock:
            timer = self._timers.get(timer_name)
            if timer is None:
                return False

            timer_event = self._timer_event(condition)
            if timer_event == "started":
                key = (rule.id, timer.name, timer.generation)
                if key in self._timer_start_seen:
                    return False
                self._timer_start_seen.add(key)
                return True

            if timer_event == "ended":
                key = (rule.id, timer.name, timer.generation)
                if not timer.ended or key in self._timer_end_seen:
                    return False
                self._timer_end_seen.add(key)
                return True

            if timer_event == "position":
                percent = self._optional_percent(condition.get("percent"))
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

    def _end_timer(self, name: str) -> str:
        timer_name = self._timer_name(name)
        if not timer_name:
            raise ValueError("Timer name must use only A-Z, a-z, and 0-9")
        now = time.monotonic()
        with self._lock:
            timer = self._timers.get(timer_name)
            if timer is None:
                self._timer_generation += 1
                self._timers[timer_name] = CountdownTimer(
                    name=timer_name,
                    duration_seconds=0,
                    started_at=now,
                    generation=self._timer_generation,
                    ended=True,
                )
            else:
                timer.ended = True
        return timer_name

    def _fire(self, rule: LogicRule, branch: str = "then") -> None:
        actions = rule.actions if branch == "then" else rule.else_actions
        for index, action in enumerate(actions):
            self._run_action(action, rule.id, branch, index)
        if self._on_action:
            self._on_action()

    def _run_action(self, action: dict[str, Any], rule_id: int, branch: str, index: int) -> None:
        action_type = action.get("type")
        if action_type == "audio_play":
            filename = self._audio_filename(action, rule_id, branch, index)
            if filename:
                self._audio_manager.play(filename, str(action.get("speaker") or "both"))
                self._record_action_event("audio", "Audio", filename)
        elif action_type == "audio_stop":
            self._audio_manager.stop()
            self._record_action_event("audio", "Audio stopped", "All audio")
        elif action_type == "animation_play":
            name = self._animation_name(action.get("animation_name") or "")
            if name:
                interrupt = self._bool(action.get("interrupt", False))
                with self._lock:
                    if interrupt:
                        self._running_animations.clear()
                    self._running_animations.add(name)
                detail = f"{name} (interrupting)" if interrupt else name
                self._record_action_event("animation", "Animation started", detail)
        elif action_type == "animation_stop":
            name = self._animation_name(action.get("animation_name") or "")
            with self._lock:
                if name:
                    self._running_animations.discard(name)
                    detail = name
                else:
                    self._running_animations.clear()
                    detail = "All animations"
            self._record_action_event("animation", "Animation stopped", detail)
        elif action_type == "timer_set":
            self._set_timer(
                str(action.get("timer_name") or ""),
                int(action.get("duration_ms", 1000)),
            )
            self._record_action_event("timer", "Timer", str(action.get("timer_name") or ""))
        elif action_type == "timer_end":
            timer_name = self._end_timer(str(action.get("timer_name") or ""))
            self._record_action_event("timer", "Timer ended", timer_name)
        elif action_type == "solenoid_set":
            self._controller.set_solenoid(
                str(action.get("name") or ""), self._bool(action.get("enabled", True))
            )
            state = "HIGH" if self._bool(action.get("enabled", True)) else "LOW"
            self._record_action_event("solenoid", "Solenoid", f"{action.get('name')} {state}")
        elif action_type == "solenoid_pulse":
            name = str(action.get("name") or "")
            duration = max(0, int(action.get("duration_ms", 1000))) / 1000
            self._controller.set_solenoid(name, True)
            timer = threading.Timer(duration, self._finish_solenoid_pulse, args=(name,))
            timer.daemon = True
            timer.start()
            self._record_action_event("solenoid", "Solenoid pulse", f"{name} {int(duration * 1000)} ms")
        elif action_type == "servo_set":
            channel = int(action.get("channel", 0))
            if self._bool(action.get("enable", True)):
                self._controller.set_servo_enabled(channel, True)
            self._controller.set_servo_value(channel, int(action.get("value", 127)))
            self._record_action_event("servo", "Servo", f"{channel} -> {int(action.get('value', 127))}")
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
            self._record_action_event("pixels", "Neopixels", f"Rails {', '.join(str(rail) for rail in rails)}")

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

    def _animation_name(self, value: Any) -> str:
        return str(value or "").strip()

    def _optional_percent(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        return max(0, min(100, int(value)))

    def _timer_event(self, cause: dict[str, Any]) -> str:
        event = str(cause.get("timer_event") or "").strip().lower()
        if event in {"started", "ended", "position"}:
            return event
        legacy_type = str(cause.get("type") or "")
        if legacy_type == "timer_started":
            return "started"
        if legacy_type == "timer_ended":
            return "ended"
        if legacy_type == "timer_position":
            return "position"
        return "ended"

    def _audio_filename(
        self, action: dict[str, Any], rule_id: int, branch: str, index: int
    ) -> str:
        mode = str(action.get("mode") or "single")
        if mode != "playlist":
            return str(action.get("filename") or "").strip()

        playlist = [
            str(filename).strip()
            for filename in action.get("playlist", [])
            if str(filename).strip()
        ]
        if not playlist:
            return ""

        key = (rule_id, branch, index)
        position = self._audio_playlist_positions.get(key, 0) % len(playlist)
        self._audio_playlist_positions[key] = (position + 1) % len(playlist)
        return playlist[position]

    def _record_action_event(self, event_type: str, label: str, detail: str) -> None:
        with self._lock:
            self._action_event_id += 1
            self._action_events.append(
                ActionEvent(
                    id=self._action_event_id,
                    type=event_type,
                    label=label,
                    detail=detail,
                    created_at=time.monotonic(),
                )
            )
            self._action_events = self._action_events[-30:]
