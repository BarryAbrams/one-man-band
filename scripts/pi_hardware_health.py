#!/usr/bin/env python3
from __future__ import annotations

import grp
import importlib.util
import os
import platform
import pwd
import subprocess
import sys
import time
from pathlib import Path


I2C_BUS = 1
RP2040_ADDRESS = 0x12
BH1750_ADDRESSES = (0x23, 0x5C)
BH1750_POWER_ON = 0x01
BH1750_RESET = 0x07
BH1750_ONE_TIME_HIGH_RES_MODE = 0x20
GPIO_PINS = {
    "1": 13,
    "2": 6,
    "3": 5,
    "4": 22,
    "5": 27,
    "6": 17,
}


def status_line(ok: bool | None, label: str, detail: str) -> None:
    if ok is True:
        prefix = "OK"
    elif ok is False:
        prefix = "FAIL"
    else:
        prefix = "INFO"
    print(f"[{prefix}] {label}: {detail}")


def command_output(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (127, f"{args[0]} not found")
    except subprocess.TimeoutExpired:
        return (124, "timed out")
    return (result.returncode, result.stdout.strip())


def current_groups() -> set[str]:
    groups = {grp.getgrgid(group_id).gr_name for group_id in os.getgroups()}
    try:
        groups.add(grp.getgrgid(os.getgid()).gr_name)
    except KeyError:
        pass
    return groups


def check_python_package(module: str, package_hint: str) -> None:
    spec = importlib.util.find_spec(module)
    if spec is None:
        status_line(False, f"python import {module}", f"missing; install {package_hint} in this venv")
    else:
        status_line(True, f"python import {module}", str(spec.origin or "available"))


def check_device_node(path: str, group_name: str) -> None:
    node = Path(path)
    if not node.exists():
        status_line(False, path, "missing")
        return

    stat = node.stat()
    try:
        owner = pwd.getpwuid(stat.st_uid).pw_name
    except KeyError:
        owner = str(stat.st_uid)
    try:
        group = grp.getgrgid(stat.st_gid).gr_name
    except KeyError:
        group = str(stat.st_gid)

    groups = current_groups()
    access = os.access(node, os.R_OK | os.W_OK)
    detail = f"owner={owner} group={group} mode={stat.st_mode & 0o777:o}"
    if group_name not in groups:
        detail += f"; current user is not in {group_name}"
    status_line(access, path, detail)


def check_i2c() -> None:
    check_device_node(f"/dev/i2c-{I2C_BUS}", "i2c")
    check_python_package("smbus2", "smbus2")

    try:
        from smbus2 import SMBus
    except ImportError:
        return

    try:
        with SMBus(I2C_BUS) as bus:
            bus.write_byte(RP2040_ADDRESS, 0x00)
            version = bus.read_byte(RP2040_ADDRESS)
        status_line(True, "RP2040 I2C", f"address=0x{RP2040_ADDRESS:02x} protocol_version={version}")
        if version != 2:
            status_line(False, "RP2040 protocol", "expected version 2")
    except OSError as exc:
        status_line(False, "RP2040 I2C", str(exc))

    code, output = command_output(["i2cdetect", "-y", str(I2C_BUS)])
    if code == 0:
        seen = f"{RP2040_ADDRESS:02x}" in output.lower().split()
        status_line(seen, "i2cdetect", f"0x{RP2040_ADDRESS:02x} {'seen' if seen else 'not seen'}")
        print(output)
    else:
        status_line(None, "i2cdetect", output)


def check_bh1750() -> None:
    try:
        from smbus2 import SMBus
    except ImportError:
        return

    selected_address = os.environ.get("OMB_BH1750_ADDRESS")
    try:
        addresses = (int(selected_address, 0),) if selected_address else BH1750_ADDRESSES
    except ValueError:
        status_line(False, "env OMB_BH1750_ADDRESS", selected_address)
        return
    found = False

    try:
        with SMBus(I2C_BUS) as bus:
            for address in addresses:
                try:
                    bus.write_byte(address, BH1750_POWER_ON)
                    bus.write_byte(address, BH1750_RESET)
                    bus.write_byte(address, BH1750_ONE_TIME_HIGH_RES_MODE)
                    time.sleep(0.18)
                    msb, lsb = bus.read_i2c_block_data(
                        address,
                        BH1750_ONE_TIME_HIGH_RES_MODE,
                        2,
                    )
                except OSError as exc:
                    status_line(False, f"BH1750 0x{address:02x}", str(exc))
                    continue
                raw = (msb << 8) | lsb
                status_line(True, f"BH1750 0x{address:02x}", f"raw={raw} lux={raw / 1.2:.2f}")
                found = True
    except OSError as exc:
        status_line(False, "BH1750 I2C", str(exc))
        return

    if not found:
        status_line(False, "BH1750", "no sensor responded")


def check_gpio() -> None:
    check_python_package("RPi.GPIO", "rpi-lgpio")

    try:
        import RPi.GPIO as GPIO
    except ImportError:
        return

    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        values: list[str] = []
        for name, pin in GPIO_PINS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            values.append(f"{name}=GPIO{pin}:{GPIO.input(pin)}")
        status_line(True, "GPIO inputs", ", ".join(values))
    except RuntimeError as exc:
        status_line(False, "GPIO inputs", str(exc))
    finally:
        try:
            GPIO.cleanup()
        except Exception:
            pass


def check_audio() -> None:
    check_device_node("/dev/snd/controlC0", "audio")
    check_python_package("pygame", "pygame")

    for args in (["aplay", "-l"], ["amixer", "scontrols"]):
        code, output = command_output(args)
        status_line(code == 0 if code != 127 else None, " ".join(args), output or "no output")

    try:
        import pygame
    except ImportError:
        return

    try:
        pygame.mixer.init(
            frequency=int(os.environ.get("OMB_AUDIO_RATE", "44100")),
            size=int(os.environ.get("OMB_AUDIO_SIZE", "-16")),
            channels=int(os.environ.get("OMB_AUDIO_CHANNELS", "2")),
            buffer=int(os.environ.get("OMB_AUDIO_BUFFER", "1024")),
        )
        status_line(True, "pygame mixer", str(pygame.mixer.get_init()))
    except Exception as exc:
        status_line(False, "pygame mixer", str(exc))
    finally:
        try:
            pygame.mixer.quit()
        except Exception:
            pass


def check_system() -> None:
    status_line(None, "python", sys.executable)
    status_line(None, "platform", platform.platform())
    status_line(None, "user", f"{pwd.getpwuid(os.getuid()).pw_name} groups={','.join(sorted(current_groups()))}")
    for name in (
        "OMB_MOCK_HARDWARE",
        "OMB_GPIO_PULL",
        "OMB_BH1750_ADDRESS",
        "OMB_BH1750_LOW_THRESHOLD_LUX",
        "OMB_BH1750_HIGH_THRESHOLD_LUX",
        "SDL_AUDIODRIVER",
        "AUDIODEV",
    ):
        value = os.environ.get(name)
        status_line(None, f"env {name}", value if value is not None else "unset")

    code, output = command_output(["vcgencmd", "get_throttled"])
    status_line(code == 0 if code != 127 else None, "vcgencmd get_throttled", output)


def main() -> int:
    print("One Man Band Raspberry Pi hardware health\n")
    check_system()
    print("\nI2C")
    check_i2c()
    print("\nBH1750")
    check_bh1750()
    print("\nGPIO")
    check_gpio()
    print("\nAudio")
    check_audio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
