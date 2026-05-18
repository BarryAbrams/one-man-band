# OneManBand_2040 Control Notes

This project runs on an RP2040 and exposes a simple `I2C_1` control interface for a Raspberry Pi.

The Raspberry Pi acts as the I2C master.
The RP2040 acts as the I2C slave.

## Wiring

- `I2C_0` on `GP4` / `GP5` is used locally on the RP2040 board for onboard peripherals.
- `I2C_1` on `GP14` / `GP15` is reserved for the Raspberry Pi control link.
- RP2040 `I2C_1` slave address: `0x12`

Make sure the Raspberry Pi and RP2040 share ground.

## What The Pi Can Control

The Pi can command:

- solenoid outputs `P0` through `P3`
- servo channels `0` through `7` on the first half of the PCA9685
- NeoPixel fades and canned effects on pixel rails `1` through `4`

Power rails are fixed in firmware: `12V_B`, `12V_C`, and `8V` stay enabled;
`12V_A` stays disabled. Rail register writes are accepted for compatibility but
ignored.

The Pi can also read back:

- protocol version
- current applied rail state
- current applied solenoid state
- current applied servo enable mask
- current applied servo values
- alarm input bits

## Register Map

The protocol uses a simple register-pointer model.

To read a register:

1. Write the register address as a single byte.
2. Read one byte back.

To write a register:

1. Write the register address.
2. Write one byte of register data.

Registers:

- `0x00`: protocol version, read only
- `0x01`: fixed rail state, read only in app usage; writes are ignored
- `0x02`: solenoid state, read/write
- `0x03`: alarm state, read only
- `0x05`: servo enable mask, read/write
- `0x10` to `0x17`: servo values for channels `0` to `7`, read/write
- `0x40` to `0x4C`: NeoPixel animation command block, write/read
- `0x60` to `0x77`: atomic status snapshot block, read only

## Status Snapshot Block

Register block: `0x60` through `0x77`

The Pi can read this as one 24-byte I2C block instead of reading each status
register individually. The RP2040 main loop rebuilds the snapshot from applied
state, alarm inputs, and servo values, then the I2C request callback
serves the stable buffer. This avoids mixed-byte reads while output state is
changing.

Layout:

- byte `0`: protocol version, currently `2`
- byte `1`: snapshot sequence counter
- byte `2`: rail state
- byte `3`: solenoid state
- byte `4`: alarm state
- byte `5`: reserved, currently `0`
- byte `6`: servo enable mask
- bytes `7` to `14`: servo values for channels `0` to `7`
- bytes `15` to `22`: reserved, currently `0`
- byte `23`: checksum, `sum(bytes 0 through 22) & 0xFF`

## Rail State Register

Register: `0x01`

The rail state is fixed at `0x0E`. `12V_B`, `12V_C`, and `8V` are enabled.
`12V_A` is disabled. The firmware ignores writes to this register.

Bit layout:

- bit `0`: `12V_A`
- bit `1`: `12V_B`
- bit `2`: `12V_C`
- bit `3`: `8V`

Meaning:

- bit set to `1` = enabled
- bit set to `0` = disabled

Current fixed value:

- `0x0E` = `12V_B`, `12V_C`, and `8V` on; `12V_A` off

Hardware detail:

- the `8V` rail uses an active-low enable on the RP2040 side
- the protocol hides that detail
- from the Pi perspective, `8V = 1` still means enabled

## Solenoid State Register

Register: `0x02`

Bit layout:

- bit `0`: `P0`
- bit `1`: `P1`
- bit `2`: `P2`
- bit `3`: `P3`

Meaning:

- bit set to `1` = enabled
- bit set to `0` = disabled

Examples:

- `0x00` = all solenoids off
- `0x01` = `P0` on
- `0x05` = `P0` and `P2` on
- `0x0F` = all solenoids on

## Servo Enable Mask Register

Register: `0x05`

Bit layout:

- bit `0`: servo `0`
- bit `1`: servo `1`
- bit `2`: servo `2`
- bit `3`: servo `3`
- bit `4`: servo `4`
- bit `5`: servo `5`
- bit `6`: servo `6`
- bit `7`: servo `7`

Meaning:

- bit set to `1` = servo output enabled
- bit set to `0` = servo output disabled and channel driven off

Examples:

- `0x00` = all servos off
- `0x01` = only servo `0` enabled
- `0xFF` = all 8 servo channels enabled

## Servo Value Registers

Registers: `0x10` through `0x17`

Mapping:

- `0x10` = servo `0`
- `0x11` = servo `1`
- `0x12` = servo `2`
- `0x13` = servo `3`
- `0x14` = servo `4`
- `0x15` = servo `5`
- `0x16` = servo `6`
- `0x17` = servo `7`

Each register holds one byte from `0` to `255`.

