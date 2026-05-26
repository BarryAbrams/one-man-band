#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from smbus2 import SMBus


I2C_BUS = 1
TINYRFID_ADDRESS = 0x13
STATUS_REGISTER = 0x00
STATUS_LENGTH = 16
STATUS_MAGIC = 0xA7
STATUS_VERSION = 1
FLAG_TAG_PRESENT = 1 << 0


def xor_checksum(data: list[int]) -> int:
    checksum = 0
    for value in data[:-1]:
        checksum ^= value
    return checksum & 0xFF


def read_status(bus: SMBus, address: int) -> dict[str, object]:
    data = bus.read_i2c_block_data(address, STATUS_REGISTER, STATUS_LENGTH)
    if len(data) != STATUS_LENGTH:
        raise OSError(f"short TinyRFID status: expected {STATUS_LENGTH}, got {len(data)}")
    if data[0] != STATUS_MAGIC:
        raise OSError(f"bad TinyRFID magic: 0x{data[0]:02x}")
    if data[1] != STATUS_VERSION:
        raise OSError(f"bad TinyRFID version: {data[1]}")
    expected = xor_checksum(data)
    if data[-1] != expected:
        raise OSError(f"bad TinyRFID checksum: got 0x{data[-1]:02x}, expected 0x{expected:02x}")

    uid = data[5:13]
    return {
        "tag_present": bool(data[2] & FLAG_TAG_PRESENT),
        "scan_count": data[3] | (data[4] << 8),
        "uid": uid,
        "raw": data,
    }


def format_uid(uid: list[int]) -> str:
    return ":".join(f"{value:02X}" for value in uid)


def print_status(status: dict[str, object]) -> None:
    present = "yes" if status["tag_present"] else "no"
    uid = format_uid(status["uid"])  # type: ignore[arg-type]
    raw = " ".join(f"{value:02X}" for value in status["raw"])  # type: ignore[index]
    print(f"tag={present} scans={status['scan_count']} uid={uid} raw={raw}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read TinyRFID status over Raspberry Pi I2C.")
    parser.add_argument("--bus", type=int, default=I2C_BUS)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=TINYRFID_ADDRESS)
    parser.add_argument("--watch", action="store_true", help="poll continuously")
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    with SMBus(args.bus) as bus:
        while True:
            try:
                print_status(read_status(bus, args.address))
            except OSError as exc:
                print(f"TinyRFID read failed: {exc}")
            if not args.watch:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
