#!/usr/bin/env python3

"""
Interactive keyboard controller for the OneManBand_2040 RP2040 board.

This script is intended to run on a Raspberry Pi, stay alive in the terminal,
and let you toggle rails and solenoids with single key presses.

Dependencies:
    pip install smbus2
"""

from __future__ import annotations

import select
import sys
import termios
import time
import tty
from dataclasses import dataclass

from smbus2 import SMBus


I2C_BUS = 1
DEVICE = 0x12

REG_VERSION = 0x00
REG_RAILS = 0x01
REG_SOLENOIDS = 0x02
REG_ALARMS = 0x03
REG_INA_PRESENCE = 0x04

RAIL_12V_A = 1 << 0
RAIL_12V_B = 1 << 1
RAIL_12V_C = 1 << 2
RAIL_8V = 1 << 3

SOL_P0 = 1 << 0
SOL_P1 = 1 << 1
SOL_P2 = 1 << 2
SOL_P3 = 1 << 3

REFRESH_INTERVAL_S = 0.25


@dataclass
class DeviceState:
    version: int = 0
    rails: int = 0
    solenoids: int = 0
    alarms: int = 0
    ina_presence: int = 0
    connected: bool = False
    error: str = ""


def read_reg(bus: SMBus, reg: int) -> int:
    bus.write_byte(DEVICE, reg)
    return bus.read_byte(DEVICE)


def write_reg(bus: SMBus, reg: int, value: int) -> None:
    bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF])


def bit_label(value: int, mask: int) -> str:
    return "ON " if value & mask else "OFF"


def alarm_label(value: int, mask: int) -> str:
    return "HIGH" if value & mask else "LOW "


def read_state(bus: SMBus) -> DeviceState:
    try:
        return DeviceState(
            version=read_reg(bus, REG_VERSION),
            rails=read_reg(bus, REG_RAILS),
            solenoids=read_reg(bus, REG_SOLENOIDS),
            alarms=read_reg(bus, REG_ALARMS),
            ina_presence=read_reg(bus, REG_INA_PRESENCE),
            connected=True,
            error="",
        )
    except OSError as exc:
        return DeviceState(connected=False, error=str(exc))


def toggle_mask(bus: SMBus, reg: int, current_value: int, mask: int) -> int:
    new_value = current_value ^ mask
    write_reg(bus, reg, new_value)
    return new_value


def set_solenoids(bus: SMBus, value: int) -> int:
    write_reg(bus, REG_SOLENOIDS, value & 0x0F)
    return value & 0x0F


def clear_screen() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def render(state: DeviceState) -> None:
    clear_screen()
    print("OneManBand_2040 Keyboard Control")
    print()

    if not state.connected:
      print("Device status: DISCONNECTED")
      print(f"Last error: {state.error}")
    else:
      print(f"Device status: CONNECTED, protocol v{state.version}")
      print()
      print("Rails")
      print(f"  [1] 12V_A : {bit_label(state.rails, RAIL_12V_A)}")
      print(f"  [2] 12V_B : {bit_label(state.rails, RAIL_12V_B)}")
      print(f"  [3] 12V_C : {bit_label(state.rails, RAIL_12V_C)}")
      print(f"  [4] 8V    : {bit_label(state.rails, RAIL_8V)}")
      print()
      print("Solenoids")
      print(f"  [q] P0    : {bit_label(state.solenoids, SOL_P0)}")
      print(f"  [w] P1    : {bit_label(state.solenoids, SOL_P1)}")
      print(f"  [e] P2    : {bit_label(state.solenoids, SOL_P2)}")
      print(f"  [r] P3    : {bit_label(state.solenoids, SOL_P3)}")
      print()
      print("Alarms")
      print(f"  12V_C_ALARM : {alarm_label(state.alarms, 1 << 0)}")
      print(f"  8V_ALARM    : {alarm_label(state.alarms, 1 << 1)}")
      print()
      print("INA260 Presence")
      print(f"  12V_C : {bit_label(state.ina_presence, 1 << 0)}")
      print(f"  8V    : {bit_label(state.ina_presence, 1 << 1)}")

    print()
    print("Keys")
    print("  1/2/3/4 : toggle 12V_A / 12V_B / 12V_C / 8V")
    print("  q/w/e/r : toggle solenoids P0 / P1 / P2 / P3")
    print("  0       : all rails off")
    print("  -       : all solenoids off")
    print("  s       : refresh status now")
    print("  x       : exit")
    print()
    print("This screen auto-refreshes while the script is running.")


def read_key_nonblocking(timeout_s: float) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout_s)
    if not ready:
        return None
    return sys.stdin.read(1)


def main() -> int:
    with SMBus(I2C_BUS) as bus:
        original_term = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())

        state = read_state(bus)
        render(state)
        last_refresh = time.monotonic()

        try:
            while True:
                key = read_key_nonblocking(0.05)

                if key is not None:
                    if key == "x":
                        break
                    if state.connected:
                        if key == "1":
                            state.rails = toggle_mask(bus, REG_RAILS, state.rails, RAIL_12V_A)
                        elif key == "2":
                            state.rails = toggle_mask(bus, REG_RAILS, state.rails, RAIL_12V_B)
                        elif key == "3":
                            state.rails = toggle_mask(bus, REG_RAILS, state.rails, RAIL_12V_C)
                        elif key == "4":
                            state.rails = toggle_mask(bus, REG_RAILS, state.rails, RAIL_8V)
                        elif key == "q":
                            state.solenoids = toggle_mask(
                                bus, REG_SOLENOIDS, state.solenoids, SOL_P0
                            )
                        elif key == "w":
                            state.solenoids = toggle_mask(
                                bus, REG_SOLENOIDS, state.solenoids, SOL_P1
                            )
                        elif key == "e":
                            state.solenoids = toggle_mask(
                                bus, REG_SOLENOIDS, state.solenoids, SOL_P2
                            )
                        elif key == "r":
                            state.solenoids = toggle_mask(
                                bus, REG_SOLENOIDS, state.solenoids, SOL_P3
                            )
                        elif key == "0":
                            state.rails = 0
                            write_reg(bus, REG_RAILS, 0)
                        elif key == "-":
                            state.solenoids = set_solenoids(bus, 0)

                    if key == "s":
                        state = read_state(bus)
                        render(state)
                        last_refresh = time.monotonic()
                        continue

                    state = read_state(bus)
                    render(state)
                    last_refresh = time.monotonic()
                    continue

                now = time.monotonic()
                if now - last_refresh >= REFRESH_INTERVAL_S:
                    state = read_state(bus)
                    render(state)
                    last_refresh = now
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_term)
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
