# One Man Band

Closed-system Raspberry Pi control surface for an RP2040-driven hardware rig.

## What is here

- Flask app for the local web interface
- Socket.IO event layer for live state updates and control actions
- Alpine-powered dashboard UI for rails, solenoids, alarms, and INA presence
- I2C hardware controller with mock mode for non-Pi development

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
- The GPIO monitor imports `RPi.GPIO`, and on current Raspberry Pi OS this is best provided by the `rpi-lgpio` compatibility package from `requirements.txt`.
- If the GPIO panel says `No compatible GPIO library is installed in this Python environment`, activate the venv and run `pip install -r requirements.txt`.
- For a fully offline deployment, vendor Alpine locally instead of relying on an external CDN.

## Next good additions

- Audio transport and playlist control
- Named presets/scenes
- RP2040 command history and safety interlocks
- Physical status page for amps, DAC, and current playback
