# Backstage Air Compressor Diagnostics

Raspberry Pi 4 Flask dashboard for monitoring the backstage air compressor
sensor node directly over I2C.

This branch keeps the One Man Band dashboard style, but removes the RP2040,
audio, servo, solenoid, RFID, and bus-transport assumptions. It is intended for
`pi@backstage-air-compressor.local` with two sensors attached directly to the
Pi:

- SHT40 temperature and humidity sensor on I2C
- LIS3DH accelerometer on I2C

## What is here

- Flask and Socket.IO live dashboard
- Direct I2C reader for the SHT40 and LIS3DH
- SQLite telemetry history in `data/compressor_diagnostics.sqlite3`
- SVG trend charts for temperature, humidity, and vibration
- Warning thresholds with MQTT publishing
- Mock sensor mode for local UI development

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
BAC_MOCK_HARDWARE=1 python app.py
```

Then open `http://localhost:5000`.

## Run on the Pi

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If needed, enable I2C in Raspberry Pi OS and confirm the sensors appear on the
bus:

```bash
i2cdetect -y 1
```

Start the app:

```bash
python app.py
```

The Flask host and port default to `0.0.0.0:5000` and can be changed with
`OMB_HOST` and `OMB_PORT` for compatibility with existing launch scripts.

## I2C sensor layout

- SHT40 address: `0x44`
- LIS3DH addresses tried in order: `0x18`, `0x19`
- LIS3DH is configured for 100 Hz, +/-2 g, block data update on

## Warning and MQTT settings

MQTT is enabled by default and reuses the One Man Band broker host default of
`192.168.0.153:1883`.

```bash
export BAC_MQTT_ENABLED=1
export BAC_MQTT_BROKER_HOST=192.168.0.153
export BAC_MQTT_BROKER_PORT=1883
export BAC_MQTT_WARNING_TOPIC=parcadia/backstage-air-compressor/warnings
export BAC_MQTT_STATUS_TOPIC=parcadia/backstage-air-compressor/status
```

Thresholds:

```bash
export BAC_TEMP_C_MAX=45
export BAC_HUMIDITY_PCT_MAX=75
export BAC_VIBRATION_G_MAX=0.08
export BAC_STALE_SECONDS=15
export BAC_WARNING_COOLDOWN_SECONDS=300
```

Compressor inference:

```bash
export BAC_COMPRESSOR_VIBRATION_ON_G=0.12
export BAC_COMPRESSOR_VIBRATION_OFF_G=0.08
export BAC_COMPRESSOR_CONFIRM_SAMPLES=2
```

Sampling and retention:

```bash
export BAC_SAMPLE_SECONDS=2
export BAC_RETENTION_DAYS=30
```

## Notes

- Mock mode does not require sensor hardware and writes realistic sample
  history.
- If one sensor is missing, the dashboard will show the values that are present
  and mark the rest as unavailable.
