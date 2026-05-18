#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from smbus2 import SMBus


I2C_BUS = 1
DEFAULT_ADDRESSES = (0x23, 0x5C)

POWER_ON = 0x01
RESET = 0x07
ONE_TIME_HIGH_RES_MODE = 0x20


def read_lux(bus: SMBus, address: int) -> tuple[int, int, float]:
    bus.write_byte(address, POWER_ON)
    bus.write_byte(address, RESET)
    bus.write_byte(address, ONE_TIME_HIGH_RES_MODE)
    time.sleep(0.18)

    msb, lsb = bus.read_i2c_block_data(address, ONE_TIME_HIGH_RES_MODE, 2)
    raw = (msb << 8) | lsb
    lux = raw / 1.2
    return raw, (msb << 8) | lsb, lux


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a BH1750 light sensor on Raspberry Pi I2C.")
    parser.add_argument("--bus", type=int, default=I2C_BUS, help="I2C bus number, default 1")
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        help="BH1750 address, usually 0x23 or 0x5c. If omitted, both are tried.",
    )
    parser.add_argument("--watch", action="store_true", help="Read continuously once per second")
    args = parser.parse_args()

    addresses = (args.address,) if args.address is not None else DEFAULT_ADDRESSES

    with SMBus(args.bus) as bus:
        while True:
            found = False
            for address in addresses:
                try:
                    raw, _, lux = read_lux(bus, address)
                except OSError as exc:
                    print(f"0x{address:02x}: read failed: {exc}")
                    continue
                found = True
                print(f"0x{address:02x}: raw={raw} lux={lux:.2f}")

            if not args.watch:
                if not found:
                    raise SystemExit("No BH1750 responded. Check address, wiring, and pullups.")
                return

            time.sleep(1)


if __name__ == "__main__":
    main()
