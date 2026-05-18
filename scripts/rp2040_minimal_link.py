#!/usr/bin/env python3
from __future__ import annotations

import curses
import time

from smbus2 import SMBus


I2C_BUS = 1
ADDRESS = 0x12
REG_RAILS = 0x01

RAILS = (
    ("1", "12V_A", 1 << 0),
    ("2", "12V_B", 1 << 1),
    ("3", "12V_C", 1 << 2),
    ("4", "8V", 1 << 3),
)


def draw(screen, rail_mask: int, message: str) -> None:
    screen.erase()
    screen.addstr(0, 0, "Minimal Pi -> RP2040 rail control")
    screen.addstr(2, 0, "Press 1, 2, 3, or 4 to toggle rails. Press q to quit.")
    screen.addstr(4, 0, f"target address: 0x{ADDRESS:02x} on /dev/i2c-{I2C_BUS}")
    screen.addstr(5, 0, f"rail mask: 0x{rail_mask:02x} / 0b{rail_mask:04b}")

    row = 7
    for key, name, mask in RAILS:
        state = "ON " if rail_mask & mask else "OFF"
        screen.addstr(row, 0, f"{key}: {name:<5} {state}")
        row += 1

    screen.addstr(row + 1, 0, message[: curses.COLS - 1])
    screen.refresh()


def main(screen) -> None:
    curses.noecho()
    curses.cbreak()
    screen.nodelay(True)
    screen.keypad(True)

    rail_mask = 0x0E
    message = "Ready."

    with SMBus(I2C_BUS) as bus:
        while True:
            draw(screen, rail_mask, message)
            key = screen.getch()

            if key in (ord("q"), ord("Q")):
                break

            rail = next((item for item in RAILS if key == ord(item[0])), None)
            if rail is None:
                time.sleep(0.03)
                continue

            _, name, mask = rail
            next_rail_mask = rail_mask ^ mask

            try:
                bus.write_byte_data(ADDRESS, REG_RAILS, next_rail_mask)
                rail_mask = next_rail_mask
                state = "on" if rail_mask & mask else "off"
                message = f"write ok: toggled {name} {state}, sent rail mask 0x{rail_mask:02x}"
            except OSError as exc:
                message = (
                    f"I2C error: {exc}. Check RP2040 firmware, address 0x{ADDRESS:02x}, "
                    "GP14/GP15 wiring, pullups, and common ground."
                )

            time.sleep(0.03)


if __name__ == "__main__":
    curses.wrapper(main)
