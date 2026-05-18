#!/usr/bin/env python3
from __future__ import annotations

import curses
import time

from smbus2 import SMBus


I2C_BUS = 1
ADDRESS = 0x12


def draw(screen, value: int, message: str) -> None:
    screen.erase()
    screen.addstr(0, 0, "Minimal Pi -> RP2040 I2C test")
    screen.addstr(2, 0, "Press 1 to add 5, 0 to subtract 5, q to quit.")
    screen.addstr(4, 0, f"target address: 0x{ADDRESS:02x} on /dev/i2c-{I2C_BUS}")
    screen.addstr(5, 0, f"value: {value}")
    screen.addstr(7, 0, message[: curses.COLS - 1])
    screen.refresh()


def main(screen) -> None:
    curses.noecho()
    curses.cbreak()
    screen.nodelay(True)
    screen.keypad(True)

    value = 0
    message = "Ready."

    with SMBus(I2C_BUS) as bus:
        while True:
            draw(screen, value, message)
            key = screen.getch()

            if key in (ord("q"), ord("Q")):
                break

            next_value = value
            if key == ord("1"):
                next_value = min(250, value + 5)
            elif key == ord("0"):
                next_value = max(0, value - 5)
            else:
                time.sleep(0.03)
                continue

            try:
                bus.write_byte(ADDRESS, next_value)
                echoed = bus.read_byte(ADDRESS)
                value = next_value
                message = f"write ok: sent={value}, rp2040_echo={echoed}"
            except OSError as exc:
                message = (
                    f"I2C error: {exc}. Check RP2040 firmware, address 0x{ADDRESS:02x}, "
                    "GP14/GP15 wiring, pullups, and common ground."
                )

            time.sleep(0.03)


if __name__ == "__main__":
    curses.wrapper(main)
