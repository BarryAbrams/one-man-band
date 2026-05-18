#include <Arduino.h>
#include <Adafruit_MCP2515.h>
#include <SPI.h>

namespace {
constexpr unsigned long kSerialBaudRate = 115200;

constexpr uint8_t kCanMisoPin = 0;
constexpr uint8_t kCanCsPin = 1;
constexpr uint8_t kCanSckPin = 2;
constexpr uint8_t kCanMosiPin = 3;
constexpr uint8_t kCanResetPin = 7;
constexpr uint8_t kCanIntPin = 8;

constexpr uint16_t kServo42dCanId = 0x01;
constexpr uint16_t kRunSpeed = 500;
constexpr uint8_t kAcceleration = 200;
constexpr bool kScanMotorIds = true;
constexpr bool kRunLoopbackSelfTest = true;
constexpr uint16_t kScanFirstCanId = 0x01;
constexpr uint16_t kScanLastCanId = 0x20;
constexpr uint16_t kScanNudgeSpeed = 120;
constexpr uint8_t kScanAcceleration = 50;
constexpr unsigned long kScanNudgeMs = 350;
constexpr unsigned long kScanSettleMs = 350;
constexpr unsigned long kRunMs = 10000;
constexpr unsigned long kStopMs = 2000;
constexpr unsigned long kCanStartupDelayMs = 8000;
constexpr bool kPulseCanResetPin = true;
constexpr bool kPrintMcpRegisterProbe = true;

constexpr uint8_t kMcpInstructionReset = 0xC0;
constexpr uint8_t kMcpInstructionRead = 0x03;
constexpr uint8_t kMcpInstructionWrite = 0x02;
constexpr uint8_t kMcpRegisterCanStat = 0x0E;
constexpr uint8_t kMcpRegisterCanCtrl = 0x0F;
constexpr uint8_t kMcpRegisterTec = 0x1C;
constexpr uint8_t kMcpRegisterRec = 0x1D;
constexpr uint8_t kMcpRegisterCanIntf = 0x2C;
constexpr uint8_t kMcpRegisterEflg = 0x2D;
constexpr uint8_t kMcpRegisterTxb0Ctrl = 0x30;

// MCP25625 is register-compatible with MCP2515 for this library. This module
// has a 16 MHz crystal, which is required for correct CAN bit timing.
constexpr long kMcp2515ClockHz = 16000000;
constexpr long kCanBaudRate = 500000;
constexpr long kScanCanBaudRates[] = {
    1000000,
    500000,
    250000,
    125000,
    100000,
    50000,
};

enum class MotionState : uint8_t {
  RunForward,
  StopAfterForward,
  RunReverse,
  StopAfterReverse,
};

Adafruit_MCP2515 canController(kCanCsPin, kCanMosiPin, kCanMisoPin,
                                kCanSckPin);
MotionState gMotionState = MotionState::RunForward;
unsigned long gBootMs = 0;
unsigned long gLastStateChangeMs = 0;
bool gCanStarted = false;
bool gCanReady = false;

uint8_t checksum(uint16_t canId, const uint8_t* data, uint8_t length) {
  uint16_t sum = canId;
  for (uint8_t index = 0; index < length; ++index) {
    sum += data[index];
  }
  return static_cast<uint8_t>(sum & 0xFF);
}

bool sendServo42dFrame(uint16_t canId, const uint8_t* payloadWithoutChecksum,
                       uint8_t payloadLength) {
  if (!canController.beginPacket(canId)) {
    return false;
  }

  for (uint8_t index = 0; index < payloadLength; ++index) {
    canController.write(payloadWithoutChecksum[index]);
  }
  canController.write(checksum(canId, payloadWithoutChecksum, payloadLength));

  return canController.endPacket() == 1;
}

bool sendMotorEnable(uint16_t canId, bool enabled) {
  const uint8_t payload[] = {
      0xF3,
      static_cast<uint8_t>(enabled ? 0x01 : 0x00),
  };
  return sendServo42dFrame(canId, payload, sizeof(payload));
}

bool sendSpeedCommand(uint16_t canId, bool clockwise, uint16_t speed,
                      uint8_t acceleration) {
  speed = min<uint16_t>(speed, 3000);

  const uint8_t speedHigh =
      static_cast<uint8_t>((clockwise ? 0x80 : 0x00) | ((speed >> 8) & 0x0F));
  const uint8_t speedLow = static_cast<uint8_t>(speed & 0xFF);
  const uint8_t payload[] = {
      0xF6,
      speedHigh,
      speedLow,
      acceleration,
  };
  return sendServo42dFrame(canId, payload, sizeof(payload));
}

bool sendMotorEnable(bool enabled) {
  return sendMotorEnable(kServo42dCanId, enabled);
}

bool sendSpeedCommand(bool clockwise, uint16_t speed, uint8_t acceleration) {
  return sendSpeedCommand(kServo42dCanId, clockwise, speed, acceleration);
}

bool runMotor(bool clockwise) {
  return sendSpeedCommand(clockwise, kRunSpeed, kAcceleration);
}

bool stopMotor() {
  return sendSpeedCommand(false, 0, kAcceleration);
}

void hardResetCanController() {
  pinMode(kCanResetPin, OUTPUT);

  if (!kPulseCanResetPin) {
    digitalWrite(kCanResetPin, HIGH);
    return;
  }

  digitalWrite(kCanResetPin, LOW);
  delay(10);
  digitalWrite(kCanResetPin, HIGH);
  delay(25);
}

uint8_t bitBangSpiTransfer(uint8_t value) {
  uint8_t response = 0;

  for (uint8_t mask = 0x80; mask != 0; mask >>= 1) {
    digitalWrite(kCanMosiPin, (value & mask) ? HIGH : LOW);
    digitalWrite(kCanSckPin, HIGH);
    if (digitalRead(kCanMisoPin)) {
      response |= mask;
    }
    digitalWrite(kCanSckPin, LOW);
  }

  return response;
}

void configureBitBangSpiPins() {
  pinMode(kCanCsPin, OUTPUT);
  pinMode(kCanSckPin, OUTPUT);
  pinMode(kCanMosiPin, OUTPUT);
  pinMode(kCanMisoPin, INPUT);

  digitalWrite(kCanCsPin, HIGH);
  digitalWrite(kCanSckPin, LOW);
  digitalWrite(kCanMosiPin, LOW);
}

void mcpSelect() {
  digitalWrite(kCanCsPin, LOW);
}

void mcpDeselect() {
  digitalWrite(kCanCsPin, HIGH);
}

void mcpResetBySpi() {
  mcpSelect();
  bitBangSpiTransfer(kMcpInstructionReset);
  mcpDeselect();
  delay(10);
}

uint8_t readMcpRegister(uint8_t address) {
  mcpSelect();
  bitBangSpiTransfer(kMcpInstructionRead);
  bitBangSpiTransfer(address);
  const uint8_t value = bitBangSpiTransfer(0x00);
  mcpDeselect();
  return value;
}

void writeMcpRegister(uint8_t address, uint8_t value) {
  mcpSelect();
  bitBangSpiTransfer(kMcpInstructionWrite);
  bitBangSpiTransfer(address);
  bitBangSpiTransfer(value);
  mcpDeselect();
}

void printHexByte(uint8_t value) {
  if (value < 0x10) {
    Serial.print('0');
  }
  Serial.print(value, HEX);
}

void printPinLevels(const char* label) {
  Serial.print(label);
  Serial.print(" CS=");
  Serial.print(digitalRead(kCanCsPin));
  Serial.print(" SCK=");
  Serial.print(digitalRead(kCanSckPin));
  Serial.print(" MOSI=");
  Serial.print(digitalRead(kCanMosiPin));
  Serial.print(" MISO=");
  Serial.print(digitalRead(kCanMisoPin));
  Serial.print(" RESET=");
  Serial.print(digitalRead(kCanResetPin));
  Serial.print(" INT=");
  Serial.println(digitalRead(kCanIntPin));
}

void printMisoPullTest() {
  pinMode(kCanMisoPin, INPUT_PULLUP);
  delay(2);
  const uint8_t pulledUp = digitalRead(kCanMisoPin);

  pinMode(kCanMisoPin, INPUT_PULLDOWN);
  delay(2);
  const uint8_t pulledDown = digitalRead(kCanMisoPin);

  pinMode(kCanMisoPin, INPUT);

  Serial.print("MISO pull test: pullup=");
  Serial.print(pulledUp);
  Serial.print(", pulldown=");
  Serial.println(pulledDown);
}

void printSpiOutputDriveTest() {
  pinMode(kCanCsPin, OUTPUT);
  pinMode(kCanSckPin, OUTPUT);
  pinMode(kCanMosiPin, OUTPUT);

  digitalWrite(kCanCsPin, HIGH);
  digitalWrite(kCanSckPin, HIGH);
  digitalWrite(kCanMosiPin, HIGH);
  delay(2);
  printPinLevels("Drive high test:");

  digitalWrite(kCanCsPin, LOW);
  digitalWrite(kCanSckPin, LOW);
  digitalWrite(kCanMosiPin, LOW);
  delay(2);
  printPinLevels("Drive low test:");

  digitalWrite(kCanCsPin, HIGH);
  digitalWrite(kCanSckPin, LOW);
  digitalWrite(kCanMosiPin, LOW);
}

void printMcpRegisterProbe() {
  if (!kPrintMcpRegisterProbe) {
    return;
  }

  printPinLevels("Before SPI probe:");
  printMisoPullTest();
  printSpiOutputDriveTest();
  configureBitBangSpiPins();
  mcpResetBySpi();

  const uint8_t canCtrlAfterReset = readMcpRegister(kMcpRegisterCanCtrl);
  writeMcpRegister(kMcpRegisterCanCtrl, 0x80);
  const uint8_t canCtrlAfterWrite = readMcpRegister(kMcpRegisterCanCtrl);

  Serial.print("MCP25625 CANCTRL after SPI reset: 0x");
  printHexByte(canCtrlAfterReset);
  Serial.print(", after write 0x80: 0x");
  printHexByte(canCtrlAfterWrite);
  Serial.println();
  printPinLevels("After SPI probe:");
}

void printCanStatus(const char* label) {
  Serial.print(label);
  Serial.print(" CANSTAT=0x");
  printHexByte(readMcpRegister(kMcpRegisterCanStat));
  Serial.print(" CANCTRL=0x");
  printHexByte(readMcpRegister(kMcpRegisterCanCtrl));
  Serial.print(" CANINTF=0x");
  printHexByte(readMcpRegister(kMcpRegisterCanIntf));
  Serial.print(" EFLG=0x");
  printHexByte(readMcpRegister(kMcpRegisterEflg));
  Serial.print(" TXB0CTRL=0x");
  printHexByte(readMcpRegister(kMcpRegisterTxb0Ctrl));
  Serial.print(" TEC=");
  Serial.print(readMcpRegister(kMcpRegisterTec));
  Serial.print(" REC=");
  Serial.println(readMcpRegister(kMcpRegisterRec));
}

bool beginCanAtBaudRate(long baudRate) {
  canController.end();
  canController.setClockFrequency(kMcp2515ClockHz);

  constexpr uint8_t kMaxCanBeginAttempts = 4;
  for (uint8_t attempt = 1; attempt <= kMaxCanBeginAttempts; ++attempt) {
    hardResetCanController();

    if (canController.begin(baudRate)) {
      return true;
    }

    Serial.print("MCP2515 begin attempt ");
    Serial.print(attempt);
    Serial.print(" failed at ");
    Serial.print(baudRate);
    Serial.println(" bps");
    delay(50);
  }

  return false;
}

void printSendResult(const char* action, bool ok) {
  Serial.print(action);
  if (ok) {
    Serial.println(" sent");
    return;
  }

  Serial.println(" send failed");
  printCanStatus("  after failed send:");
}

void printCanId(uint16_t canId) {
  Serial.print("0x");
  if (canId < 0x100) {
    Serial.print('0');
  }
  if (canId < 0x10) {
    Serial.print('0');
  }
  Serial.print(canId, HEX);
}

bool printScanSendResult(uint16_t canId, const char* action, bool ok) {
  Serial.print("  ");
  printCanId(canId);
  Serial.print(' ');
  Serial.println(ok ? action : "send failed");
  if (!ok) {
    printCanStatus("  scan abort status:");
  }
  return ok;
}

bool runMotorIdScanAtCurrentBaud() {
  Serial.print("Scanning SERVO42D CAN IDs ");
  printCanId(kScanFirstCanId);
  Serial.print("..");
  printCanId(kScanLastCanId);
  Serial.println();
  Serial.println("Watch for a short motor nudge; the matching ID should react.");

  for (uint16_t canId = kScanFirstCanId; canId <= kScanLastCanId; ++canId) {
    Serial.print("Trying CAN ID ");
    printCanId(canId);
    Serial.println();

    if (!printScanSendResult(canId, "enable",
                             sendMotorEnable(canId, true))) {
      Serial.println("CAN transmit failed; fix ACK/bitrate/wiring before ID scan.");
      return false;
    }

    if (!printScanSendResult(
            canId, "nudge",
            sendSpeedCommand(canId, true, kScanNudgeSpeed,
                             kScanAcceleration))) {
      Serial.println("CAN transmit failed during scan.");
      return false;
    }

    delay(kScanNudgeMs);

    if (!printScanSendResult(
            canId, "stop",
            sendSpeedCommand(canId, false, 0, kScanAcceleration))) {
      Serial.println("CAN transmit failed while stopping scan nudge.");
      return false;
    }

    delay(kScanSettleMs);
  }

  Serial.println("ID scan complete.");
  return true;
}

void runLoopbackSelfTest() {
  Serial.println("Running MCP loopback self-test at 500000 bps");
  canController.end();
  canController.setClockFrequency(kMcp2515ClockHz);
  hardResetCanController();

  if (!canController.begin(kCanBaudRate)) {
    Serial.println("Loopback self-test init failed");
    return;
  }

  if (!canController.loopback()) {
    Serial.println("Loopback mode request failed");
    return;
  }

  printCanStatus("CAN in loopback:");

  const uint8_t payload[] = {0xF3, 0x01};
  const bool sent = sendServo42dFrame(kServo42dCanId, payload, sizeof(payload));
  Serial.println(sent ? "Loopback transmit succeeded" : "Loopback transmit failed");

  const int packetSize = canController.parsePacket();
  if (packetSize <= 0) {
    Serial.println("Loopback receive failed");
    printCanStatus("CAN after loopback failure:");
    return;
  }

  Serial.print("Loopback received ID 0x");
  Serial.print(canController.packetId(), HEX);
  Serial.print(" length ");
  Serial.println(packetSize);
}

void runMotorIdScan() {
  for (long baudRate : kScanCanBaudRates) {
    Serial.print("Trying CAN bitrate ");
    Serial.print(baudRate);
    Serial.println(" bps");

    if (!beginCanAtBaudRate(baudRate)) {
      Serial.println("  MCP init failed at this bitrate");
      continue;
    }

    printCanStatus("CAN after begin:");
    if (runMotorIdScanAtCurrentBaud()) {
      Serial.print("Scan completed at ");
      Serial.print(baudRate);
      Serial.println(" bps");
      return;
    }

    printCanStatus("CAN after failed bitrate scan:");
    delay(250);
  }

  Serial.println("No bitrate produced a successful CAN transmit.");
}

void enterState(MotionState nextState) {
  if (!gCanReady) {
    return;
  }

  gMotionState = nextState;
  gLastStateChangeMs = millis();

  switch (gMotionState) {
    case MotionState::RunForward:
      printSendResult("forward", runMotor(true));
      break;
    case MotionState::StopAfterForward:
      printSendResult("stop after forward", stopMotor());
      break;
    case MotionState::RunReverse:
      printSendResult("reverse", runMotor(false));
      break;
    case MotionState::StopAfterReverse:
      printSendResult("stop after reverse", stopMotor());
      break;
  }
}

void advanceMotionPattern() {
  if (!gCanReady) {
    return;
  }

  const unsigned long now = millis();

  switch (gMotionState) {
    case MotionState::RunForward:
      if (now - gLastStateChangeMs >= kRunMs) {
        enterState(MotionState::StopAfterForward);
      }
      break;
    case MotionState::StopAfterForward:
      if (now - gLastStateChangeMs >= kStopMs) {
        enterState(MotionState::RunReverse);
      }
      break;
    case MotionState::RunReverse:
      if (now - gLastStateChangeMs >= kRunMs) {
        enterState(MotionState::StopAfterReverse);
      }
      break;
    case MotionState::StopAfterReverse:
      if (now - gLastStateChangeMs >= kStopMs) {
        enterState(MotionState::RunForward);
      }
      break;
  }
}

void startCanController() {
  if (gCanStarted || millis() - gBootMs < kCanStartupDelayMs) {
    return;
  }
  gCanStarted = true;

  Serial.println("Starting CAN controller");

  pinMode(kCanIntPin, INPUT_PULLUP);
  hardResetCanController();
  printMcpRegisterProbe();

  if (kRunLoopbackSelfTest) {
    runLoopbackSelfTest();
  }

  if (kScanMotorIds) {
    runMotorIdScan();
    gCanReady = false;
    return;
  }

  gCanReady = beginCanAtBaudRate(kCanBaudRate);
  if (!gCanReady) {
    Serial.println("MCP2515 init failed; motor commands disabled");
    return;
  }

  printCanStatus("CAN after begin:");

  printSendResult("enable", sendMotorEnable(true));
  enterState(MotionState::RunForward);
}

}  // namespace

void setup() {
  Serial.begin(kSerialBaudRate);
  gBootMs = millis();
  pinMode(kCanCsPin, OUTPUT);
  digitalWrite(kCanCsPin, HIGH);
  pinMode(kCanResetPin, OUTPUT);
  digitalWrite(kCanResetPin, HIGH);
  delay(250);
  Serial.println();
  Serial.println(kScanMotorIds ? "SERVO42D CAN ID scan"
                               : "SERVO42D CAN speed-mode loop");
  Serial.println("USB recovery window: CAN startup delayed 8 seconds");
}

void loop() {
  startCanController();
  advanceMotionPattern();
}
