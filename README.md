# One Man Band

Closed-system Raspberry Pi control surface for an RP2040-driven hardware rig.

## What is here

- Flask app for the local web interface
- Socket.IO event layer for live state updates and control actions
- Alpine-powered dashboard UI for rails, relays, servos, telemetry, GPIO, and audio playback
- I2C hardware controller with mock mode for non-Pi development
- Local audio upload library with pygame playback support

## Run locally

For UI work on a non-Pi machine, use mock hardware and install only the web/runtime
dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "Flask>=3.0,<4.0" "Flask-SocketIO>=5.3,<6.0" "simple-websocket>=1.0,<2.0" "smbus2>=0.4.3,<1.0"
OMB_MOCK_HARDWARE=1 python app.py
```

Then open `http://localhost:5000`.

On the Raspberry Pi, install the full project requirements so GPIO and audio playback
support are available:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Pi notes

- Real hardware mode is the default. It expects the RP2040 at I2C address `0x12` on bus `1`.
- The app expects RP2040 protocol version `2`.
- Mock mode is useful for browser/UI work before the Pi and RP2040 are wired together.
- Audio uploads are stored under `uploads/audio/`.
- Audio playback uses `pygame`, so install project requirements inside the same virtualenv you use to run `app.py`.
- The GPIO monitor imports `RPi.GPIO`, and on current Raspberry Pi OS this is best provided by the `rpi-lgpio` compatibility package from `requirements.txt`.
- If the GPIO panel says `No compatible GPIO library is installed in this Python environment`, activate the venv and run `pip install -r requirements.txt`.
- GPIO inputs default to internal pull-ups (`OMB_GPIO_PULL=up`), which is usually what you want for a switch wired between the input and ground. Set `OMB_GPIO_PULL=down` for switches wired to 3.3V, or `OMB_GPIO_PULL=off` if you provide external bias resistors.
- Logic and state polling runs every 100 ms.
- Logic timer causes listen for named countdown timers that are started by timer actions. Timer names use only letters and numbers.
- NeoPixel commands target RP2040 pixel rails `PIX_1` through `PIX_4`, each currently treated as 100 pixels.
- For a fully offline deployment, vendor Alpine locally instead of relying on an external CDN.

## RP2040 control protocol

The Raspberry Pi is the I2C master and the RP2040 is the I2C slave at address `0x12` on bus `1`.

The register map used by this app is:

- `0x00`: protocol version, read only
- `0x01`: rail state, read/write
- `0x02`: solenoid state, read/write
- `0x03`: alarm state, read only
- `0x04`: INA260 presence bits, read only
- `0x05`: servo enable mask, read/write
- `0x10` to `0x17`: servo values for channels `0` to `7`, read/write
- `0x20` to `0x27`: INA260 telemetry, read only
- `0x40` to `0x4C`: NeoPixel animation command block, write/read

NeoPixel command registers:

- `0x40`: rail mask, bits `0` through `3` for `PIX_1` through `PIX_4`
- `0x41`: start pixel index, `0` to `99`
- `0x42`: pixel count, with `0` meaning from start index to end of rail
- `0x43` to `0x45`: start/base RGB
- `0x46` to `0x48`: end RGB or canned animation parameters
- `0x49` / `0x4A`: duration in little-endian milliseconds
- `0x4B`: trigger, write nonzero to start
- `0x4C`: animation ID

Animation IDs:

- `0`: color fade from start/base RGB to end RGB. Duration `0` applies the end color immediately.
- `1`: persistent candle flicker. Base RGB is in `0x43` to `0x45`, hue variation is `0x46`, seed is `0x47`, and target intensity is `0x48`. Duration is the ramp time from current intensity to target intensity; duration `0` jumps immediately. Candle flicker runs until another pixel command replaces it.
- `2`: lightning strike. Base RGB is restored after the strike, flash RGB is in `0x46` to `0x48`, and duration `0` uses the firmware default `850 ms`.

Boot defaults from the RP2040 are `12V_B` enabled, `8V` enabled, all solenoids off, and all servos disabled.

## Next good additions

- Audio transport controls
- Named presets/scenes
- RP2040 command history and safety interlocks
- Physical status page for amps, DAC, and current playback
