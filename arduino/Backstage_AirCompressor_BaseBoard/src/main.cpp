#include <Arduino.h>
#include <SPI.h>
#include "CanBusHelperMini.h"

// Minimal known-good CAN sender:
// - initializes the MCP2515
// - sends one random 32-bit integer every 500 ms
// - uses 100 kbps on the CAN bus

#define FW_BUILD_ID ((uint32_t)__DATE__[0] << 24 ^ (uint32_t)__DATE__[1] << 16 ^ (uint32_t)__DATE__[2] << 8 ^ \
                     (uint32_t)__TIME__[0] << 24 ^ (uint32_t)__TIME__[1] << 16 ^ (uint32_t)__TIME__[3] << 8 ^ \
                     (uint32_t)__TIME__[4])

// MCP2515 wiring used by this board.
#define CAN_CS_PIN        12
#define CAN_INT_PIN       13
#define CAN_RESET_PIN     10

// Standard frame ID for the test sender.
#define CAN_TEST_FRAME_ID 0x123

static const uint32_t CAN_SEND_INTERVAL_MS = 500;

const uint8_t ADDRESS_PINS[3] = {2, 3, 4};
CanBusHelperMini canHelper(CAN_CS_PIN, CAN_INT_PIN, CAN_RESET_PIN);

static uint32_t lastSendMs = 0;

static uint32_t buildRandomU32() {
  uint32_t hi = (uint32_t)random(0, 65536);
  uint32_t lo = (uint32_t)random(0, 65536);
  return (hi << 16) | lo;
}

static void writeU32Le(uint8_t *buf, uint32_t value) {
  buf[0] = (uint8_t)(value & 0xFF);
  buf[1] = (uint8_t)((value >> 8) & 0xFF);
  buf[2] = (uint8_t)((value >> 16) & 0xFF);
  buf[3] = (uint8_t)((value >> 24) & 0xFF);
}

static void sendRandomFrame() {
  uint32_t value = buildRandomU32();
  uint8_t payload[4];
  writeU32Le(payload, value);

  bool ok = canHelper.sendStandard(CAN_TEST_FRAME_ID, payload, sizeof(payload));

  Serial.print("CAN TX 0x");
  Serial.print(CAN_TEST_FRAME_ID, HEX);
  Serial.print(ok ? " ok " : " fail ");
  Serial.print("value=");
  Serial.println(value);
}

void onMessage(uint8_t sender,
               uint8_t recipient,
               uint8_t messageType,
               const uint8_t *payload,
               uint8_t length)
{
  (void)sender;
  (void)recipient;
  (void)messageType;
  (void)payload;
  (void)length;
}

void setup() {
  Serial.begin(115200);
  delay(2000);

  randomSeed((uint32_t)micros() ^ (uint32_t)analogRead(A0));

  SPI.begin();

  uint8_t address = 0;
  for (uint8_t i = 0; i < 3; i++) {
    pinMode(ADDRESS_PINS[i], INPUT_PULLUP);
    if (digitalRead(ADDRESS_PINS[i]) == HIGH) {
      address |= (1 << i);
    }
  }
  address += 0xB0;

  canHelper.setFirmwareVersion(FW_BUILD_ID);
  canHelper.setAddress(address);

  Serial.println("----- Air Compressor CAN Test Sender -----");
  Serial.print("Build ID: ");
  Serial.println(FW_BUILD_ID);
  Serial.print("Device Address: 0x");
  Serial.println(address, HEX);
  Serial.println("Initializing CAN Bus...");

  canHelper.begin(onMessage);
  if (!canHelper.isReady()) {
    Serial.println("CAN controller not ready");
  } else {
    Serial.println("CAN controller ready");
  }
}

void loop() {
  delay(2);
  canHelper.loop();

  uint32_t now = millis();
  if (now - lastSendMs >= CAN_SEND_INTERVAL_MS) {
    lastSendMs = now;
    sendRandomFrame();
  }
}
