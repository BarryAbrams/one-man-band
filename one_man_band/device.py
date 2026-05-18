from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Final

try:
    from smbus2 import SMBus
except ImportError:  # pragma: no cover - depends on target hardware
    SMBus = None

try:
    import RPi.GPIO as GPIO
except ImportError:  # pragma: no cover - depends on target hardware
    GPIO = None


I2C_BUS: Final[int] = 1
DEVICE: Final[int] = 0x12
BH1750_ADDRESS: Final[int] = 0x23
BH1750_POWER_ON: Final[int] = 0x01
BH1750_RESET: Final[int] = 0x07
BH1750_ONE_TIME_HIGH_RES_MODE: Final[int] = 0x20
BH1750_MEASUREMENT_SECONDS: Final[float] = 0.18
BH1750_UPDATE_SECONDS: Final[float] = 1.0
BH1750_DEFAULT_THRESHOLD_LUX: Final[float] = 200.0
I2C_WRITE_ATTEMPTS: Final[int] = 5
I2C_WRITE_RETRY_SECONDS: Final[float] = 0.01
SOLENOID_WRITE_REPEATS: Final[int] = 3
SOLENOID_WRITE_REPEAT_SECONDS: Final[float] = 0.02

REG_VERSION: Final[int] = 0x00
REG_RAILS: Final[int] = 0x01
REG_SOLENOIDS: Final[int] = 0x02
REG_ALARMS: Final[int] = 0x03
REG_PIXEL_COMMAND: Final[int] = 0x40
REG_STATUS_SNAPSHOT: Final[int] = 0x60
STATUS_SNAPSHOT_LENGTH: Final[int] = 8
STATUS_SNAPSHOT_MAGIC: Final[int] = 0xA5
STATUS_SNAPSHOT_VERSION: Final[int] = 2
STATUS_SNAPSHOT_CHECKSUM_INDEX: Final[int] = STATUS_SNAPSHOT_LENGTH - 1

PIXEL_ANIMATION_STATIC: Final[int] = 0
PIXEL_ANIMATION_FADE: Final[int] = PIXEL_ANIMATION_STATIC
PIXEL_ANIMATION_CANDLE_FLICKER: Final[int] = 1

RAILS: Final[dict[str, int]] = {
    "12V_A": 1 << 0,
    "12V_B": 1 << 1,
    "12V_C": 1 << 2,
    "8V": 1 << 3,
}
FIXED_RAIL_STATE: Final[int] = (
    RAILS["12V_A"] | RAILS["12V_B"] | RAILS["12V_C"] | RAILS["8V"]
)

SOLENOIDS: Final[dict[str, int]] = {
    "P0": 1 << 0,
    "P1": 1 << 1,
    "P2": 1 << 2,
    "P3": 1 << 3,
}

ALARM_BITS: Final[dict[str, int]] = {
    "12V_C_ALARM": 1 << 0,
    "8V_ALARM": 1 << 1,
}

GPIO_INPUTS: Final[dict[str, int]] = {
    "1": 13,
    "2": 6,
    "3": 5,
    "4": 22,
    "5": 27,
    "6": 17,
}

SERVO_CHANNELS: Final[tuple[int, ...]] = ()

PIXEL_RAILS: Final[dict[str, int]] = {
    "1": 1 << 0,
    "2": 1 << 1,
    "3": 1 << 2,
    "4": 1 << 3,
}

PIXEL_ANIMATIONS: Final[dict[str, dict[str, object]]] = {
    "static": {
        "id": PIXEL_ANIMATION_STATIC,
        "label": "Static Color",
    },
    "candle": {
        "id": PIXEL_ANIMATION_CANDLE_FLICKER,
        "label": "Candle Flicker",
    },
}


