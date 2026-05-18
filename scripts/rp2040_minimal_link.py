#!/usr/bin/env python3
from __future__ import annotations

import curses
import time
from dataclasses import dataclass

from smbus2 import SMBus


I2C_BUS = 1
ADDRESS = 0x12
REG_RAILS = 0x01
REG_RELAYS = 0x02
REG_PIXEL_COMMAND = 0x40
REG_STATUS_SNAPSHOT = 0x60
STATUS_SNAPSHOT_LENGTH = 16
STATUS_MAGIC = 0xA5
STATUS_VERSION = 1
PIXEL_ANIMATION_STATIC = 0
PIXEL_ANIMATION_CANDLE = 1

RAILS = (
    ("1", "12V_A", 1 << 0),
    ("2", "12V_B", 1 << 1),
    ("3", "12V_C", 1 << 2),
    ("4", "8V", 1 << 3),
)

RELAYS = (
    ("q", "P0", 1 << 0),
    ("w", "P1", 1 << 1),
    ("e", "P2", 1 << 2),
    ("r", "P3", 1 << 3),
)

PIXEL_LINES = (
    ("a", "PIX_1", 1 << 0),
    ("s", "PIX_2", 1 << 1),
    ("d", "PIX_3", 1 << 2),
    ("f", "PIX_4", 1 << 3),
)


@dataclass
class ControlState:
    rail_mask: int = 0x0E
    relay_mask: int = 0x00
    servo_enable_mask: int = 0x00
    servo_values: list[int] | None = None
    pixel_active_mask: int = 0x00
    tca_ready: bool = False


def checksum(data: list[int]) -> int:
    return (-sum(data[:-1])) & 0xFF


def read_state(bus: SMBus) -> ControlState:
    data = bus.read_i2c_block_data(ADDRESS, REG_STATUS_SNAPSHOT, STATUS_SNAPSHOT_LENGTH)
    if len(data) != STATUS_SNAPSHOT_LENGTH:
        raise OSError(f"short status snapshot: expected {STATUS_SNAPSHOT_LENGTH}, got {len(data)}")
    if data[0] != STATUS_MAGIC:
        raise OSError(f"bad status magic: 0x{data[0]:02x}")
    if data[1] != STATUS_VERSION:
        raise OSError(f"bad status version: {data[1]}")
    if checksum(data) != data[-1]:
        raise OSError(f"bad status checksum: got 0x{data[-1]:02x}, expected 0x{checksum(data):02x}")
    return ControlState(
        rail_mask=data[2] & 0x0F,
        relay_mask=data[3] & 0x0F,
        servo_enable_mask=data[4],
        servo_values=list(data[5:13]),
        pixel_active_mask=data[13] & 0x0F,
        tca_ready=bool(data[14]),
    )


def push_state(bus: SMBus, state: ControlState) -> None:
    bus.write_byte_data(ADDRESS, REG_RAILS, state.rail_mask & 0x0F)
    bus.write_byte_data(ADDRESS, REG_RELAYS, state.relay_mask & 0x0F)


def write_pixel_command(
    bus: SMBus,
    *,
    line_mask: int,
    animation_id: int,
    base_rgb: tuple[int, int, int],
    param_rgb: tuple[int, int, int],
    duration_ms: int,
) -> None:
    command = [
        line_mask & 0x0F,
        0,  # start pixel
        0,  # count 0 means through the end of the line
        base_rgb[0] & 0xFF,
        base_rgb[1] & 0xFF,
        base_rgb[2] & 0xFF,
        param_rgb[0] & 0xFF,
        param_rgb[1] & 0xFF,
        param_rgb[2] & 0xFF,
        duration_ms & 0xFF,
        (duration_ms >> 8) & 0xFF,
        1,  # trigger
        animation_id & 0xFF,
    ]
    bus.write_i2c_block_data(ADDRESS, REG_PIXEL_COMMAND, command)


def draw(screen, state: ControlState, candle_mask: int, message: str) -> None:
    screen.erase()
    screen.addstr(0, 0, "Minimal Pi -> RP2040 control")
    screen.addstr(2, 0, "Rails: 1-4. Relays: Q/W/E/R. Candles: A/S/D/F. X fade selected off.")
    screen.addstr(3, 0, "Shift-S pulls Arduino state. P pushes. Esc quits.")
    screen.addstr(4, 0, f"target address: 0x{ADDRESS:02x} on /dev/i2c-{I2C_BUS}")
    screen.addstr(5, 0, f"rail mask: 0x{state.rail_mask:02x} / 0b{state.rail_mask:04b}")

    row = 7
    for key, name, mask in RAILS:
        label = "ON " if state.rail_mask & mask else "OFF"
        screen.addstr(row, 0, f"{key}: {name:<5} {label}")
        row += 1

    row += 1
    screen.addstr(row, 0, f"relay mask: 0x{state.relay_mask:02x} / 0b{state.relay_mask:04b}")
    row += 2
    for key, name, mask in RELAYS:
        label = "ON " if state.relay_mask & mask else "OFF"
        screen.addstr(row, 0, f"{key.upper()}: {name:<2} {label}")
        row += 1

    row += 1
    screen.addstr(row, 0, f"candle selection: 0x{candle_mask:02x} / 0b{candle_mask:04b}")
    row += 2
    for key, name, mask in PIXEL_LINES:
        label = "ON " if candle_mask & mask else "OFF"
        active = "active" if state.pixel_active_mask & mask else "idle"
        screen.addstr(row, 0, f"{key.upper()}: {name:<5} {label} ({active})")
        row += 1

    row += 1
    screen.addstr(
        row,
        0,
        f"arduino extras: servo_enable=0x{state.servo_enable_mask:02x} "
        f"pixel_active=0b{state.pixel_active_mask:04b} "
        f"tca9534={'ok' if state.tca_ready else 'not ready'}",
    )

    screen.addstr(row + 1, 0, message[: curses.COLS - 1])
    screen.refresh()


