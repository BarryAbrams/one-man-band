#include <Arduino.h>
#include <Adafruit_MCP2515.h>
#include <SPI.h>

namespace {
constexpr long kSerialBaudRate = 115200;
constexpr long kMcpClockFrequency = 16000000;
constexpr long kCanBaudRate = 500000;
#ifdef CAN_TEST_ALT_RP2040
constexpr const char* kNodeName = "ALT RP2040 CAN TEST NODE";
constexpr uint8_t kCanMisoPin = 8;
constexpr uint8_t kCanCsPin = 19;
constexpr uint8_t kCanSckPin = 14;
constexpr uint8_t kCanMosiPin = 15;
constexpr uint8_t kCanStbyPin = 16;
constexpr uint8_t kCanResetPin = 18;
constexpr uint8_t kCanIntPin = 22;
constexpr bool kHasCanStbyPin = true;
SPIClassRP2040 canSpi(spi1, kCanMisoPin, kCanCsPin, kCanSckPin, kCanMosiPin);
#else
constexpr const char* kNodeName = "RP2040 CAN TEST NODE";
constexpr uint8_t kCanMisoPin = 0;
constexpr uint8_t kCanCsPin = 1;
constexpr uint8_t kCanSckPin = 2;
constexpr uint8_t kCanMosiPin = 3;
constexpr uint8_t kCanStbyPin = 255;
constexpr uint8_t kCanResetPin = 7;
constexpr uint8_t kCanIntPin = 8;
constexpr bool kHasCanStbyPin = false;
SPIClassRP2040& canSpi = SPI;
#endif

Adafruit_MCP2515 canController(kCanCsPin, &canSpi);
bool gCanReady = false;

void printBootBanner() {
  Serial.println();
  Serial.println("########################################");
  Serial.println(kNodeName);
  Serial.println("Role: CAN byte monitor");
  Serial.println("########################################");
}

void configureSystemSpiPins() {
  const bool misoOk = canSpi.setMISO(kCanMisoPin);
  const bool mosiOk = canSpi.setMOSI(kCanMosiPin);
  const bool sckOk = canSpi.setSCK(kCanSckPin);
  const bool csOk = canSpi.setCS(kCanCsPin);

  Serial.print("System SPI pins: MISO=");
  Serial.print(misoOk ? "OK" : "FAIL");
  Serial.print(" MOSI=");
  Serial.print(mosiOk ? "OK" : "FAIL");
  Serial.print(" SCK=");
  Serial.print(sckOk ? "OK" : "FAIL");
  Serial.print(" CS=");
  Serial.println(csOk ? "OK" : "FAIL");
  Serial.print("CAN SPI pin map: MISO GP");
  Serial.print(kCanMisoPin);
  Serial.print(" MOSI GP");
  Serial.print(kCanMosiPin);
  Serial.print(" SCK GP");
  Serial.print(kCanSckPin);
  Serial.print(" CS GP");
  Serial.println(kCanCsPin);
  Serial.print("Board default macros: MISO GP");
  Serial.print(MISO);
  Serial.print(" MOSI GP");
  Serial.println(MOSI);
}

void configureCanControlPins() {
  pinMode(kCanResetPin, OUTPUT);
  digitalWrite(kCanResetPin, HIGH);
  pinMode(kCanCsPin, OUTPUT);
  digitalWrite(kCanCsPin, HIGH);
  pinMode(kCanIntPin, INPUT_PULLUP);

  if (kHasCanStbyPin) {
    pinMode(kCanStbyPin, OUTPUT);
    digitalWrite(kCanStbyPin, LOW);
    Serial.print("CAN STBY GP");
    Serial.print(kCanStbyPin);
    Serial.println(" driven LOW");
  }
}

bool resetAndInitializeMcp() {
  digitalWrite(kCanResetPin, LOW);
  delay(10);
  digitalWrite(kCanResetPin, HIGH);
  delay(25);

  canController.setClockFrequency(kMcpClockFrequency);
  return canController.begin(kCanBaudRate);
}

bool resetAndInitializeMcp(long baudRate) {
  digitalWrite(kCanResetPin, LOW);
  delay(10);
  digitalWrite(kCanResetPin, HIGH);
  delay(25);

  canController.setClockFrequency(kMcpClockFrequency);
  return canController.begin(baudRate);
}

void printHexByte(uint8_t value) {
  if (value < 0x10) {
    Serial.print('0');
  }
  Serial.print(value, HEX);
}

void receiveFrames() {
  const int packetSize = canController.parsePacket();
  if (!packetSize) {
    return;
  }

  Serial.print("RX id=0x");
  Serial.print(canController.packetId(), HEX);
  Serial.print(canController.packetExtended() ? " ext" : " std");
  Serial.print(" len=");
  Serial.print(packetSize);
  Serial.print(" data=");

  while (canController.available()) {
    printHexByte(canController.read());
    Serial.print(' ');
  }
  Serial.println();
}
}  // namespace

void setup() {
  Serial.begin(kSerialBaudRate);
  while (!Serial) {
    delay(10);
  }

  printBootBanner();
  Serial.println("Starting MCP CAN init test...");

  configureSystemSpiPins();

  configureCanControlPins();

  if (!resetAndInitializeMcp()) {
    Serial.println("MCP init failed");
    return;
  }

  gCanReady = true;
  Serial.print("MCP initialized at ");
  Serial.print(kCanBaudRate);
  Serial.print(" bps with ");
  Serial.print(kMcpClockFrequency);
  Serial.println(" Hz crystal");
  Serial.println("CAN monitor ready; printing received frames.");
}

void loop() {
  if (!gCanReady) {
    delay(1000);
    return;
  }

  receiveFrames();

  delay(2);
}
