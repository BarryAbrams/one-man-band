# One Man Band

Closed-system Raspberry Pi control surface for an RP2040-driven hardware rig.

## What is here

- Flask app for the local web interface
- Socket.IO event layer for live state updates and control actions
- Alpine-powered dashboard UI for rails, relays, servos, telemetry, GPIO, and audio playback
- I2C hardware controller with mock mode for non-Pi development
- Local audio upload library with pygame playback support

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
OMB_MOCK_HARDWARE=1 python app.py
```

Then open `http://localhost:5000`.

## Pi notes

- Real hardware mode is the default. It expects the RP2040 at I2C address `0x12` on bus `1`.
- Mock mode is useful for browser/UI work before the Pi and RP2040 are wired together.
- Audio uploads are stored under `uploads/audio/`.
- Audio playback uses `pygame`, so install project requirements inside the same virtualenv you use to run `app.py`.
- The GPIO monitor imports `RPi.GPIO`, and on current Raspberry Pi OS this is best provided by the `rpi-lgpio` compatibility package from `requirements.txt`.
- If the GPIO panel says `No compatible GPIO library is installed in this Python environment`, activate the venv and run `pip install -r requirements.txt`.
- GPIO inputs default to internal pull-ups (`OMB_GPIO_PULL=up`), which is usually what you want for a switch wired between the input and ground. Set `OMB_GPIO_PULL=down` for switches wired to 3.3V, or `OMB_GPIO_PULL=off` if you provide external bias resistors.
- Logic and state polling runs every 100 ms.
- Logic timer causes listen for named countdown timers that are started by timer actions. Timer names use only letters and numbers.
- For a fully offline deployment, vendor Alpine locally instead of relying on an external CDN.

## Next good additions

- Audio transport and playlist control
- Named presets/scenes
- RP2040 command history and safety interlocks
- Physical status page for amps, DAC, and current playback