def main(screen) -> None:
    curses.noecho()
    curses.cbreak()
    screen.nodelay(True)
    screen.keypad(True)

    state = ControlState()
    candle_mask = 0x00
    message = "Ready."

    with SMBus(I2C_BUS) as bus:
        try:
            state = read_state(bus)
            candle_mask = state.pixel_active_mask
            message = "Pulled initial state from Arduino."
        except OSError as exc:
            message = f"Initial Arduino status read failed: {exc}"

        while True:
            draw(screen, state, candle_mask, message)
            key = screen.getch()

            if key == 27:
                break

            if key == ord("S"):
                try:
                    state = read_state(bus)
                    candle_mask = state.pixel_active_mask
                    message = "Pulled state from Arduino."
                except OSError as exc:
                    message = f"Arduino status read failed: {exc}"
                time.sleep(0.03)
                continue

            if key in (ord("p"), ord("P")):
                try:
                    push_state(bus, state)
                    message = "Pushed Python state to Arduino."
                except OSError as exc:
                    message = f"Arduino state push failed: {exc}"
                time.sleep(0.03)
                continue

            pixel_line = next(
                (item for item in PIXEL_LINES if key in (ord(item[0]), ord(item[0].upper()))),
                None,
            )
            if pixel_line is not None:
                _, name, mask = pixel_line
                turning_on = (candle_mask & mask) == 0
                try:
                    write_pixel_command(
                        bus,
                        line_mask=mask,
                        animation_id=PIXEL_ANIMATION_CANDLE,
                        base_rgb=(255, 96, 18),
                        param_rgb=(32, 17, 230 if turning_on else 0),
                        duration_ms=1000,
                    )
                    candle_mask = (candle_mask | mask) if turning_on else (candle_mask & ~mask)
                    time.sleep(0.02)
                    state = read_state(bus)
                    label = "on" if turning_on else "off"
                    message = f"Fading candle {name} {label}."
                except OSError as exc:
                    message = f"Pixel candle toggle failed: {exc}"
                time.sleep(0.03)
                continue

            if key in (ord("x"), ord("X")):
                target_mask = candle_mask or state.pixel_active_mask
                if target_mask == 0:
                    message = "No selected candles to fade off."
                    time.sleep(0.03)
                    continue
                try:
                    write_pixel_command(
                        bus,
                        line_mask=target_mask,
                        animation_id=PIXEL_ANIMATION_CANDLE,
                        base_rgb=(255, 96, 18),
                        param_rgb=(32, 17, 0),
                        duration_ms=1000,
                    )
                    candle_mask &= ~target_mask
                    time.sleep(0.02)
                    state = read_state(bus)
                    message = f"Fading selected candles off, mask 0x{target_mask:02x}."
                except OSError as exc:
                    message = f"Pixel fade-off command failed: {exc}"
                time.sleep(0.03)
                continue

            rail = next((item for item in RAILS if key == ord(item[0])), None)
            relay = next(
                (item for item in RELAYS if key in (ord(item[0]), ord(item[0].upper()))),
                None,
            )
            if rail is None and relay is None:
                time.sleep(0.03)
                continue

            try:
                if rail is not None:
                    _, name, mask = rail
                    next_rail_mask = state.rail_mask ^ mask
                    bus.write_byte_data(ADDRESS, REG_RAILS, next_rail_mask)
                    state.rail_mask = next_rail_mask
                    label = "on" if state.rail_mask & mask else "off"
                    message = f"write ok: toggled {name} {label}, sent rail mask 0x{state.rail_mask:02x}"
                elif relay is not None:
                    _, name, mask = relay
                    next_relay_mask = state.relay_mask ^ mask
                    bus.write_byte_data(ADDRESS, REG_RELAYS, next_relay_mask)
                    state.relay_mask = next_relay_mask
                    label = "on" if state.relay_mask & mask else "off"
                    message = f"write ok: toggled {name} {label}, sent relay mask 0x{state.relay_mask:02x}"
            except OSError as exc:
                message = (
                    f"I2C error: {exc}. Check RP2040 firmware, address 0x{ADDRESS:02x}, "
                    "GP14/GP15 wiring, pullups, and common ground."
                )

            time.sleep(0.03)


if __name__ == "__main__":
    curses.wrapper(main)
