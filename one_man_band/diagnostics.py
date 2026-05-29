from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import socket
import sqlite3
import threading
import time
from typing import Any

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:  # pragma: no cover - optional on development machines
    SMBus = None
    i2c_msg = None

try:
    import RPi.GPIO as GPIO
except ImportError:  # pragma: no cover - optional on development machines
    GPIO = None

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - optional when MQTT is disabled
    mqtt = None


I2C_BUS = int(os.environ.get("BAC_I2C_BUS", "1"), 0)
SHT40_ADDRESS = int(os.environ.get("BAC_SHT40_ADDRESS", "0x44"), 0)
LIS3DH_ADDRESSES = [
    int(value, 0)
    for value in os.environ.get("BAC_LIS3DH_ADDRESSES", "0x18,0x19").split(",")
    if value.strip()
]
SHT40_MEASURE_HIGH_PRECISION = 0xFD
LIS3DH_WHO_AM_I = 0x0F
LIS3DH_WHO_AM_I_VALUE = 0x33
LIS3DH_CTRL_REG1 = 0x20
LIS3DH_CTRL_REG4 = 0x23
LIS3DH_OUT_X_L = 0x28
FLOAT_SWITCH_GPIO = int(os.environ.get("BAC_FLOAT_SWITCH_GPIO", "5"), 0)
FLOAT_SWITCH_ACTIVE_LOW = os.environ.get("BAC_FLOAT_SWITCH_ACTIVE_LOW", "1") == "1"
SMOOTHING_ALPHA = min(1.0, max(0.0, float(os.environ.get("BAC_SMOOTHING_ALPHA", "0.35"))))
COMPRESSOR_VIBRATION_ON_G = float(os.environ.get("BAC_COMPRESSOR_VIBRATION_ON_G", "0.12"))
COMPRESSOR_VIBRATION_OFF_G = float(os.environ.get("BAC_COMPRESSOR_VIBRATION_OFF_G", "0.08"))
COMPRESSOR_CONFIRM_SAMPLES = max(1, int(os.environ.get("BAC_COMPRESSOR_CONFIRM_SAMPLES", "2")))


@dataclass
class CompressorReading:
    temperature_c: float | None = None
    humidity_pct: float | None = None
    vibration_g: float | None = None
    current_a: float | None = None
    float_switch: bool | None = None
    compressor_running: bool | None = None
    updated_at: float = 0
    source: str = "starting"
    last_frame_at: float = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
            "vibration_g": self.vibration_g,
            "current_a": self.current_a,
            "float_switch": self.float_switch,
            "compressor_running": self.compressor_running,
            "updated_at": self.updated_at,
            "last_frame_at": self.last_frame_at,
            "age_ms": max(0, int((time.time() - self.updated_at) * 1000)) if self.updated_at else None,
            "source": self.source,
        }


class DiagnosticStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL,
                    temperature_c REAL,
                    humidity_pct REAL,
                    vibration_g REAL,
                    current_a REAL,
                    float_switch INTEGER,
                    compressor_running INTEGER,
                    source TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_recorded_at ON readings(recorded_at)"
            )

    def add_reading(self, reading: CompressorReading) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO readings (
                    recorded_at, temperature_c, humidity_pct, vibration_g, current_a,
                    float_switch, compressor_running, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reading.updated_at or time.time(),
                    reading.temperature_c,
                    reading.humidity_pct,
                    reading.vibration_g,
                    reading.current_a,
                    None if reading.float_switch is None else int(reading.float_switch),
                    None if reading.compressor_running is None else int(reading.compressor_running),
                    reading.source,
                ),
            )

    def history(self, minutes: int = 60, limit: int = 720) -> list[dict[str, object]]:
        since = time.time() - max(1, minutes) * 60
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT recorded_at, temperature_c, humidity_pct, vibration_g, current_a,
                       float_switch, compressor_running, source
                FROM readings
                WHERE recorded_at >= ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (since, max(1, limit)),
            ).fetchall()
        return [self._row_payload(row) for row in reversed(rows)]

    def summary(self, minutes: int = 60) -> dict[str, object]:
        rows = self.history(minutes=minutes, limit=5000)
        compressor = self._compressor_stats(rows, minutes)
        return {
            "window_minutes": minutes,
            "sample_count": len(rows),
            "temperature_c": self._stats(rows, "temperature_c"),
            "humidity_pct": self._stats(rows, "humidity_pct"),
            "vibration_g": self._stats(rows, "vibration_g"),
            "current_a": self._stats(rows, "current_a"),
            "run_minutes": compressor["active_minutes"],
            "compressor": compressor,
        }

    def prune(self, retention_days: int) -> None:
        cutoff = time.time() - max(1, retention_days) * 86400
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM readings WHERE recorded_at < ?", (cutoff,))

    def _row_payload(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "recorded_at": row["recorded_at"],
            "temperature_c": row["temperature_c"],
            "humidity_pct": row["humidity_pct"],
            "vibration_g": row["vibration_g"],
            "current_a": row["current_a"],
            "float_switch": None if row["float_switch"] is None else bool(row["float_switch"]),
            "compressor_running": None
            if row["compressor_running"] is None
            else bool(row["compressor_running"]),
            "source": row["source"],
        }

    def _stats(self, rows: list[dict[str, object]], key: str) -> dict[str, float | None]:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        if not values:
            return {"min": None, "max": None, "avg": None}
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
        }

    def _compressor_stats(self, rows: list[dict[str, object]], minutes: int) -> dict[str, object]:
        samples = [
            (float(row["recorded_at"]), bool(row["compressor_running"]))
            for row in rows
            if row.get("compressor_running") is not None
        ]
        window_seconds = max(1, minutes) * 60
        if not samples:
            return {
                "running": None,
                "start_count": 0,
                "starts_per_hour": None,
                "active_minutes": 0.0,
                "duty_cycle_pct": None,
                "avg_run_seconds": None,
                "last_run_seconds": None,
                "current_run_seconds": None,
            }

        now = time.time()
        active_seconds = 0.0
        run_durations: list[float] = []
        start_count = 0
        previous_state = samples[0][1]
        current_run_start = samples[0][0] if previous_state else None

        for index, (timestamp, state) in enumerate(samples):
            next_timestamp = samples[index + 1][0] if index + 1 < len(samples) else now
            if state:
                active_seconds += max(0.0, next_timestamp - timestamp)

            if index == 0:
                continue

            if not previous_state and state:
                start_count += 1
                current_run_start = timestamp
            elif previous_state and not state and current_run_start is not None:
                run_durations.append(max(0.0, timestamp - current_run_start))
                current_run_start = None

            previous_state = state

        running_now = samples[-1][1]
        current_run_seconds = (
            round(max(0.0, now - current_run_start), 1)
            if running_now and current_run_start is not None
            else None
        )
        avg_run_seconds = round(sum(run_durations) / len(run_durations), 1) if run_durations else None
        last_run_seconds = round(run_durations[-1], 1) if run_durations else current_run_seconds
        starts_per_hour = round(start_count / (minutes / 60), 2) if minutes > 0 else None
        duty_cycle_pct = round((active_seconds / window_seconds) * 100, 1) if window_seconds > 0 else None

        return {
            "running": running_now,
            "start_count": start_count,
            "starts_per_hour": starts_per_hour,
            "active_minutes": round(active_seconds / 60, 2),
            "duty_cycle_pct": duty_cycle_pct,
            "avg_run_seconds": avg_run_seconds,
            "last_run_seconds": last_run_seconds,
            "current_run_seconds": current_run_seconds,
        }


