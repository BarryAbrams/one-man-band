#!/usr/bin/env python3

"""
Example Raspberry Pi controller for the OneManBand_2040 RP2040 board.

This script talks to the RP2040 over I2C using the register protocol
documented in README.md.

Tested target:
- Raspberry Pi Zero 2 W
- Linux I2C bus 1

Python dependencies:
- smbus2

Install:
    sudo apt install python3-pip i2c-tools
    pip install smbus2
"""

from __future__ import annotations

import argparse
import sys
import time

from smbus2 import SMBus


I2C_BUS = 1
DEVICE = 0x12

REG_VERSION = 0x00
REG_RAILS = 0x01
REG_SOLENOIDS = 0x02
REG_ALARMS = 0x03
REG_INA_PRESENCE = 0x04
REG_SERVO_ENABLE_MASK = 0x05
REG_SERVO0 = 0x10
REG_INA0_MV_L = 0x20
REG_INA0_MV_H = 0x21
REG_INA0_MA_L = 0x22
REG_INA0_MA_H = 0x23
REG_INA1_MV_L = 0x24
REG_INA1_MV_H = 0x25
REG_INA1_MA_L = 0x26
REG_INA1_MA_H = 0x27
REG_PIXEL_COMMAND = 0x40

RAIL_12V_A = 1 << 0
RAIL_12V_B = 1 << 1
RAIL_12V_C = 1 << 2
RAIL_8V = 1 << 3

SOL_P0 = 1 << 0
SOL_P1 = 1 << 1
SOL_P2 = 1 << 2
SOL_P3 = 1 << 3

SERVO_0 = 1 << 0
SERVO_1 = 1 << 1
SERVO_2 = 1 << 2
SERVO_3 = 1 << 3
SERVO_4 = 1 << 4
SERVO_5 = 1 << 5
SERVO_6 = 1 << 6
SERVO_7 = 1 << 7

PIX_1 = 1 << 0
PIX_2 = 1 << 1
PIX_3 = 1 << 2
PIX_4 = 1 << 3


def read_reg(bus: SMBus, reg: int) -> int:
    bus.write_byte(DEVICE, reg)
    return bus.read_byte(DEVICE)


def write_reg(bus: SMBus, reg: int, value: int) -> None:
    bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF])


def read_u16_le(bus: SMBus, reg_low: int) -> int:
    low = read_reg(bus, reg_low)
    high = read_reg(bus, reg_low + 1)
    return low | (high << 8)


def read_i16_le(bus: SMBus, reg_low: int) -> int:
    value = read_u16_le(bus, reg_low)
    if value & 0x8000:
        value -= 0x10000
    return value


def write_pixel_animation(
    bus: SMBus,
    rail_mask: int,
    start: int,
    count: int,
    from_rgb: tuple[int, int, int],
    to_rgb: tuple[int, int, int],
    duration_ms: int,
    animation_id: int = 0,
) -> None:
    duration_ms = max(0, min(duration_ms, 65535))
    payload = [
        rail_mask & 0x0F,
        start & 0xFF,
        count & 0xFF,
        from_rgb[0] & 0xFF,
        from_rgb[1] & 0xFF,
        from_rgb[2] & 0xFF,
        to_rgb[0] & 0xFF,
        to_rgb[1] & 0xFF,
        to_rgb[2] & 0xFF,
        duration_ms & 0xFF,
        (duration_ms >> 8) & 0xFF,
        1,
        animation_id & 0xFF,
    ]
    bus.write_i2c_block_data(DEVICE, REG_PIXEL_COMMAND, payload)


def decode_rails(value: int) -> dict[str, bool]:
    return {
        "12V_A": bool(value & RAIL_12V_A),
        "12V_B": bool(value & RAIL_12V_B),
        "12V_C": bool(value & RAIL_12V_C),
        "8V": bool(value & RAIL_8V),
    }


def decode_solenoids(value: int) -> dict[str, bool]:
    return {
        "P0": bool(value & SOL_P0),
        "P1": bool(value & SOL_P1),
        "P2": bool(value & SOL_P2),
        "P3": bool(value & SOL_P3),
    }


def decode_alarms(value: int) -> dict[str, bool]:
    return {
        "12V_C_ALARM": bool(value & (1 << 0)),
        "8V_ALARM": bool(value & (1 << 1)),
    }


def decode_ina_presence(value: int) -> dict[str, bool]:
    return {
        "12V_C": bool(value & (1 << 0)),
        "8V": bool(value & (1 << 1)),
    }


def format_flags(flags: dict[str, bool]) -> str:
    parts = [f"{name}={'on' if state else 'off'}" for name, state in flags.items()]
    return ", ".join(parts)


