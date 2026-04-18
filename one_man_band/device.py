from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from typing import Final

try:
    from smbus2 import SMBus
except ImportError:  # pragma: no cover - depends on target hardware
    SMBus = None


I2C_BUS: Final[int] = 1
DEVICE: Final[int] = 0x12

REG_VERSION: Final[int] = 0x00
REG_RAILS: Final[int] = 0x01
REG_SOLENOIDS: Final[int] = 0x02
REG_ALARMS: Final[int] = 0x03
REG_INA_PRESENCE: Final[int] = 0x04

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


@dataclass(slots=True)
class DeviceState:
    version: int = 0
    rails: int = 0
    solenoids: int = 0
    alarms: int = 0
    ina_presence: int = 0
    connected: bool = False
    error: str = ""
    backend: str = "disconnected"

    def to_payload(self) -> dict[str, object]:
        return {
            **asdict(self),
            "rails_map": {name: bool(self.rails & mask) for name, mask in RAILS.items()},
            "solenoids_map": {
                name: bool(self.solenoids & mask) for name, mask in SOLENOIDS.items()
            },
            "alarms_map": {
                name: bool(self.alarms & mask) for name, mask in ALARM_BITS.items()
            },
            "ina_presence_map": {
                name: bool(self.ina_presence & mask) for name, mask in INA_BITS.items()
            },
        }


class DeviceController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mock_mode = os.environ.get("OMB_MOCK_HARDWARE", "0") == "1"
        self._state = DeviceState(
            version=1 if self._mock_mode else 0,
            ina_presence=0b11 if self._mock_mode else 0,
            connected=self._mock_mode,
            backend="mock" if self._mock_mode else "i2c",
        )

    def _read_reg(self, bus: SMBus, reg: int) -> int:
        bus.write_byte(DEVICE, reg)
        return bus.read_byte(DEVICE)

    def _write_reg(self, bus: SMBus, reg: int, value: int) -> None:
        bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF])

    def _read_from_bus(self) -> DeviceState:
        if SMBus is None:
            return DeviceState(
                connected=False,
                error="smbus2 is not installed",
                backend="unavailable",
            )

        try:
            with SMBus(I2C_BUS) as bus:
                return DeviceState(
                    version=self._read_reg(bus, REG_VERSION),
                    rails=self._read_reg(bus, REG_RAILS),
                    solenoids=self._read_reg(bus, REG_SOLENOIDS),
                    alarms=self._read_reg(bus, REG_ALARMS),
                    ina_presence=self._read_reg(bus, REG_INA_PRESENCE),
                    connected=True,
                    error="",
                    backend="i2c",
                )
        except OSError as exc:
            return DeviceState(
                connected=False,
                error=str(exc),
                backend="i2c",
            )

    def read_state(self) -> DeviceState:
        with self._lock:
            if self._mock_mode:
                self._state.connected = True
                self._state.backend = "mock"
                return DeviceState(**asdict(self._state))

            self._state = self._read_from_bus()
            return DeviceState(**asdict(self._state))

    def _update_register(self, reg: int, value: int) -> DeviceState:
        with self._lock:
            if self._mock_mode:
                if reg == REG_RAILS:
                    self._state.rails = value & 0x0F
                elif reg == REG_SOLENOIDS:
                    self._state.solenoids = value & 0x0F
                self._state.connected = True
                self._state.error = ""
                return DeviceState(**asdict(self._state))

            if SMBus is None:
                self._state = DeviceState(
                    connected=False,
                    error="smbus2 is not installed",
                    backend="unavailable",
                )
                return DeviceState(**asdict(self._state))

            try:
                with SMBus(I2C_BUS) as bus:
                    self._write_reg(bus, reg, value)
                self._state = self._read_from_bus()
            except OSError as exc:
                self._state = DeviceState(
                    connected=False,
                    error=str(exc),
                    backend="i2c",
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

    def metadata(self) -> dict[str, object]:
        return {
            "rails": list(RAILS.keys()),
            "solenoids": list(SOLENOIDS.keys()),
            "alarms": list(ALARM_BITS.keys()),
            "ina_presence": list(INA_BITS.keys()),
            "mock_mode": self._mock_mode,
        }