class I2CSensorReader:
    def __init__(self) -> None:
        self.mock = os.environ.get("BAC_MOCK_HARDWARE", "0") == "1"
        self._bus = None
        self._lis_addr: int | None = None
        self._last_accel: tuple[int, int, int] | None = None
        self._vibration_peak_mg = 0
        self._last_open_error = ""
        self._gpio_ready = False
        self._gpio_error = ""
        self._smoothed_values: dict[str, float] = {}
        self._compressor_running = False
        self._compressor_high_streak = 0
        self._compressor_low_streak = 0

    @property
    def backend_name(self) -> str:
        if self.mock:
            return "mock"
        return f"i2c:bus-{I2C_BUS}"

    def open(self) -> None:
        if self.mock:
            return
        if SMBus is None:
            raise RuntimeError("smbus2 is not installed")
        self._bus = SMBus(I2C_BUS)
        self._lis_addr = self._probe_lis3dh()
        if self._lis_addr is None:
            raise RuntimeError("LIS3DH not found on I2C bus")
        self._init_lis3dh(self._lis_addr)
        self._init_float_gpio()
        self._last_open_error = ""

    def read_next(self, current: CompressorReading, timeout: float = 1.0) -> CompressorReading:
        if self.mock:
            return self._mock_reading(current)
        if self._bus is None:
            try:
                self.open()
            except Exception as exc:
                return CompressorReading(**{**current.__dict__, "updated_at": 0, "source": f"i2c-error: {exc}"})

        now = time.time()
        values = current.__dict__.copy()
        values["updated_at"] = now
        values["last_frame_at"] = now
        values["source"] = "i2c"

        temp_c, humidity_pct, sht_error = self._read_sht40()
        if temp_c is not None:
            values["temperature_c"] = self._smooth("temperature_c", temp_c, 2)
        else:
            values["temperature_c"] = None
        if humidity_pct is not None:
            values["humidity_pct"] = self._smooth("humidity_pct", humidity_pct, 2)
        else:
            values["humidity_pct"] = None

        accel = self._read_lis3dh()
        if accel is not None:
            vibration_mg = self._update_vibration_peak(accel)
            raw_vibration_g = vibration_mg / 1000
            values["vibration_g"] = self._smooth("vibration_g", raw_vibration_g, 3)
            values["compressor_running"] = self._update_compressor_state(raw_vibration_g)
        else:
            values["vibration_g"] = None
            values["compressor_running"] = None

        values["current_a"] = None
        values["float_switch"] = self._read_float_switch()

        errors = [error for error in (sht_error, self._last_open_error, self._gpio_error) if error]
        if temp_c is None and humidity_pct is None and accel is None and values["float_switch"] is None:
            values["source"] = "i2c-error: sensor read failed"
        elif errors:
            values["source"] = f"i2c-partial: {', '.join(dict.fromkeys(errors))}"

        return CompressorReading(**values)

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None
        if GPIO is not None and self._gpio_ready:
            try:
                GPIO.cleanup(FLOAT_SWITCH_GPIO)
            except Exception:
                pass
            self._gpio_ready = False

    def _probe_lis3dh(self) -> int | None:
        assert self._bus is not None
        for address in LIS3DH_ADDRESSES:
            try:
                who_am_i = self._bus.read_byte_data(address, LIS3DH_WHO_AM_I)
            except OSError:
                continue
            if who_am_i == LIS3DH_WHO_AM_I_VALUE:
                return address
        return None

    def _init_lis3dh(self, address: int) -> None:
        assert self._bus is not None
        self._bus.write_byte_data(address, LIS3DH_CTRL_REG1, 0x57)
        self._bus.write_byte_data(address, LIS3DH_CTRL_REG4, 0x80)

    def _init_float_gpio(self) -> None:
        if GPIO is None:
            self._gpio_error = "RPi.GPIO is not installed"
            self._gpio_ready = False
            return
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            pull = GPIO.PUD_UP if FLOAT_SWITCH_ACTIVE_LOW else GPIO.PUD_DOWN
            GPIO.setup(FLOAT_SWITCH_GPIO, GPIO.IN, pull_up_down=pull)
            self._gpio_ready = True
            self._gpio_error = ""
        except Exception as exc:
            self._gpio_ready = False
            self._gpio_error = str(exc)

    def _read_sht40(self) -> tuple[float | None, float | None, str]:
        assert self._bus is not None
        try:
            if i2c_msg is None:
                raise RuntimeError("smbus2 is not installed")
            self._bus.write_byte(SHT40_ADDRESS, SHT40_MEASURE_HIGH_PRECISION)
            time.sleep(0.01)
            read = i2c_msg.read(SHT40_ADDRESS, 6)
            self._bus.i2c_rdwr(read)
            data = list(read)
            if len(data) != 6:
                return (None, None, f"SHT40 short read: {len(data)} bytes")
            raw_temp = (data[0] << 8) | data[1]
            raw_humidity = (data[3] << 8) | data[4]
            temp_c = round(-45.0 + (175.0 * raw_temp / 65535.0), 2)
            humidity_pct = round(-6.0 + (125.0 * raw_humidity / 65535.0), 2)
            humidity_pct = min(100.0, max(0.0, humidity_pct))
            return (temp_c, humidity_pct, "")
        except OSError as exc:
            return (None, None, str(exc))
        except Exception as exc:
            return (None, None, str(exc))

    def _read_lis3dh(self) -> tuple[int, int, int] | None:
        if self._bus is None or self._lis_addr is None:
            return None
        try:
            data = self._bus.read_i2c_block_data(self._lis_addr, LIS3DH_OUT_X_L | 0x80, 6)
            if len(data) != 6:
                return None
            raw_x = self._signed_12bit(data[1], data[0])
            raw_y = self._signed_12bit(data[3], data[2])
            raw_z = self._signed_12bit(data[5], data[4])
            return (raw_x, raw_y, raw_z)
        except OSError:
            return None

    def _read_float_switch(self) -> bool | None:
        if self.mock:
            return None
        if GPIO is None or not self._gpio_ready:
            return None
        try:
            raw = bool(GPIO.input(FLOAT_SWITCH_GPIO))
            return raw if FLOAT_SWITCH_ACTIVE_LOW else (not raw)
        except Exception as exc:
            self._gpio_ready = False
            self._gpio_error = str(exc)
            return None

    def _smooth(self, key: str, value: float, digits: int) -> float:
        previous = self._smoothed_values.get(key)
        smoothed = value if previous is None else previous + SMOOTHING_ALPHA * (value - previous)
        self._smoothed_values[key] = smoothed
        return round(smoothed, digits)

    def _update_compressor_state(self, vibration_g: float) -> bool:
        if self._compressor_running:
            if vibration_g <= COMPRESSOR_VIBRATION_OFF_G:
                self._compressor_low_streak += 1
                if self._compressor_low_streak >= COMPRESSOR_CONFIRM_SAMPLES:
                    self._compressor_running = False
                    self._compressor_low_streak = 0
                    self._compressor_high_streak = 0
            else:
                self._compressor_low_streak = 0
            return self._compressor_running

        if vibration_g >= COMPRESSOR_VIBRATION_ON_G:
            self._compressor_high_streak += 1
            if self._compressor_high_streak >= COMPRESSOR_CONFIRM_SAMPLES:
                self._compressor_running = True
                self._compressor_high_streak = 0
                self._compressor_low_streak = 0
        else:
            self._compressor_high_streak = 0
        return self._compressor_running

    def _update_vibration_peak(self, accel: tuple[int, int, int]) -> int:
        x_mg, y_mg, z_mg = accel
        magnitude_mg = int(round((x_mg * x_mg + y_mg * y_mg + z_mg * z_mg) ** 0.5))
        vibration_mg = abs(magnitude_mg - 1000)
        if self._last_accel is None:
            self._last_accel = accel
            self._vibration_peak_mg = vibration_mg
            return vibration_mg
        self._last_accel = accel
        self._vibration_peak_mg = vibration_mg
        return vibration_mg

    def _signed_12bit(self, high: int, low: int) -> int:
        value = ((high & 0xFF) << 8) | (low & 0xFF)
        value >>= 4
        if value & 0x800:
            value -= 0x1000
        return value

    def _mock_reading(self, current: CompressorReading) -> CompressorReading:
        return CompressorReading(
            temperature_c=29.3,
            humidity_pct=47.8,
            vibration_g=0.012,
            current_a=None,
            float_switch=None,
            compressor_running=False,
            updated_at=time.time(),
            source="mock",
        )


