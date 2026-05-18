#include <Arduino.h>
#include <Wire.h>

namespace {
constexpr uint8_t kPiI2cAddress = 0x12;
constexpr uint8_t kPiI2cSdaPin = 14;
constexpr uint8_t kPiI2cSclPin = 15;

volatile uint8_t gLastValue = 0;
volatile uint32_t gReceiveCount = 0;
volatile bool gReceived = false;

void receiveEvent(int howMany) {
  while (Wire1.available()) {
    gLastValue = static_cast<uint8_t>(Wire1.read());
    ++gReceiveCount;
    gReceived = true;
  }
  (void)howMany;
}

void requestEvent() {
  Wire1.write(gLastValue);
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);

  Wire1.setSDA(kPiI2cSdaPin);
  Wire1.setSCL(kPiI2cSclPin);
  Wire1.begin(kPiI2cAddress);
  Wire1.onReceive(receiveEvent);
  Wire1.onRequest(requestEvent);

  Serial.println();
  Serial.println("Minimal RP2040 I2C target ready");
  Serial.print("Address: 0x");
  Serial.println(kPiI2cAddress, HEX);
  Serial.print("SDA GP");
  Serial.print(kPiI2cSdaPin);
  Serial.print(", SCL GP");
  Serial.println(kPiI2cSclPin);
}

void loop() {
  static uint32_t lastPrintedCount = 0;

  if (gReceived && gReceiveCount != lastPrintedCount) {
    noInterrupts();
    const uint8_t value = gLastValue;
    const uint32_t count = gReceiveCount;
    interrupts();

    Serial.print("received count=");
    Serial.print(count);
    Serial.print(" value=");
    Serial.println(value);

    lastPrintedCount = count;
  }

  delay(10);
}