def print_status(bus: SMBus) -> None:
    version = read_reg(bus, REG_VERSION)
    rails = read_reg(bus, REG_RAILS)
    solenoids = read_reg(bus, REG_SOLENOIDS)
    alarms = read_reg(bus, REG_ALARMS)
    ina = read_reg(bus, REG_INA_PRESENCE)
    ina0_mv = read_u16_le(bus, REG_INA0_MV_L)
    ina0_ma = read_i16_le(bus, REG_INA0_MA_L)
    ina1_mv = read_u16_le(bus, REG_INA1_MV_L)
    ina1_ma = read_i16_le(bus, REG_INA1_MA_L)

    print(f"Protocol version: {version}")
    print(f"Rails:      0x{rails:02X}  {format_flags(decode_rails(rails))}")
    print(
        f"Solenoids:  0x{solenoids:02X}  "
        f"{format_flags(decode_solenoids(solenoids))}"
    )
    print(f"Alarms:     0x{alarms:02X}  {format_flags(decode_alarms(alarms))}")
    print(f"INA260s:    0x{ina:02X}  {format_flags(decode_ina_presence(ina))}")
    print(f"12V_C INA:  {ina0_mv / 1000.0:.3f} V, {ina0_ma} mA")
    print(f"8V INA:     {ina1_mv / 1000.0:.3f} V, {ina1_ma} mA")


def build_rail_mask(args: argparse.Namespace) -> int:
    mask = 0
    if args.a:
      mask |= RAIL_12V_A
    if args.b:
      mask |= RAIL_12V_B
    if args.c:
      mask |= RAIL_12V_C
    if args.eight:
      mask |= RAIL_8V
    return mask


def build_solenoid_mask(args: argparse.Namespace) -> int:
    mask = 0
    if args.p0:
      mask |= SOL_P0
    if args.p1:
      mask |= SOL_P1
    if args.p2:
      mask |= SOL_P2
    if args.p3:
      mask |= SOL_P3
    return mask


def handle_status(_: argparse.Namespace) -> int:
    with SMBus(I2C_BUS) as bus:
        print_status(bus)
    return 0


def handle_set_rails(args: argparse.Namespace) -> int:
    rail_mask = build_rail_mask(args)
    with SMBus(I2C_BUS) as bus:
        write_reg(bus, REG_RAILS, rail_mask)
        time.sleep(0.1)
        applied = read_reg(bus, REG_RAILS)
    print(f"Wrote rail state 0x{rail_mask:02X}, applied 0x{applied:02X}")
    print(format_flags(decode_rails(applied)))
    return 0


def handle_set_solenoids(args: argparse.Namespace) -> int:
    solenoid_mask = build_solenoid_mask(args)
    with SMBus(I2C_BUS) as bus:
        write_reg(bus, REG_SOLENOIDS, solenoid_mask)
        time.sleep(0.1)
        applied = read_reg(bus, REG_SOLENOIDS)
    print(f"Wrote solenoid state 0x{solenoid_mask:02X}, applied 0x{applied:02X}")
    print(format_flags(decode_solenoids(applied)))
    return 0


def handle_pulse(args: argparse.Namespace) -> int:
    solenoid_mask = build_solenoid_mask(args)
    if solenoid_mask == 0:
        print("No solenoids selected for pulse.", file=sys.stderr)
        return 1

    with SMBus(I2C_BUS) as bus:
        write_reg(bus, REG_SOLENOIDS, solenoid_mask)
        time.sleep(args.duration)
        write_reg(bus, REG_SOLENOIDS, 0x00)
        time.sleep(0.1)
        applied = read_reg(bus, REG_SOLENOIDS)

    print(
        f"Pulsed solenoids 0x{solenoid_mask:02X} for {args.duration:.2f}s, "
        f"final state 0x{applied:02X}"
    )
    return 0


def servo_mask_from_channels(channels: list[int]) -> int:
    mask = 0
    for channel in channels:
        if channel < 0 or channel > 7:
            raise ValueError(f"Servo channel out of range: {channel}")
        mask |= 1 << channel
    return mask


def handle_set_servos(args: argparse.Namespace) -> int:
    if not args.channels:
        print("No servo channels selected.", file=sys.stderr)
        return 1

    if args.value < 0 or args.value > 255:
        print("Servo value must be between 0 and 255.", file=sys.stderr)
        return 1

    try:
        enable_mask = servo_mask_from_channels(args.channels)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    with SMBus(I2C_BUS) as bus:
        write_reg(bus, REG_SERVO_ENABLE_MASK, enable_mask)
        for channel in args.channels:
            write_reg(bus, REG_SERVO0 + channel, args.value)
        time.sleep(0.1)
        applied_mask = read_reg(bus, REG_SERVO_ENABLE_MASK)

    channel_list = ", ".join(str(channel) for channel in args.channels)
    print(
        f"Enabled servos [{channel_list}] with value {args.value}, "
        f"applied mask 0x{applied_mask:02X}"
    )
    return 0