@dataclass(slots=True)
class DeviceState:
    version: int = 0
    rails: int = 0
    solenoids: int = 0
    servo_enable_mask: int = 0
    servo_values: list[int] | None = None
    alarms: int = 0
    ina_presence: int = 0
    ina_voltage_mv: dict[str, int] | None = None
    ina_current_ma: dict[str, int] | None = None
    connected: bool = False
    error: str = ""
    backend: str = "disconnected"
    status_source: str = "unknown"
    gpio_inputs: dict[str, bool] | None = None
    gpio_input_overrides: dict[str, bool] | None = None
    gpio_error: str = ""
    pixel_command: dict[str, object] | None = None
    ambient_light_lux: float | None = None
    ambient_light_raw: int | None = None
    ambient_light_error: str = ""
    ambient_light_state: str = "unknown"
    ambient_light_low_threshold_lux: float = BH1750_DEFAULT_THRESHOLD_LUX
    ambient_light_high_threshold_lux: float = BH1750_DEFAULT_THRESHOLD_LUX

    def to_payload(self) -> dict[str, object]:
        return {
            **asdict(self),
            "rails_map": {name: bool(self.rails & mask) for name, mask in RAILS.items()},
            "solenoids_map": {
                name: bool(self.solenoids & mask) for name, mask in SOLENOIDS.items()
            },
            "servo_enabled_map": {
                str(channel): bool(self.servo_enable_mask & (1 << channel))
                for channel in SERVO_CHANNELS
            },
            "servo_values_map": {
                str(channel): self._servo_value(channel) for channel in SERVO_CHANNELS
            },
            "gpio_inputs_map": {
                name: self._gpio_value(name) for name in GPIO_INPUTS
            },
            "gpio_physical_map": self.gpio_inputs or {},
            "gpio_override_map": self.gpio_input_overrides or {},
            "pixel_command": self.pixel_command or {},
        }

    def _servo_value(self, channel: int) -> int:
        if self.servo_values is None or channel >= len(self.servo_values):
            return 0
        return int(self.servo_values[channel])

    def _gpio_value(self, name: str) -> bool:
        if self.gpio_input_overrides and name in self.gpio_input_overrides:
            return bool(self.gpio_input_overrides[name])
        if self.gpio_inputs and name in self.gpio_inputs:
            return bool(self.gpio_inputs[name])
        return False


