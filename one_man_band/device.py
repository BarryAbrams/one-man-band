from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from typing import Final

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

REG_VERSION: Final[int] = 0x00
REG_RAILS: Final[int] = 0x01
REG_SOLENOIDS: Final[int] = 0x02
REG_ALARMS: Final[int] = 0x03
REG_INA_PRESENCE: Final[int] = 0x04
REG_SERVO_ENABLE_MASK: Final[int] = 0x05
REG_SERVO_BASE: Final[int] = 0x10
REG_INA0_VOLTAGE_L: Final[int] = 0x20
REG_INA0_CURRENT_L: Final[int] = 0x22
REG_INA1_VOLTAGE_L: Final[int] = 0x24
REG_INA1_CURRENT_L: Final[int] = 0x26
REG_PIXEL_COMMAND: Final[int] = 0x40

RAILS: Final[dict[str, int]] = {
    "12V_A": 1 << 0,
    "12V_B": 1 << 1,
    "12V_C": 1 << 2,
    "8V": 1 << 3,
}

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

INA_BITS: Final[dict[str, int]] = {
    "12V_C": 1 << 0,
    "8V": 1 << 1,
}

INA_CHANNELS: Final[dict[str, dict[str, object]]] = {
    "12V_C": {
        "title": "Solenoid Line",
        "presence_mask": 1 << 0,
        "voltage_reg": REG_INA0_VOLTAGE_L,
        "current_reg": REG_INA0_CURRENT_L,
    },
    "8V": {
        "title": "Servo Line",
        "presence_mask": 1 << 1,
        "voltage_reg": REG_INA1_VOLTAGE_L,
        "current_reg": REG_INA1_CURRENT_L,
    },
}

GPIO_INPUTS: Final[dict[str, int]] = {
    "1": 17,
    "2": 27,
    "3": 22,
    "4": 5,
    "5": 6,
    "6": 13,
}

SERVO_CHANNELS: Final[tuple[int, ...]] = tuple(range(8))