CanSensorReader = I2CSensorReader


class MqttWarningPublisher:
    def __init__(self) -> None:
        self.enabled = os.environ.get("BAC_MQTT_ENABLED", "1") == "1"
        self.host = os.environ.get("BAC_MQTT_BROKER_HOST", os.environ.get("OMB_MQTT_BROKER_HOST", "192.168.0.153"))
        self.port = int(os.environ.get("BAC_MQTT_BROKER_PORT", os.environ.get("OMB_MQTT_BROKER_PORT", "1883")))
        self.topic = os.environ.get("BAC_MQTT_WARNING_TOPIC", "parcadia/backstage-air-compressor/warnings")
        self.status_topic = os.environ.get("BAC_MQTT_STATUS_TOPIC", "parcadia/backstage-air-compressor/status")
        self.hostname = os.environ.get("BAC_MQTT_HOSTNAME", socket.gethostname())
        self._client = None
        self._connected = False
        self._error = ""

    def start(self) -> None:
        if not self.enabled or mqtt is None:
            if self.enabled and mqtt is None:
                self._error = "paho-mqtt is not installed"
            return
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.hostname}-diagnostics")
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        try:
            self._client.connect_async(self.host, self.port, keepalive=30)
            self._client.loop_start()
        except Exception as exc:
            self._error = str(exc)

    def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()

    def publish_status(self, payload: dict[str, object]) -> None:
        self._publish(self.status_topic, payload, retain=True)

    def publish_warning(self, warning: dict[str, object]) -> None:
        self._publish(self.topic, warning, retain=False)

    def payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "warning_topic": self.topic,
            "status_topic": self.status_topic,
            "error": self._error,
        }

    def _publish(self, topic: str, payload: dict[str, object], retain: bool) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            import json

            self._client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=0, retain=retain)
        except Exception as exc:
            self._error = str(exc)

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connected = self._reason_code_ok(reason_code)
        self._error = "" if self._connected else f"MQTT connect failed: {reason_code}"
        if self._connected:
            client.subscribe("parcadia/global/whoisthere")

    def _on_message(self, _client, _userdata, message) -> None:
        if str(message.topic) == "parcadia/global/whoisthere":
            self.publish_status({"hostname": self.hostname})

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties=None) -> None:
        self._connected = False
        if not self._reason_code_ok(reason_code):
            self._error = f"MQTT disconnected: {reason_code}"

    def _reason_code_ok(self, reason_code) -> bool:
        is_failure = getattr(reason_code, "is_failure", None)
        if callable(is_failure):
            return not is_failure()
        value = getattr(reason_code, "value", reason_code)
        try:
            return int(value) == 0
        except (TypeError, ValueError):
            return str(reason_code).lower() in {"success", "normal disconnection"}


