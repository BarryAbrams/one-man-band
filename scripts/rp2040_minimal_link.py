#!/usr/bin/env python3
from __future__ import annotations

import curses
import time

from smbus2 import SMBus


I2C_BUS = 1
ADDRESS = 0x12
REG_RAILS = 0x01
REG_RELAYS = 0x02

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


def draw(screen, rail_mask: int, relay_mask: int, message: str) -> None:
    screen.erase()
    screen.addstr(0, 0, "Minimal Pi -> RP2040 control")
    screen.addstr(2, 0, "Rails: 1, 2, 3, 4. Relays: Q, W, E, R. Press Esc to quit.")
    screen.addstr(4, 0, f"target address: 0x{ADDRESS:02x} on /dev/i2c-{I2C_BUS}")
    screen.addstr(5, 0, f"rail mask: 0x{rail_mask:02x} / 0b{rail_mask:04b}")

    row = 7
    for key, name, mask in RAILS:
        state = "ON " if rail_mask & mask else "OFF"
        screen.addstr(row, 0, f"{key}: {name:<5} {state}")
        row += 1

    row += 1
    screen.addstr(row, 0, f"relay mask: 0x{relay_mask:02x} / 0b{relay_mask:04b}")
    row += 2
    for key, name, mask in RELAYS:
        state = "ON " if relay_mask & mask else "OFF"
        screen.addstr(row, 0, f"{key.upper()}: {name:<2} {state}")
        row += 1

    screen.addstr(row + 1, 0, message[: curses.COLS - 1])
    screen.refresh()


def main(screen) -> None:
    curses.noecho()
    curses.cbreak()
    screen.nodelay(True)
    screen.keypad(True)

    rail_mask = 0x0E
    relay_mask = 0x00
    message = "Ready."

    with SMBus(I2C_BUS) as bus:
        while True:
            draw(screen, rail_mask, relay_mask, message)
            key = screen.getch()

            if key == 27:
                break

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
                    next_rail_mask = rail_mask ^ mask
                    bus.write_byte_data(ADDRESS, REG_RAILS, next_rail_mask)
                    rail_mask = next_rail_mask
                    state = "on" if rail_mask & mask else "off"
                    message = f"write ok: toggled {name} {state}, sent rail mask 0x{rail_mask:02x}"
                elif relay is not None:
                    _, name, mask = relay
                    next_relay_mask = relay_mask ^ mask
                    bus.write_byte_data(ADDRESS, REG_RELAYS, next_relay_mask)
                    relay_mask = next_relay_mask
                    state = "on" if relay_mask & mask else "off"
                    message = f"write ok: toggled {name} {state}, sent relay mask 0x{relay_mask:02x}"
            except OSError as exc:
                message = (
                    f"I2C error: {exc}. Check RP2040 firmware, address 0x{ADDRESS:02x}, "
                    "GP14/GP15 wiring, pullups, and common ground."
                )

            time.sleep(0.03)


if __name__ == "__main__":
    curses.wrapper(main)