def rgb_arg(value: str) -> tuple[int, int, int]:
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("RGB must be in R,G,B form")

    try:
        red, green, blue = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("RGB values must be integers") from exc

    for channel in (red, green, blue):
        if channel < 0 or channel > 255:
            raise argparse.ArgumentTypeError("RGB values must be 0-255")

    return red, green, blue


def pixel_rail_mask_from_args(rails: list[int]) -> int:
    mask = 0
    for rail in rails:
        if rail < 1 or rail > 4:
            raise ValueError(f"Pixel rail out of range: {rail}")
        mask |= 1 << (rail - 1)
    return mask


def handle_animate_pixels(args: argparse.Namespace) -> int:
    try:
        rail_mask = pixel_rail_mask_from_args(args.rails)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.start < 0 or args.start > 99:
        print("Start pixel must be 0-99.", file=sys.stderr)
        return 1
    if args.count < 0 or args.count > 100:
        print("Count must be 0-100. Use 0 for through end of rail.", file=sys.stderr)
        return 1
    if args.duration < 0 or args.duration > 65535:
        print("Duration must be 0-65535 milliseconds.", file=sys.stderr)
        return 1

    with SMBus(I2C_BUS) as bus:
        write_pixel_animation(
            bus,
            rail_mask=rail_mask,
            start=args.start,
            count=args.count,
            from_rgb=args.from_rgb,
            to_rgb=args.to_rgb,
            duration_ms=args.duration,
        )

    print(
        f"Started pixel animation rails=0x{rail_mask:02X} start={args.start} "
        f"count={args.count} from={args.from_rgb} to={args.to_rgb} "
        f"duration={args.duration}ms"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control the OneManBand_2040 RP2040 from a Raspberry Pi."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Read and print device status")
    status.set_defaults(func=handle_status)

    set_rails = subparsers.add_parser(
        "set-rails",
        help="Set the rail enable mask explicitly",
    )
    set_rails.add_argument("--a", action="store_true", help="Enable 12V_A")
    set_rails.add_argument("--b", action="store_true", help="Enable 12V_B")
    set_rails.add_argument("--c", action="store_true", help="Enable 12V_C")
    set_rails.add_argument("--eight", action="store_true", help="Enable 8V")
    set_rails.set_defaults(func=handle_set_rails)

    set_solenoids = subparsers.add_parser(
        "set-solenoids",
        help="Set the solenoid output mask explicitly",
    )
    set_solenoids.add_argument("--p0", action="store_true", help="Enable P0")
    set_solenoids.add_argument("--p1", action="store_true", help="Enable P1")
    set_solenoids.add_argument("--p2", action="store_true", help="Enable P2")
    set_solenoids.add_argument("--p3", action="store_true", help="Enable P3")
    set_solenoids.set_defaults(func=handle_set_solenoids)

    pulse = subparsers.add_parser(
        "pulse",
        help="Pulse one or more solenoids for a short duration",
    )
    pulse.add_argument("--p0", action="store_true", help="Pulse P0")
    pulse.add_argument("--p1", action="store_true", help="Pulse P1")
    pulse.add_argument("--p2", action="store_true", help="Pulse P2")
    pulse.add_argument("--p3", action="store_true", help="Pulse P3")
    pulse.add_argument(
        "--duration",
        type=float,
        default=0.25,
        help="Pulse duration in seconds, default 0.25",
    )
    pulse.set_defaults(func=handle_pulse)

    set_servos = subparsers.add_parser(
        "set-servos",
        help="Enable one or more servo channels and set them to a byte value",
    )
    set_servos.add_argument(
        "--channels",
        type=int,
        nargs="+",
        required=True,
        help="Servo channels to update, valid range 0-7",
    )
    set_servos.add_argument(
        "--value",
        type=int,
        required=True,
        help="Servo value from 0 to 255",
    )
    set_servos.set_defaults(func=handle_set_servos)

    animate_pixels = subparsers.add_parser(
        "animate-pixels",
        help="Request a smooth color transition on one or more pixel rails",
    )
    animate_pixels.add_argument(
        "--rails",
        type=int,
        nargs="+",
        required=True,
        help="Pixel rails to target, valid range 1-4",
    )
    animate_pixels.add_argument(
        "--start",
        type=int,
        default=0,
        help="Start pixel index, default 0",
    )
    animate_pixels.add_argument(
        "--count",
        type=int,
        default=0,
        help="Pixel count, default 0 means through end of rail",
    )
    animate_pixels.add_argument(
        "--from-rgb",
        type=rgb_arg,
        required=True,
        help="Start color as R,G,B",
    )
    animate_pixels.add_argument(
        "--to-rgb",
        type=rgb_arg,
        required=True,
        help="End color as R,G,B",
    )
    animate_pixels.add_argument(
        "--duration",
        type=int,
        default=1000,
        help="Animation duration in milliseconds, default 1000",
    )
    animate_pixels.set_defaults(func=handle_animate_pixels)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
