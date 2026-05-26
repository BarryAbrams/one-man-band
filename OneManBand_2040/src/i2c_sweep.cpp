#include <Arduino.h>
#include <Wire.h>

namespace {
constexpr unsigned long kSerialBaud = 115200;
constexpr uint8_t kI2c0SdaPin = 4;
constexpr uint8_t kI2c0SclPin = 5;
constexpr uint8_t kI2c1SdaPin = 14;
constexpr uint8_t kI2c1SclPin = 15;
constexpr uint8_t kTinyRfidAddress = 0x13;
constexpr uint8_t kTinyRfidStatusLength = 16;
constexpr uint8_t kTinyRfidMagic = 0xA7;

uint8_t checksumXor(const uint8_t* data, size_t length) {
  uint8_t checksum = 0;
  for (size_t i = 0; i < length; i++) {
    checksum ^= data[i];
  }
  return checksum;
}

bool addressResponds(TwoWire& bus, uint8_t address) {
  bus.beginTransmission(address);
  return bus.endTransmission() == 0;
}

void printHexByte(uint8_t value) {
  if (value < 16) {
    Serial.print('0');
  }
  Serial.print(value, HEX);
}

void readTinyRfidStatus(TwoWire& bus) {
  uint8_t status[kTinyRfidStatusLength] = {0};

  bus.beginTransmission(kTinyRfidAddress);
  bus.write(0x00);
  uint8_t writeResult = bus.endTransmission(false);
  if (writeResult != 0) {
    Serial.print("  TinyRFID register select failed: ");
    Serial.println(writeResult);
    return;
  }

  uint8_t received = bus.requestFrom(kTinyRfidAddress, kTinyRfidStatusLength);
  if (received != kTinyRfidStatusLength) {
    Serial.print("  TinyRFID short read: ");
    Serial.print(received);
    Serial.print("/");
    Serial.println(kTinyRfidStatusLength);
    while (bus.available()) {
      bus.read();
    }
    return;
  }

  for (uint8_t i = 0; i < kTinyRfidStatusLength; i++) {
    status[i] = bus.read();
  }

  Serial.print("  TinyRFID status:");
  for (uint8_t i = 0; i < kTinyRfidStatusLength; i++) {
    Serial.print(' ');
    printHexByte(status[i]);
  }
  Serial.println();

  if (status[0] != kTinyRfidMagic) {
    Serial.print("  TinyRFID bad magic: 0x");
    printHexByte(status[0]);
    Serial.println();
    return;
  }

  const uint8_t expected = checksumXor(status, kTinyRfidStatusLength - 1);
  if (status[kTinyRfidStatusLength - 1] != expected) {
    Serial.print("  TinyRFID bad checksum: got 0x");
    printHexByte(status[kTinyRfidStatusLength - 1]);
    Serial.print(" expected 0x");
    printHexByte(expected);
    Serial.println();
    return;
  }

  const bool tagPresent = (status[2] & 0x01) != 0;
  const uint16_t scanCount = status[3] | (static_cast<uint16_t>(status[4]) << 8);
  Serial.print("  TinyRFID OK tag=");
  Serial.print(tagPresent ? "yes" : "no");
  Serial.print(" scans=");
  Serial.print(scanCount);
  Serial.print(" uid=");
  for (uint8_t i = 0; i < 8; i++) {
    if (i > 0) {
      Serial.print(':');
    }
    printHexByte(status[5 + i]);
  }
  Serial.println();
}

void scanBus(TwoWire& bus, const char* label) {
  Serial.println();
  Serial.print("Scanning ");
  Serial.println(label);

  uint8_t found = 0;
  for (uint8_t address = 0x08; address <= 0x77; address++) {
    if (addressResponds(bus, address)) {
      found++;
      Serial.print("  found 0x");
      printHexByte(address);
      if (address == kTinyRfidAddress) {
        Serial.print("  <-- TinyRFID");
      }
      Serial.println();
    }
    delay(2);
  }

  if (found == 0) {
    Serial.println("  no I2C devices found");
  }
  if (found > 20) {
    Serial.println("  many addresses responded; SDA may be stuck low or the bus may be miswired");
  }
  if (addressResponds(bus, kTinyRfidAddress)) {
    readTinyRfidStatus(bus);
  }
}
}  // namespace

void setup() {
  Serial.begin(kSerialBaud);
  delay(1500);

  Serial.println("########################################");
  Serial.println("RP2040 I2C SWEEP");
  Serial.println("I2C_0: SDA GP4  SCL GP5");
  Serial.println("I2C_1: SDA GP14 SCL GP15");
  Serial.println("Looking for TinyRFID at 0x13");
  Serial.println("########################################");

  Wire.setSDA(kI2c0SdaPin);
  Wire.setSCL(kI2c0SclPin);
  Wire.begin();
  Wire.setClock(100000);

  Wire1.setSDA(kI2c1SdaPin);
  Wire1.setSCL(kI2c1SclPin);
  Wire1.begin();
  Wire1.setClock(100000);
}

void loop() {
  scanBus(Wire, "I2C_0 GP4/GP5");
  scanBus(Wire1, "I2C_1 GP14/GP15");
  Serial.println();
  Serial.println("Sweep complete; waiting 2 seconds.");
  delay(2000);
}