class DeviceController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._i2c_write_listener: Callable[[DeviceState], None] | None = None
        self._mock_mode = os.environ.get("OMB_MOCK_HARDWARE", "0") == "1"
        self._gpio_mode = os.environ.get("OMB_GPIO_PULL", "up").strip().lower()
        self._gpio_ready = False
        self._gpio_error = ""
        self._bh1750_address = self._int_env("OMB_BH1750_ADDRESS", BH1750_ADDRESS)
        single_threshold = self._float_env(
            "OMB_BH1750_THRESHOLD_LUX",
            BH1750_DEFAULT_THRESHOLD_LUX,
        )
        self._bh1750_low_threshold_lux = self._float_env(
            "OMB_BH1750_LOW_THRESHOLD_LUX",
            single_threshold,
        )
        self._bh1750_high_threshold_lux = self._float_env(
            "OMB_BH1750_HIGH_THRESHOLD_LUX",
            single_threshold,
        )
        if self._bh1750_low_threshold_lux > self._bh1750_high_threshold_lux:
            self._bh1750_low_threshold_lux = self._bh1750_high_threshold_lux
        self._bh1750_update_seconds = self._float_env(
            "OMB_BH1750_UPDATE_SECONDS",
            BH1750_UPDATE_SECONDS,
        )
        self._state = DeviceState(
            version=2 if self._mock_mode else 0,
            rails=FIXED_RAIL_STATE if self._mock_mode else 0,
            ina_presence=0,
            connected=self._mock_mode,
            backend="mock" if self._mock_mode else "i2c",
            status_source="mock" if self._mock_mode else "unknown",
            servo_values=[],
            ina_voltage_mv={},
            ina_current_ma={},
            gpio_inputs={name: False for name in GPIO_INPUTS},
            gpio_input_overrides={},
            pixel_command={},
            ambient_light_lux=0.0 if self._mock_mode else None,
            ambient_light_raw=0 if self._mock_mode else None,
            ambient_light_state="low" if self._mock_mode else "unknown",
            ambient_light_low_threshold_lux=self._bh1750_low_threshold_lux,
            ambient_light_high_threshold_lux=self._bh1750_high_threshold_lux,
        )
        self._setup_gpio()
        self._last_bh1750_read_at = 0.0

    def set_i2c_write_listener(
        self, listener: Callable[[DeviceState], None] | None
    ) -> None:
        self._i2c_write_listener = listener

    def _notify_i2c_write(self, state: DeviceState) -> None:
        if self._mock_mode or self._i2c_write_listener is None:
            return
        self._i2c_write_listener(DeviceState(**asdict(state)))

    def _setup_gpio(self) -> None:
        if GPIO is None:
            self._gpio_error = "No compatible GPIO library is installed in this Python environment"
            return

        pull_mode = {
            "off": GPIO.PUD_OFF,
            "up": GPIO.PUD_UP,
            "down": GPIO.PUD_DOWN,
        }.get(self._gpio_mode)

        if pull_mode is None:
            self._gpio_error = f"Invalid OMB_GPIO_PULL setting: {self._gpio_mode}"
            return

        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            for pin in GPIO_INPUTS.values():
                GPIO.setup(pin, GPIO.IN, pull_up_down=pull_mode)
            self._gpio_ready = True
            self._gpio_error = ""
        except RuntimeError as exc:
            self._gpio_ready = False
            self._gpio_error = str(exc)

    def _read_gpio_inputs(self) -> tuple[dict[str, bool], str]:
        if self._mock_mode:
            return (self._state.gpio_inputs or {name: False for name in GPIO_INPUTS}, "")

        if GPIO is None or not self._gpio_ready:
            return ({name: False for name in GPIO_INPUTS}, self._gpio_error)

        try:
            return (
                {name: bool(GPIO.input(pin)) for name, pin in GPIO_INPUTS.items()},
                "",
            )
        except RuntimeError as exc:
            self._gpio_ready = False
            self._gpio_error = str(exc)
            return ({name: False for name in GPIO_INPUTS}, self._gpio_error)

    def _int_env(self, name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)), 0)
        except ValueError:
            return default

    def _float_env(self, name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, str(default)))
        except ValueError:
            return default

    def _read_reg(self, bus: SMBus, reg: int) -> int:
        bus.write_byte(DEVICE, reg)
        return bus.read_byte(DEVICE)

    def _write_reg(self, bus: SMBus, reg: int, value: int) -> None:
        bus.write_byte_data(DEVICE, reg, value & 0xFF)

    def _write_block(self, bus: SMBus, reg: int, values: list[int]) -> None:
        bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF for value in values])

    def _read_block(self, bus: SMBus, reg: int, length: int) -> list[int]:
        return [value & 0xFF for value in bus.read_i2c_block_data(DEVICE, reg, length)]

    def _read_ambient_light(self, bus: SMBus) -> tuple[float | None, int | None, str]:
        try:
            bus.write_byte(self._bh1750_address, BH1750_POWER_ON)
            bus.write_byte(self._bh1750_address, BH1750_RESET)
            bus.write_byte(self._bh1750_address, BH1750_ONE_TIME_HIGH_RES_MODE)
            time.sleep(BH1750_MEASUREMENT_SECONDS)
            data = bus.read_i2c_block_data(
                self._bh1750_address,
                BH1750_ONE_TIME_HIGH_RES_MODE,
                2,
            )
            raw = ((data[0] & 0xFF) << 8) | (data[1] & 0xFF)
            return (raw / 1.2, raw, "")
        except OSError as exc:
            return (None, None, str(exc))

    def _with_fixed_rails(self, state: DeviceState) -> DeviceState:
        state.rails = FIXED_RAIL_STATE
        return state

    def _apply_cached_register(self, reg: int, value: int) -> None:
        if reg == REG_RAILS:
            self._state.rails = FIXED_RAIL_STATE
        elif reg == REG_SOLENOIDS:
            self._state.solenoids = value & 0x0F

    def _write_reg_with_retries(self, reg: int, value: int) -> None:
        attempts = max(1, self._int_env("OMB_I2C_WRITE_ATTEMPTS", I2C_WRITE_ATTEMPTS))
        last_error: OSError | None = None
        for attempt in range(attempts):
            try:
                with SMBus(I2C_BUS) as bus:
                    self._write_reg(bus, reg, value)
                return
            except OSError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(I2C_WRITE_RETRY_SECONDS)
        raise last_error or OSError("I2C write failed")

    def _write_solenoid_mask(self, value: int) -> None:
        repeats = max(
            1,
            self._int_env("OMB_SOLENOID_WRITE_REPEATS", SOLENOID_WRITE_REPEATS),
        )
        repeat_seconds = max(
            0.0,
            self._float_env(
                "OMB_SOLENOID_WRITE_REPEAT_SECONDS",
                SOLENOID_WRITE_REPEAT_SECONDS,
            ),
        )
        for repeat in range(repeats):
            self._write_reg_with_retries(REG_SOLENOIDS, value & 0x0F)
            if repeat + 1 < repeats and repeat_seconds > 0:
                time.sleep(repeat_seconds)

    def _ambient_light_state_for_lux(self, lux: float | None) -> str:
        if lux is None:
            return "unknown"
        previous = self._state.ambient_light_state
        if previous == "high":
            return "low" if lux <= self._bh1750_low_threshold_lux else "high"
        return "high" if lux >= self._bh1750_high_threshold_lux else "low"

    def _apply_ambient_light_reading(
        self,
        state: DeviceState,
        lux: float | None,
        raw: int | None,
        error: str,
    ) -> None:
        previous_state = self._state.ambient_light_state
        state.ambient_light_lux = lux
        state.ambient_light_raw = raw
        state.ambient_light_error = error
        state.ambient_light_state = self._ambient_light_state_for_lux(lux)
        state.ambient_light_low_threshold_lux = self._bh1750_low_threshold_lux
        state.ambient_light_high_threshold_lux = self._bh1750_high_threshold_lux

        print(
            f"Ambient light: state={state.ambient_light_state} lux={lux} "
            f"raw={raw} low={self._bh1750_low_threshold_lux:.1f} "
            f"high={self._bh1750_high_threshold_lux:.1f} error={error}",
            flush=True,
        )
        if state.ambient_light_state != previous_state:
            print(
                f"BH1750 state changed: {previous_state} -> {state.ambient_light_state}",
                flush=True,
            )

    def _copy_cached_ambient_light(self, state: DeviceState) -> None:
        state.ambient_light_lux = self._state.ambient_light_lux
        state.ambient_light_raw = self._state.ambient_light_raw
        state.ambient_light_error = self._state.ambient_light_error
        state.ambient_light_state = self._state.ambient_light_state
        state.ambient_light_low_threshold_lux = self._bh1750_low_threshold_lux
        state.ambient_light_high_threshold_lux = self._bh1750_high_threshold_lux

    def _with_ambient_light_config(self, state: DeviceState) -> DeviceState:
        state.ambient_light_low_threshold_lux = self._bh1750_low_threshold_lux
        state.ambient_light_high_threshold_lux = self._bh1750_high_threshold_lux
        return state

    def _update_ambient_light_once_per_second(self, bus: SMBus, state: DeviceState) -> None:
        now = time.monotonic()

        if now - self._last_bh1750_read_at < self._bh1750_update_seconds:
            self._copy_cached_ambient_light(state)
            return

        self._last_bh1750_read_at = now
        self._apply_ambient_light_reading(state, *self._read_ambient_light(bus))

    def refresh_ambient_light(self) -> DeviceState:
        with self._lock:
            if self._mock_mode:
                return DeviceState(**asdict(self._state))
            if SMBus is None:
                self._state.ambient_light_error = "smbus2 is not installed"
                self._state.ambient_light_state = "unknown"
                return DeviceState(**asdict(self._state))
            try:
                with SMBus(I2C_BUS) as bus:
                    self._last_bh1750_read_at = time.monotonic()
                    self._apply_ambient_light_reading(
                        self._state,
                        *self._read_ambient_light(bus),
                    )
            except OSError as exc:
                self._apply_ambient_light_reading(self._state, None, None, str(exc))
            return DeviceState(**asdict(self._state))

    def _snapshot_checksum(self, data: list[int]) -> int:
        return (-sum(data[:STATUS_SNAPSHOT_CHECKSUM_INDEX])) & 0xFF

    def _read_snapshot_state(
        self,
        bus: SMBus,
        gpio_inputs: dict[str, bool],
        gpio_error: str,
    ) -> DeviceState:
        data = self._read_block(bus, REG_STATUS_SNAPSHOT, STATUS_SNAPSHOT_LENGTH)
        if len(data) != STATUS_SNAPSHOT_LENGTH:
            raise OSError(
                f"Invalid RP2040 snapshot length: expected {STATUS_SNAPSHOT_LENGTH}, got {len(data)}"
            )
        if data[0] != STATUS_SNAPSHOT_MAGIC:
            raise OSError(f"Invalid RP2040 snapshot magic: 0x{data[0]:02x}")
        if data[1] != STATUS_SNAPSHOT_VERSION:
            raise OSError(f"Unsupported RP2040 snapshot version: {data[1]}")
        expected_checksum = self._snapshot_checksum(data)
        actual_checksum = data[STATUS_SNAPSHOT_CHECKSUM_INDEX]
        if actual_checksum != expected_checksum:
            raise OSError(
                f"Invalid RP2040 snapshot checksum: expected 0x{expected_checksum:02x}, got 0x{actual_checksum:02x}"
            )

        return DeviceState(
            version=data[1],
            rails=data[2],
            solenoids=data[3],
            alarms=0,
            ina_presence=0,
            servo_enable_mask=0,
            servo_values=[],
            ina_voltage_mv={},
            ina_current_ma={},
            connected=True,
            error="",
            backend="i2c",
            status_source="i2c_snapshot",
            gpio_inputs=gpio_inputs,
            gpio_error=gpio_error,
            pixel_command={
                **(self._state.pixel_command or {}),
                "active_mask": data[4] & 0x0F,
                "active_rails": [
                    name for name, mask in PIXEL_RAILS.items() if data[4] & mask
                ],
                "tca9534_ready": bool(data[5]),
            },
        )

    def _read_from_bus(self) -> DeviceState:
        gpio_inputs, gpio_error = self._read_gpio_inputs()

        if SMBus is None:
            return DeviceState(
                connected=False,
                error="smbus2 is not installed",
                backend="unavailable",
                gpio_inputs=gpio_inputs,
                gpio_error=gpio_error,
            )

        try:
            with SMBus(I2C_BUS) as bus:
                last_error: OSError | None = None
                for _attempt in range(5):
                    try:
                        state = self._with_fixed_rails(
                            self._read_snapshot_state(bus, gpio_inputs, gpio_error)
                        )
                        self._update_ambient_light_once_per_second(bus, state)
                        return state
                    except OSError as exc:
                        last_error = exc
                return DeviceState(
                    connected=False,
                    error=str(last_error or "RP2040 status read failed"),
                    backend="i2c",
                    gpio_inputs=gpio_inputs,
                    gpio_error=gpio_error,
                )
        except OSError as exc:
            return DeviceState(
                connected=False,
                error=str(exc),
                backend="i2c",
                gpio_inputs=gpio_inputs,
                gpio_error=gpio_error,
            )

    def read_state(self) -> DeviceState:
        with self._lock:
            overrides = dict(self._state.gpio_input_overrides or {})
            pixel_command = dict(self._state.pixel_command or {})
            if self._mock_mode:
                self._state.connected = True
                self._state.backend = "mock"
                self._state.status_source = "mock"
                self._state.rails = FIXED_RAIL_STATE
                self._state.gpio_inputs, self._state.gpio_error = self._read_gpio_inputs()
                self._state.gpio_input_overrides = overrides
                self._state.pixel_command = pixel_command
                return DeviceState(**asdict(self._state))

            self._state = self._read_from_bus()
            self._with_ambient_light_config(self._state)
            self._state.gpio_input_overrides = overrides
            self._state.pixel_command = {
                **pixel_command,
                **(self._state.pixel_command or {}),
            }
            return DeviceState(**asdict(self._state))

    def read_cached_state(self, refresh_gpio: bool = True) -> DeviceState:
        with self._lock:
            if refresh_gpio:
                self._state.gpio_inputs, self._state.gpio_error = self._read_gpio_inputs()
            self._with_ambient_light_config(self._state)
            return DeviceState(**asdict(self._state))

    def _update_register(self, reg: int, value: int) -> DeviceState:
        with self._lock:
            wrote_to_i2c = False
            overrides = dict(self._state.gpio_input_overrides or {})
            pixel_command = dict(self._state.pixel_command or {})
            if self._mock_mode:
                self._apply_cached_register(reg, value)
                self._state.rails = FIXED_RAIL_STATE
                self._state.connected = True
                self._state.error = ""
                self._state.status_source = "mock"
                self._state.rails = FIXED_RAIL_STATE
                self._state.gpio_inputs, self._state.gpio_error = self._read_gpio_inputs()
                self._state.gpio_input_overrides = overrides
                self._state.pixel_command = pixel_command
                return DeviceState(**asdict(self._state))

            if SMBus is None:
                gpio_inputs, gpio_error = self._read_gpio_inputs()
                self._state = DeviceState(
                    connected=False,
                    error="smbus2 is not installed",
                    backend="unavailable",
                    gpio_inputs=gpio_inputs,
                    gpio_error=gpio_error,
                    gpio_input_overrides=overrides,
                    pixel_command=pixel_command,
                )
                return DeviceState(**asdict(self._state))

            try:
                if reg == REG_SOLENOIDS:
                    self._write_solenoid_mask(value)
                else:
                    with SMBus(I2C_BUS) as bus:
                        self._write_reg(bus, reg, value)
                wrote_to_i2c = True
                self._apply_cached_register(reg, value)
                self._state.connected = True
                self._state.error = ""
                self._state.backend = "i2c"
                self._state.status_source = "i2c_write_cache"
                self._state.gpio_input_overrides = overrides
                self._state.pixel_command = pixel_command
            except OSError as exc:
                gpio_inputs, gpio_error = self._read_gpio_inputs()
                self._state = DeviceState(
                    connected=False,
                    error=str(exc),
                    backend="i2c",
                    gpio_inputs=gpio_inputs,
                    gpio_error=gpio_error,
                    gpio_input_overrides=overrides,
                    pixel_command=pixel_command,
                )
            result = DeviceState(**asdict(self._state))
        if wrote_to_i2c:
            self._notify_i2c_write(result)
        return result

    def toggle_rail(self, name: str) -> DeviceState:
        if name not in RAILS:
            raise ValueError(f"Unknown rail: {name}")
        return self.read_state()

    def set_all_rails(self, enabled: bool) -> DeviceState:
        return self.read_state()

    def set_rails_enabled(self, names: list[str], enabled: bool) -> DeviceState:
        unknown = [name for name in names if name not in RAILS]
        if unknown:
            raise ValueError(f"Unknown rail: {unknown[0]}")
        return self.read_state()

    def toggle_solenoid(self, name: str) -> DeviceState:
        if name not in SOLENOIDS:
            raise ValueError(f"Unknown solenoid: {name}")
        mask = SOLENOIDS[name]
        with self._lock:
            state = self.read_cached_state(refresh_gpio=False)
            return self._update_register(REG_SOLENOIDS, state.solenoids ^ mask)

    def set_solenoid(self, name: str, enabled: bool) -> DeviceState:
        if name not in SOLENOIDS:
            raise ValueError(f"Unknown solenoid: {name}")
        mask = SOLENOIDS[name]
        with self._lock:
            state = self.read_cached_state(refresh_gpio=False)
            value = (state.solenoids | mask) if enabled else (state.solenoids & ~mask)
            return self._update_register(REG_SOLENOIDS, value)

    def clear_solenoids(self) -> DeviceState:
        with self._lock:
            return self._update_register(REG_SOLENOIDS, 0x00)

    def toggle_gpio_override(self, name: str) -> DeviceState:
        if name not in GPIO_INPUTS:
            raise ValueError(f"Unknown input pin: {name}")
        with self._lock:
            state = self.read_cached_state(refresh_gpio=True)
            current_override = None
            if state.gpio_input_overrides:
                current_override = state.gpio_input_overrides.get(name)
            physical_value = False
            if state.gpio_inputs:
                physical_value = bool(state.gpio_inputs.get(name, False))

            if current_override is None:
                next_value = not physical_value
            else:
                next_value = not bool(current_override)

            if self._state.gpio_input_overrides is None:
                self._state.gpio_input_overrides = {}
            self._state.gpio_input_overrides[name] = next_value
            return DeviceState(**asdict(self._state))

    def clear_gpio_override(self, name: str) -> DeviceState:
        if name not in GPIO_INPUTS:
            raise ValueError(f"Unknown input pin: {name}")
        with self._lock:
            state = self.read_cached_state(refresh_gpio=True)
            overrides = dict(state.gpio_input_overrides or {})
            overrides.pop(name, None)
            self._state.gpio_input_overrides = overrides
            return DeviceState(**asdict(self._state))

    def clear_all_gpio_overrides(self) -> DeviceState:
        with self._lock:
            state = self.read_cached_state(refresh_gpio=True)
            self._state = DeviceState(**asdict(state))
            self._state.gpio_input_overrides = {}
            return DeviceState(**asdict(self._state))

    def set_servo_enabled(self, channel: int, enabled: bool) -> DeviceState:
        return self.read_cached_state(refresh_gpio=False)

    def set_all_servos_enabled(self, enabled: bool) -> DeviceState:
        return self.read_cached_state(refresh_gpio=False)

    def set_servo_value(self, channel: int, value: int) -> DeviceState:
        if not 0 <= value <= 255:
            raise ValueError(f"Servo value must be between 0 and 255: {value}")
        return self.read_cached_state(refresh_gpio=False)

    def _apply_servo_value_cache(self) -> None:
        return

    def animate_pixels(
        self,
        rail_mask: int,
        start: int,
        count: int,
        start_rgb: tuple[int, int, int],
        end_rgb: tuple[int, int, int],
        duration_ms: int,
        animation_id: int = 0,
    ) -> DeviceState:
        if not 0 <= rail_mask <= 0x0F or rail_mask == 0:
            raise ValueError("Select at least one pixel rail")
        if not 0 <= start <= 99:
            raise ValueError(f"Start pixel must be between 0 and 99: {start}")
        if not 0 <= count <= 100:
            raise ValueError(f"Pixel count must be between 0 and 100: {count}")
        if not 0 <= duration_ms <= 65535:
            raise ValueError(f"Duration must be between 0 and 65535 ms: {duration_ms}")
        if animation_id not in {
            PIXEL_ANIMATION_STATIC,
            PIXEL_ANIMATION_CANDLE_FLICKER,
        }:
            raise ValueError(f"Unknown pixel animation ID: {animation_id}")
        for channel_name, rgb in (("base", start_rgb), ("parameter", end_rgb)):
            if len(rgb) != 3:
                raise ValueError(f"{channel_name} color must have red, green, and blue values")
            if any(value < 0 or value > 255 for value in rgb):
                raise ValueError(f"{channel_name} color values must be between 0 and 255")

        payload = [
            rail_mask,
            start,
            count,
            *start_rgb,
            *end_rgb,
            duration_ms & 0xFF,
            (duration_ms >> 8) & 0xFF,
            1,
            animation_id,
        ]

        with self._lock:
            wrote_to_i2c = False
            if self._mock_mode:
                self._state.pixel_command = self._pixel_command_payload(
                    rail_mask, start, count, start_rgb, end_rgb, duration_ms, animation_id
                )
                return DeviceState(**asdict(self._state))

            if SMBus is None:
                gpio_inputs, gpio_error = self._read_gpio_inputs()
                self._state = DeviceState(
                    connected=False,
                    error="smbus2 is not installed",
                    backend="unavailable",
                    gpio_inputs=gpio_inputs,
                    gpio_error=gpio_error,
                    gpio_input_overrides=dict(self._state.gpio_input_overrides or {}),
                    pixel_command=dict(self._state.pixel_command or {}),
                )
                return DeviceState(**asdict(self._state))

            try:
                with SMBus(I2C_BUS) as bus:
                    self._write_block(bus, REG_PIXEL_COMMAND, payload)
                    wrote_to_i2c = True
                self._state.connected = True
                self._state.error = ""
                self._state.backend = "i2c"
                self._state.status_source = "i2c_write_cache"
                self._state.pixel_command = self._pixel_command_payload(
                    rail_mask, start, count, start_rgb, end_rgb, duration_ms, animation_id
                )
            except OSError as exc:
                gpio_inputs, gpio_error = self._read_gpio_inputs()
                self._state = DeviceState(
                    connected=False,
                    error=str(exc),
                    backend="i2c",
                    gpio_inputs=gpio_inputs,
                    gpio_error=gpio_error,
                    gpio_input_overrides=dict(self._state.gpio_input_overrides or {}),
                    pixel_command=dict(self._state.pixel_command or {}),
                )
            result = DeviceState(**asdict(self._state))
        if wrote_to_i2c:
            self._notify_i2c_write(result)
        return result

    def _pixel_command_payload(
        self,
        rail_mask: int,
        start: int,
        count: int,
        start_rgb: tuple[int, int, int],
        end_rgb: tuple[int, int, int],
        duration_ms: int,
        animation_id: int,
    ) -> dict[str, object]:
        return {
            "rail_mask": rail_mask,
            "rails": [
                name for name, mask in PIXEL_RAILS.items() if rail_mask & mask
            ],
            "start": start,
            "count": count,
            "base_rgb": list(start_rgb),
            "param_rgb": list(end_rgb),
            "start_rgb": list(start_rgb),
            "end_rgb": list(end_rgb),
            "duration_ms": duration_ms,
            "animation_id": animation_id,
            "animation_name": self._pixel_animation_name(animation_id),
        }

    def _pixel_animation_name(self, animation_id: int) -> str:
        for name, config in PIXEL_ANIMATIONS.items():
            if int(config["id"]) == animation_id:
                return name
        return "unknown"

    def close(self) -> None:
        with self._lock:
            if GPIO is not None and self._gpio_ready:
                GPIO.cleanup()
            self._gpio_ready = False

    def metadata(self) -> dict[str, object]:
        return {
            "rails": list(RAILS.keys()),
            "solenoids": list(SOLENOIDS.keys()),
            "servos": list(SERVO_CHANNELS),
            "pixel_rails": list(PIXEL_RAILS.keys()),
            "pixel_animations": PIXEL_ANIMATIONS,
            "ina_channels": {},
            "gpio_inputs": list(GPIO_INPUTS.keys()),
            "gpio_pins": GPIO_INPUTS,
            "mock_mode": self._mock_mode,
            "gpio_pull": self._gpio_mode,
            "ambient_light_address": self._bh1750_address,
            "ambient_light_update_seconds": self._bh1750_update_seconds,
            "ambient_light_low_threshold_lux": self._bh1750_low_threshold_lux,
            "ambient_light_high_threshold_lux": self._bh1750_high_threshold_lux,
        }