PIXEL_RAILS: Final[dict[str, int]] = {
    "1": 1 << 0,
    "2": 1 << 1,
    "3": 1 << 2,
    "4": 1 << 3,
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
    gpio_inputs: dict[str, bool] | None = None
    gpio_input_overrides: dict[str, bool] | None = None
    gpio_error: str = ""
    pixel_command: dict[str, object] | None = None

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
            "ina_present_map": {
                name: bool(self.ina_presence & int(config["presence_mask"]))
                for name, config in INA_CHANNELS.items()
            },
            "ina_voltage_map": self.ina_voltage_mv or {},
            "ina_current_map": self.ina_current_ma or {},
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
        self._mock_mode = os.environ.get("OMB_MOCK_HARDWARE", "0") == "1"
        self._gpio_mode = os.environ.get("OMB_GPIO_PULL", "off").strip().lower()
        self._gpio_ready = False
        self._gpio_error = ""
        self._state = DeviceState(
            version=1 if self._mock_mode else 0,
            ina_presence=0b11 if self._mock_mode else 0,
            connected=self._mock_mode,
            backend="mock" if self._mock_mode else "i2c",
            servo_values=[127 for _ in SERVO_CHANNELS],
            ina_voltage_mv={"12V_C": 0, "8V": 8000},
            ina_current_ma={"12V_C": 0, "8V": 0},
            gpio_inputs={name: False for name in GPIO_INPUTS},
            gpio_input_overrides={},
            pixel_command={},
        )
        self._setup_gpio()

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

    def _read_reg(self, bus: SMBus, reg: int) -> int:
        bus.write_byte(DEVICE, reg)
        return bus.read_byte(DEVICE)

    def _write_reg(self, bus: SMBus, reg: int, value: int) -> None:
        bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF])

    def _write_block(self, bus: SMBus, reg: int, values: list[int]) -> None:
        bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF for value in values])

    def _read_u16_le(self, bus: SMBus, reg_low: int) -> int:
        low = self._read_reg(bus, reg_low)
        high = self._read_reg(bus, reg_low + 1)
        return low | (high << 8)

    def _read_i16_le(self, bus: SMBus, reg_low: int) -> int:
        value = self._read_u16_le(bus, reg_low)
        if value & 0x8000:
            value -= 0x10000
        return value

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
                servo_values = [
                    self._read_reg(bus, REG_SERVO_BASE + channel) for channel in SERVO_CHANNELS
                ]
                ina_voltage_mv = {
                    name: self._read_u16_le(bus, int(config["voltage_reg"]))
                    for name, config in INA_CHANNELS.items()
                }
                ina_current_ma = {
                    name: self._read_i16_le(bus, int(config["current_reg"]))
                    for name, config in INA_CHANNELS.items()
                }
                return DeviceState(
                    version=self._read_reg(bus, REG_VERSION),
                    rails=self._read_reg(bus, REG_RAILS),
                    solenoids=self._read_reg(bus, REG_SOLENOIDS),
                    servo_enable_mask=self._read_reg(bus, REG_SERVO_ENABLE_MASK),
                    servo_values=servo_values,
                    alarms=self._read_reg(bus, REG_ALARMS),
                    ina_presence=self._read_reg(bus, REG_INA_PRESENCE),
                    ina_voltage_mv=ina_voltage_mv,
                    ina_current_ma=ina_current_ma,
                    connected=True,
                    error="",
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
                self._state.gpio_inputs, self._state.gpio_error = self._read_gpio_inputs()
                self._state.gpio_input_overrides = overrides
                self._state.pixel_command = pixel_command
                return DeviceState(**asdict(self._state))

            self._state = self._read_from_bus()
            self._state.gpio_input_overrides = overrides
            self._state.pixel_command = pixel_command
            return DeviceState(**asdict(self._state))

    def _update_register(self, reg: int, value: int) -> DeviceState:
        with self._lock:
            overrides = dict(self._state.gpio_input_overrides or {})
            pixel_command = dict(self._state.pixel_command or {})
            if self._mock_mode:
                if reg == REG_RAILS:
                    self._state.rails = value & 0x0F
                elif reg == REG_SOLENOIDS:
                    self._state.solenoids = value & 0x0F
                elif reg == REG_SERVO_ENABLE_MASK:
                    self._state.servo_enable_mask = value & 0xFF
                elif REG_SERVO_BASE <= reg < REG_SERVO_BASE + len(SERVO_CHANNELS):
                    if self._state.servo_values is None:
                        self._state.servo_values = [127 for _ in SERVO_CHANNELS]
                    self._state.servo_values[reg - REG_SERVO_BASE] = value & 0xFF
                self._state.connected = True
                self._state.error = ""
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
                with SMBus(I2C_BUS) as bus:
                    self._write_reg(bus, reg, value)
                self._state = self._read_from_bus()
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
            return DeviceState(**asdict(self._state))

    def toggle_rail(self, name: str) -> DeviceState:
        if name not in RAILS:
            raise ValueError(f"Unknown rail: {name}")
        mask = RAILS[name]
        state = self.read_state()
        return self._update_register(REG_RAILS, state.rails ^ mask)

    def set_all_rails(self, enabled: bool) -> DeviceState:
        return self._update_register(REG_RAILS, 0x0F if enabled else 0x00)

    def toggle_solenoid(self, name: str) -> DeviceState:
        if name not in SOLENOIDS:
            raise ValueError(f"Unknown solenoid: {name}")
        mask = SOLENOIDS[name]
        state = self.read_state()
        return self._update_register(REG_SOLENOIDS, state.solenoids ^ mask)

    def clear_solenoids(self) -> DeviceState:
        return self._update_register(REG_SOLENOIDS, 0x00)

    def toggle_gpio_override(self, name: str) -> DeviceState:
        if name not in GPIO_INPUTS:
            raise ValueError(f"Unknown input pin: {name}")
        with self._lock:
            state = self.read_state()
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
            state = self.read_state()
            overrides = dict(state.gpio_input_overrides or {})
            overrides.pop(name, None)
            self._state.gpio_input_overrides = overrides
            return DeviceState(**asdict(self._state))

    def clear_all_gpio_overrides(self) -> DeviceState:
        with self._lock:
            state = self.read_state()
            self._state = DeviceState(**asdict(state))
            self._state.gpio_input_overrides = {}
            return DeviceState(**asdict(self._state))

    def set_servo_enabled(self, channel: int, enabled: bool) -> DeviceState:
        if channel not in SERVO_CHANNELS:
            raise ValueError(f"Unknown servo channel: {channel}")
        state = self.read_state()
        mask = 1 << channel
        value = (state.servo_enable_mask | mask) if enabled else (state.servo_enable_mask & ~mask)
        return self._update_register(REG_SERVO_ENABLE_MASK, value)

    def set_all_servos_enabled(self, enabled: bool) -> DeviceState:
        return self._update_register(REG_SERVO_ENABLE_MASK, 0xFF if enabled else 0x00)

    def set_servo_value(self, channel: int, value: int) -> DeviceState:
        if channel not in SERVO_CHANNELS:
            raise ValueError(f"Unknown servo channel: {channel}")
        if not 0 <= value <= 255:
            raise ValueError(f"Servo value must be between 0 and 255: {value}")
        return self._update_register(REG_SERVO_BASE + channel, value)

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
        if not 0 <= animation_id <= 255:
            raise ValueError(f"Animation ID must be between 0 and 255: {animation_id}")
        for channel_name, rgb in (("start", start_rgb), ("end", end_rgb)):
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
                next_state = self._read_from_bus()
                next_state.gpio_input_overrides = dict(self._state.gpio_input_overrides or {})
                next_state.pixel_command = self._pixel_command_payload(
                    rail_mask, start, count, start_rgb, end_rgb, duration_ms, animation_id
                )
                self._state = next_state
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
            return DeviceState(**asdict(self._state))

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
            "start_rgb": list(start_rgb),
            "end_rgb": list(end_rgb),
            "duration_ms": duration_ms,
            "animation_id": animation_id,
        }

    def metadata(self) -> dict[str, object]:
        return {
            "rails": list(RAILS.keys()),
            "solenoids": list(SOLENOIDS.keys()),
            "servos": list(SERVO_CHANNELS),
            "pixel_rails": list(PIXEL_RAILS.keys()),
            "ina_channels": {
                name: {
                    "title": str(config["title"]),
                    "key": name,
                }
                for name, config in INA_CHANNELS.items()
            },
            "gpio_inputs": list(GPIO_INPUTS.keys()),
            "gpio_pins": GPIO_INPUTS,
            "mock_mode": self._mock_mode,
            "gpio_pull": self._gpio_mode,
        }