The RP2040 maps that byte to a servo pulse width using the first 8 PCA9685
channels at a 50Hz update rate.

Meaning:

- `0` = minimum pulse
- `127` = approximately center
- `255` = maximum pulse

These are generic servo values, not absolute degrees.

## Alarm State Register

Register: `0x03`

Bit layout:

- bit `0`: `12V_C_ALARM`
- bit `1`: `8V_ALARM`

Meaning:

- bit set to `1` = alarm input reads high
- bit set to `0` = alarm input reads low

## NeoPixel Animation Command Registers

Registers: `0x40` through `0x4C`

The RP2040 owns the animation timing. The Raspberry Pi writes a command block,
then writes a nonzero trigger byte. A new triggered command replaces any pixel
animation currently running.

Pixel rails:

- bit `0`: `PIX_1` on `GP29`
- bit `1`: `PIX_2` on `GP28`
- bit `2`: `PIX_3` on `GP27`
- bit `3`: `PIX_4` on `GP26`

Each rail is currently assumed to have `100` pixels.

Register layout:

- `0x40`: rail mask
- `0x41`: start pixel index, `0` to `99`
- `0x42`: pixel count, use `0` for from start index to end of rail
- `0x43`: start/base red
- `0x44`: start/base green
- `0x45`: start/base blue
- `0x46`: end red, or canned animation parameter 0
- `0x47`: end green, or canned animation parameter 1
- `0x48`: end blue, or canned animation parameter 2
- `0x49`: duration low byte, milliseconds
- `0x4A`: duration high byte, milliseconds
- `0x4B`: trigger, write nonzero to start the animation
- `0x4C`: animation ID

Duration is a 16-bit little-endian millisecond value.

Animation IDs:

- `0`: generic color fade from start/base RGB to end RGB
- `1`: candle flicker
- `2`: lightning strike

For animation `0`, registers `0x43` to `0x45` are the start color and
registers `0x46` to `0x48` are the end color. If duration is `0`, the fade
applies the end color immediately.

For animation `1`, candle flicker:

- `0x43` to `0x45`: base RGB color
- `0x46`: hue variation, `0` to `255`
- `0x47`: seed, `0` to `255`
- `0x48`: target intensity, `0` to `255`
- duration: ramp time in milliseconds from current intensity to target intensity
- duration `0`: jump to target intensity immediately

Candle flicker runs until replaced by another pixel command. Each pixel in the
target range is treated as its own candle by mixing the seed with the rail and
pixel index. Re-send animation `1` at runtime to change the base RGB hue,
hue variation, seed, target intensity, or ramp time. If the previous animation
was also candle flicker, the new command ramps from the current candle
intensity instead of restarting from black.

For animation `2`, lightning strike:

- `0x43` to `0x45`: resting/base RGB color restored when the strike ends
- `0x46` to `0x48`: flash RGB color
- flash RGB `(0, 0, 0)`: use firmware default cool white
- duration `0`: use firmware default `850 ms`
- nonzero duration: use that duration in milliseconds

Examples:

- rail mask `0x01`, start `0`, count `0` targets all of `PIX_1`
- rail mask `0x0F`, start `0`, count `0` targets all pixels on all 4 rails
- rail mask `0x02`, start `10`, count `20` targets pixels `10` through `29` on `PIX_2`

## Protocol Version

Register: `0x00`

Current value:

- `2`

If the protocol changes later, this value should also change.

## Boot Defaults

After boot, the firmware currently applies:

- `12V_B` enabled
- `8V` enabled
- all solenoids off

That corresponds to:

- rail register = `0x0A`
- solenoid register = `0x00`
- servo enable mask = `0x00`

## Raspberry Pi Python Example

This example uses `smbus2`.

Install dependencies:

```bash
sudo apt install python3-smbus i2c-tools
pip install smbus2
```

Example script:

```python
from smbus2 import SMBus

I2C_BUS = 1
DEVICE = 0x12

REG_VERSION = 0x00
REG_RAILS = 0x01
REG_SOLENOIDS = 0x02
REG_ALARMS = 0x03
REG_SERVO_ENABLE_MASK = 0x05
REG_SERVO0 = 0x10
REG_PIXEL_COMMAND = 0x40
PIXEL_ANIMATION_FADE = 0
PIXEL_ANIMATION_CANDLE_FLICKER = 1
PIXEL_ANIMATION_LIGHTNING_STRIKE = 2

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
PIX_ALL = PIX_1 | PIX_2 | PIX_3 | PIX_4

def read_reg(bus, reg):
    bus.write_byte(DEVICE, reg)
    return bus.read_byte(DEVICE)

def write_reg(bus, reg, value):
    bus.write_i2c_block_data(DEVICE, reg, [value & 0xFF])

def read_u16_le(bus, reg_low):
    low = read_reg(bus, reg_low)
    high = read_reg(bus, reg_low + 1)
    return low | (high << 8)

def read_i16_le(bus, reg_low):
    value = read_u16_le(bus, reg_low)
    if value & 0x8000:
        value -= 0x10000
    return value

def trigger_pixel_animation(
    bus,
    rail_mask,
    start,
    count,
    base_rgb,
    param_rgb,
    duration_ms,
    animation_id,
):
    duration_ms = max(0, min(duration_ms, 65535))
    payload = [
        rail_mask & 0x0F,
        start & 0xFF,
        count & 0xFF,
        base_rgb[0] & 0xFF,
        base_rgb[1] & 0xFF,
        base_rgb[2] & 0xFF,
        param_rgb[0] & 0xFF,
        param_rgb[1] & 0xFF,
        param_rgb[2] & 0xFF,
        duration_ms & 0xFF,
        (duration_ms >> 8) & 0xFF,
        1,
        animation_id & 0xFF,
    ]
    bus.write_i2c_block_data(DEVICE, REG_PIXEL_COMMAND, payload)

def fade_pixels(bus, rail_mask, start, count, from_rgb, to_rgb, duration_ms):
    trigger_pixel_animation(
        bus,
        rail_mask,
        start,
        count,
        from_rgb,
        to_rgb,
        duration_ms,
        PIXEL_ANIMATION_FADE,
    )

def candle_flicker(
    bus,
    rail_mask,
    start,
    count,
    base_rgb,
    hue_variation,
    seed,
    target_intensity=255,
    duration_ms=0,
):
    trigger_pixel_animation(
        bus,
        rail_mask,
        start,
        count,
        base_rgb,
        (hue_variation, seed, target_intensity),
        duration_ms,
        PIXEL_ANIMATION_CANDLE_FLICKER,
    )

def lightning_strike(
    bus,
    rail_mask,
    start,
    count,
    base_rgb=(0, 0, 0),
    flash_rgb=(0, 0, 0),
    duration_ms=0,
):
    trigger_pixel_animation(
        bus,
        rail_mask,
        start,
        count,
        base_rgb,
        flash_rgb,
        duration_ms,
        PIXEL_ANIMATION_LIGHTNING_STRIKE,
    )

with SMBus(I2C_BUS) as bus:
    version = read_reg(bus, REG_VERSION)
    print("Protocol version:", version)

    rails = read_reg(bus, REG_RAILS)
    print("Rail state:", bin(rails))

    solenoids = read_reg(bus, REG_SOLENOIDS)
    print("Solenoid state:", bin(solenoids))

    write_reg(bus, REG_SOLENOIDS, SOL_P0 | SOL_P2)
    write_reg(bus, REG_SERVO_ENABLE_MASK, SERVO_0)
    write_reg(bus, REG_SERVO0, 127)

    fade_pixels(
        bus,
        rail_mask=PIX_ALL,
        start=0,
        count=0,
        from_rgb=(0, 0, 0),
        to_rgb=(0, 0, 255),
        duration_ms=1000,
    )

    candle_flicker(
        bus,
        rail_mask=PIX_1,
        start=0,
        count=0,
        base_rgb=(255, 120, 30),
        hue_variation=32,
        seed=17,
        target_intensity=255,
        duration_ms=1000,
    )

    candle_flicker(
        bus,
        rail_mask=PIX_1,
        start=0,
        count=0,
        base_rgb=(255, 80, 20),
        hue_variation=48,
        seed=17,
        target_intensity=80,
        duration_ms=2000,
    )

    lightning_strike(
        bus,
        rail_mask=PIX_ALL,
        start=0,
        count=0,
        base_rgb=(0, 0, 0),
        flash_rgb=(220, 235, 255),
        duration_ms=850,
    )
```

## i2c-tools Examples

Read protocol version:

```bash
i2cget -y 1 0x12 0x00
```

Read rail state:

```bash
i2cget -y 1 0x12 0x01
```

Enable solenoids `P0` and `P2`:

```bash
i2cset -y 1 0x12 0x02 0x05
```

Turn all solenoids off:

```bash
i2cset -y 1 0x12 0x02 0x00
```

Enable servo `0` only:

```bash
i2cset -y 1 0x12 0x05 0x01
```

Set servo `0` to center-ish value `127`:

```bash
i2cset -y 1 0x12 0x10 0x7F
```

## Suggested Safe Control Pattern

For the Pi side, a good control sequence is:

1. Read protocol version and confirm it is `2`
2. Read current rail state
3. Confirm rails read as fixed state `0x0E`
4. Enable the solenoids you need
5. Enable the servo channels you need
6. Write the servo values you want
7. Disable solenoids when done

## Notes

- Solenoid and servo writes are queued from the `I2C_1` callback and applied in the RP2040 main loop.
- Rail writes are ignored; the firmware enforces the fixed rail state.
- Readback values represent the last applied state.
