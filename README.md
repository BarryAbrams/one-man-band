# One Man Band

Closed-system Raspberry Pi control surface for an RP2040-driven hardware rig.

## What is here

- Flask app for the local web interface
- Socket.IO event layer for live state updates and control actions
- Alpine-powered dashboard UI for fixed rail status, relays, servos, GPIO, and audio playback
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

If DAC, I2C, or GPIO stops responding, run the hardware health check from the same
virtualenv used by the app:

```bash
source .venv/bin/activate
python scripts/pi_hardware_health.py
```

The script checks package imports, `/dev/i2c-1`, the RP2040 at address `0x12`,
GPIO reads, ALSA device visibility, and pygame mixer initialization.

To route app audio to a different ALSA/SDL output, set these before starting the
app:

```bash
export OMB_AUDIO_DRIVER=alsa
export OMB_AUDIO_DEVICE="default"
python app.py
```

For HDMI audio on Raspberry Pi OS, remove or comment out the I2S DAC overlay in
`/boot/firmware/config.txt`, make sure the `dtoverlay=vc4-kms-v3d` line does not
include `noaudio`, reboot, then use `aplay -l` and `speaker-test` to find and
test the HDMI card before starting the app.

## Pi notes

- Real hardware mode is the default. It expects the RP2040 at I2C address `0x12` on bus `1`.
- The app expects RP2040 protocol version `2`.
- Mock mode is useful for browser/UI work before the Pi and RP2040 are wired together.
- Audio uploads are stored under `uploads/audio/`.
- Audio playback uses `pygame`, so install project requirements inside the same virtualenv you use to run `app.py`.
- Set `OMB_AUDIO_DRIVER=alsa` and `OMB_AUDIO_DEVICE=<SDL device name>` to force pygame to a specific output device.
- The GPIO monitor imports `RPi.GPIO`, and on current Raspberry Pi OS this is best provided by the `rpi-lgpio` compatibility package from `requirements.txt`.
- If the GPIO panel says `No compatible GPIO library is installed in this Python environment`, activate the venv and run `pip install -r requirements.txt`.
- GPIO inputs default to internal pull-ups (`OMB_GPIO_PULL=up`), which is usually what you want for a switch wired between the input and ground. Set `OMB_GPIO_PULL=down` for switches wired to 3.3V, or `OMB_GPIO_PULL=off` if you provide external bias resistors.
- A BH1750 light sensor on I2C bus `1` is polled once per second for external LED lit/unlit detection. The default address is `0x23`; override with `OMB_BH1750_ADDRESS=0x5c` if the ADDR pin is high. Set `OMB_BH1750_HIGH_THRESHOLD_LUX` and `OMB_BH1750_LOW_THRESHOLD_LUX` to add hysteresis, or set `OMB_BH1750_THRESHOLD_LUX` to use one shared threshold.
- Logic and state polling runs every 100 ms, but the background poll reads cached RP2040 state and fresh local GPIO only. RP2040 I2C reads happen on explicit refresh, health checks, and hardware commands.
- Logic timer causes listen for named countdown timers that are started by timer actions. Timer names use only letters and numbers.
- NeoPixel commands target RP2040 pixel rails `PIX_1` through `PIX_4`, each currently treated as 100 pixels.
- Power rails are fixed by firmware: `12V_B`, `12V_C`, and `8V` stay enabled; `12V_A` stays disabled.
- MQTT node control is enabled by default and connects to `192.168.0.153:1883`.
  Override with `OMB_MQTT_ENABLED=0`, `OMB_MQTT_BROKER_HOST`, `OMB_MQTT_BROKER_PORT`, or `OMB_MQTT_HOSTNAME`.
  The node subscribes to `parcadia/global/global_state`, `parcadia/<hostname>/global_state`, and `parcadia/global/whoisthere`, publishes status to `parcadia/to_gmmy/status`, and requests current state on `parcadia/to_gmmy/state_request`.
  `shutdown` turns off NeoPixels only; `restart` and `reboot` require `OMB_MQTT_ALLOW_SYSTEM_COMMANDS=1`.
- For a fully offline deployment, vendor Alpine locally instead of relying on an external CDN.

## RP2040 control protocol

The Raspberry Pi is the I2C master and the RP2040 is the I2C slave at address `0x12` on bus `1`.

The register map used by this app is:

- `0x00`: protocol version, read only
- `0x01`: fixed rail state, read only in app usage; firmware ignores writes and enforces `0x0E`
- `0x02`: solenoid state, read/write
- `0x03`: alarm state, read only
- `0x05`: servo enable mask, read/write
- `0x10` to `0x17`: servo values for channels `0` to `7`, read/write
- `0x40` to `0x4C`: NeoPixel animation command block, write/read
- `0x60` to `0x77`: atomic status snapshot block, read only

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

Status snapshot block:

- Python first tries to read 24 bytes from register `0x60` using one I2C block read. If that snapshot is not available, it falls back to the older per-register reads.
- Firmware should copy live state into a stable 24-byte buffer before serving the snapshot so all bytes come from one coherent moment.
- Byte `0`: protocol version, currently `2`
- Byte `1`: snapshot sequence counter, incremented whenever the snapshot buffer is rebuilt
- Byte `2`: rail state
- Byte `3`: solenoid state
- Byte `4`: alarm state
- Byte `5`: reserved, currently `0`
- Byte `6`: servo enable mask
- Bytes `7` to `14`: servo values for channels `0` to `7`
- Bytes `15` to `22`: reserved, currently `0`
- Byte `23`: checksum, `sum(bytes 0 through 22) & 0xFF`

Boot defaults from the RP2040 are `12V_B`, `12V_C`, and `8V` enabled; `12V_A` disabled; all solenoids off; and all servos disabled.

## Next good additions

- Audio transport controls
- Named presets/scenes
- RP2040 command history and safety interlocks
- Physical status page for amps, DAC, and current playback