class DiagnosticService:
    SAMPLE_SECONDS = float(os.environ.get("BAC_SAMPLE_SECONDS", "2"))

    def __init__(self, store: DiagnosticStore, reader: CanSensorReader, mqtt_client: MqttWarningPublisher) -> None:
        self._store = store
        self._reader = reader
        self._mqtt = mqtt_client
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._reading = CompressorReading()
        self._backend = reader.backend_name
        self._connected = False
        self._error = ""
        self._warnings: list[dict[str, object]] = []
        self._last_warning_at: dict[str, float] = {}
        self._acked_warning_codes: set[str] = set()
        self._thresholds = {
            "temperature_f_max": float(os.environ.get("BAC_TEMP_F_MAX", "150")),
            "humidity_pct_max": float(os.environ.get("BAC_HUMIDITY_PCT_MAX", "75")),
            "vibration_g_max": float(os.environ.get("BAC_VIBRATION_G_MAX", "0.08")),
            "current_a_max": float(os.environ.get("BAC_CURRENT_A_MAX", "18")),
            "stale_seconds": float(os.environ.get("BAC_STALE_SECONDS", "15")),
            "warning_cooldown_seconds": float(os.environ.get("BAC_WARNING_COOLDOWN_SECONDS", "300")),
        }

    def start(self, on_update=None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._mqtt.start()
        self._thread = threading.Thread(target=self._run, args=(on_update,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._reader.close()
        self._mqtt.stop()

    def payload(self, history_minutes: int = 60) -> dict[str, object]:
        with self._lock:
            reading = self._reading.to_payload()
            warnings = list(self._warnings)
            connected = self._connected
            error = self._error
        return {
            "connected": connected,
            "backend": self._backend,
            "error": error,
            "readings": reading,
            "warnings": warnings,
            "thresholds": self._thresholds,
            "history": self._store.history(minutes=history_minutes),
            "summary": self._store.summary(minutes=history_minutes),
            "mqtt": self._mqtt.payload(),
        }

    def acknowledge_warning(self, code: str) -> list[dict[str, object]]:
        with self._lock:
            self._acked_warning_codes.add(code)
            for warning in self._warnings:
                if warning.get("code") == code:
                    warning["acknowledged"] = True
            return list(self._warnings)

    def _run(self, on_update) -> None:
        next_sample = 0.0
        while not self._shutdown.is_set():
            loop_started_at = time.time()
            try:
                reading = self._reader.read_next(self._reading, timeout=1.0)
                now = time.time()
                should_store = now >= next_sample and reading.updated_at
                with self._lock:
                    self._reading = reading
                    if self._reader.mock:
                        self._connected = True
                        self._error = ""
                    else:
                        self._connected = True
                        self._error = ""
                    self._warnings = self._evaluate_warnings(reading)
                if should_store:
                    self._store.add_reading(reading)
                    self._store.prune(int(os.environ.get("BAC_RETENTION_DAYS", "30")))
                    self._mqtt.publish_status({"hostname": socket.gethostname(), **reading.to_payload()})
                    next_sample = now + self.SAMPLE_SECONDS
                if on_update:
                    on_update()
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._error = str(exc)
                    self._warnings = self._evaluate_warnings(self._reading)
                if on_update:
                    on_update()
                self._shutdown.wait(2)
                continue
            elapsed = time.time() - loop_started_at
            self._shutdown.wait(max(0.0, self.SAMPLE_SECONDS - elapsed))

    def _evaluate_warnings(self, reading: CompressorReading) -> list[dict[str, object]]:
        now = time.time()
        checks = [
            (
                "temp_high",
                "High temperature",
                self._c_to_f(reading.temperature_c),
                self._thresholds["temperature_f_max"],
                "F",
            ),
            ("humidity_high", "High humidity", reading.humidity_pct, self._thresholds["humidity_pct_max"], "%"),
            ("vibration_high", "High vibration", reading.vibration_g, self._thresholds["vibration_g_max"], "g"),
            ("current_high", "High AC current", reading.current_a, self._thresholds["current_a_max"], "A"),
        ]
        warnings: list[dict[str, object]] = []
        for code, label, value, limit, unit in checks:
            if value is not None and float(value) > float(limit):
                warnings.append(self._warning(code, label, value, limit, unit, now))
        if reading.float_switch is True:
            warnings.append(self._warning("tank_float_full", "Water tank full", 1, 1, "full", now))
        if reading.updated_at and now - reading.updated_at > self._thresholds["stale_seconds"]:
            warnings.append(
                self._warning(
                    "sensor_stale",
                    "Sensor data stale",
                    round(now - reading.updated_at, 1),
                    self._thresholds["stale_seconds"],
                    "s",
                    now,
                )
            )
        return warnings

    def _c_to_f(self, value: float | None) -> float | None:
        if value is None:
            return None
        return round((float(value) * 9 / 5) + 32, 1)

    def _warning(
        self,
        code: str,
        label: str,
        value: object,
        limit: object,
        unit: str,
        now: float,
    ) -> dict[str, object]:
        warning = {
            "code": code,
            "label": label,
            "value": value,
            "limit": limit,
            "unit": unit,
            "at": now,
            "acknowledged": code in self._acked_warning_codes,
        }
        if now - self._last_warning_at.get(code, 0) >= self._thresholds["warning_cooldown_seconds"]:
            self._last_warning_at[code] = now
            self._mqtt.publish_warning({"hostname": socket.gethostname(), **warning})
        return warning
