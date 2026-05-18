#include <Arduino.h>
#include <Wire.h>

namespace {
constexpr uint8_t kPiI2cAddress = 0x12;
constexpr uint8_t kPiI2cSdaPin = 14;
constexpr uint8_t kPiI2cSclPin = 15;
constexpr uint8_t kPeripheralI2cSdaPin = 4;
constexpr uint8_t kPeripheralI2cSclPin = 5;

constexpr uint8_t k12vCEnablePin = 9;
constexpr uint8_t k12vBEnablePin = 10;
constexpr uint8_t k12vAEnablePin = 11;
constexpr uint8_t k8vEnablePin = 12;

constexpr uint8_t kTca9534Address = 0x20;
constexpr uint8_t kTca9534OutputPortRegister = 0x01;
constexpr uint8_t kTca9534PolarityRegister = 0x02;
constexpr uint8_t kTca9534ConfigRegister = 0x03;

constexpr uint8_t kRegisterRailState = 0x01;
constexpr uint8_t kRegisterRelayState = 0x02;
constexpr uint8_t kRegisterServoEnableMask = 0x05;
constexpr uint8_t kRegisterServo0Value = 0x10;
constexpr uint8_t kRegisterServo7Value = 0x17;
constexpr uint8_t kRegisterPixelCommandStart = 0x40;
constexpr uint8_t kRegisterPixelCommandEnd = 0x4C;
constexpr uint8_t kRegisterPixelTrigger = 0x4B;
constexpr uint8_t kRegisterPixelAnimationId = 0x4C;

constexpr uint8_t kRailMask = 0x0F;
constexpr uint8_t kRelayMask = 0x0F;
constexpr uint8_t kServoCount = 8;
constexpr uint8_t kPixelLineCount = 4;
constexpr uint8_t kPixelCommandSize =
    kRegisterPixelCommandEnd - kRegisterPixelCommandStart + 1;

constexpr uint8_t kCommandQueueSize = 8;
constexpr uint8_t kMaxCommandBytes = 16;

struct RailOutput {
  uint8_t mask = 0;
  uint8_t pin = 0;
  bool enabledLevel = HIGH;
};

struct I2cCommand {
  uint8_t size = 0;
  uint8_t bytes[kMaxCommandBytes] = {};
};

struct Rgb {
  uint8_t r = 0;
  uint8_t g = 0;
  uint8_t b = 0;
};

struct PixelLineState {
  bool active = false;
  uint8_t animationId = 0;
  uint8_t start = 0;
  uint8_t count = 0;
  Rgb startRgb = {};
  Rgb endRgb = {};
  uint16_t durationMs = 0;
  uint32_t updateCount = 0;
};

struct ControllerState {
  uint8_t railMask = 0x0E;  // 12V_B, 12V_C, and 8V on by default.
  uint8_t relayMask = 0x00;
  uint8_t servoEnableMask = 0x00;
  uint8_t servoValues[kServoCount] = {127, 127, 127, 127, 127, 127, 127, 127};
  PixelLineState pixelLines[kPixelLineCount] = {};
  uint32_t receivedCommandCount = 0;
  uint32_t droppedCommandCount = 0;
};

ControllerState gState;
uint8_t gPixelCommand[kPixelCommandSize] = {};
bool gTca9534Ready = false;

constexpr RailOutput kRailOutputs[] = {
    {static_cast<uint8_t>(1u << 0), k12vAEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 1), k12vBEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 2), k12vCEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 3), k8vEnablePin, LOW},
};

volatile I2cCommand gCommandQueue[kCommandQueueSize];
volatile uint8_t gCommandHead = 0;
volatile uint8_t gCommandTail = 0;
volatile uint32_t gQueuedCommandCount = 0;
volatile uint32_t gDroppedCommandCount = 0;

void writeRailOutput(const RailOutput& output, bool enabled) {
  const uint8_t disabledLevel = output.enabledLevel == HIGH ? LOW : HIGH;
  digitalWrite(output.pin, enabled ? output.enabledLevel : disabledLevel);
}

void configureRailOutputs() {
  for (const RailOutput& output : kRailOutputs) {
    pinMode(output.pin, OUTPUT);
    writeRailOutput(output, false);
  }
}

void applyRailOutputs(uint8_t railMask) {
  for (const RailOutput& output : kRailOutputs) {
    writeRailOutput(output, (railMask & output.mask) != 0);
  }
}

bool writePeripheralRegister(uint8_t address, uint8_t registerAddress,
                             uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(registerAddress);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

void initializeTca9534() {
  const bool outputOk =
      writePeripheralRegister(kTca9534Address, kTca9534OutputPortRegister, 0x00);
  const bool polarityOk =
      writePeripheralRegister(kTca9534Address, kTca9534PolarityRegister, 0x00);
  const bool configOk =
      writePeripheralRegister(kTca9534Address, kTca9534ConfigRegister, 0x00);

  gTca9534Ready = outputOk && polarityOk && configOk;
  Serial.print("TCA9534 init: ");
  Serial.println(gTca9534Ready ? "ok" : "FAILED");
}

bool applyRelayOutputs(uint8_t relayMask) {
  if (!gTca9534Ready) {
    return false;
  }
  return writePeripheralRegister(kTca9534Address, kTca9534OutputPortRegister,
                                 relayMask & kRelayMask);
}

bool queueIsFull() {
  const uint8_t nextHead =
      static_cast<uint8_t>((gCommandHead + 1) % kCommandQueueSize);
  return nextHead == gCommandTail;
}

void receiveEvent(int byteCount) {
  if (byteCount <= 0) {
    return;
  }

  if (queueIsFull()) {
    while (Wire1.available()) {
      Wire1.read();
    }
    ++gDroppedCommandCount;
    return;
  }

  volatile I2cCommand& command = gCommandQueue[gCommandHead];
  command.size = 0;

  while (Wire1.available() && command.size < kMaxCommandBytes) {
    command.bytes[command.size] = static_cast<uint8_t>(Wire1.read());
    ++command.size;
  }
  while (Wire1.available()) {
    Wire1.read();
    ++gDroppedCommandCount;
  }

  gCommandHead = static_cast<uint8_t>((gCommandHead + 1) % kCommandQueueSize);
  ++gQueuedCommandCount;
  (void)byteCount;
}

void requestEvent() {
  // Status readback is intentionally not part of this minimal link yet.
  Wire1.write(static_cast<uint8_t>(0));
}

bool popCommand(I2cCommand& command) {
  noInterrupts();
  if (gCommandHead == gCommandTail) {
    interrupts();
    return false;
  }

  command.size = gCommandQueue[gCommandTail].size;
  for (uint8_t index = 0; index < command.size; ++index) {
    command.bytes[index] = gCommandQueue[gCommandTail].bytes[index];
  }
  gCommandTail = static_cast<uint8_t>((gCommandTail + 1) % kCommandQueueSize);
  interrupts();
  return true;
}

uint16_t littleEndianU16(uint8_t low, uint8_t high) {
  return static_cast<uint16_t>(low) | (static_cast<uint16_t>(high) << 8);
}

bool applyPixelCommandIfTriggered() {
  if (gPixelCommand[kRegisterPixelTrigger - kRegisterPixelCommandStart] == 0) {
    return false;
  }

  bool changed = false;
  const uint8_t targetMask = gPixelCommand[0] & 0x0F;
  for (uint8_t line = 0; line < kPixelLineCount; ++line) {
    if ((targetMask & static_cast<uint8_t>(1u << line)) == 0) {
      continue;
    }

    PixelLineState& pixelLine = gState.pixelLines[line];
    pixelLine.active = true;
    pixelLine.animationId =
        gPixelCommand[kRegisterPixelAnimationId - kRegisterPixelCommandStart];
    pixelLine.start = gPixelCommand[1];
    pixelLine.count = gPixelCommand[2];
    pixelLine.startRgb = {gPixelCommand[3], gPixelCommand[4], gPixelCommand[5]};
    pixelLine.endRgb = {gPixelCommand[6], gPixelCommand[7], gPixelCommand[8]};
    pixelLine.durationMs = littleEndianU16(gPixelCommand[9], gPixelCommand[10]);
    ++pixelLine.updateCount;
    changed = true;
  }

  gPixelCommand[kRegisterPixelTrigger - kRegisterPixelCommandStart] = 0;
  return changed;
}

bool applyRegisterWrite(uint8_t registerAddress, const uint8_t* values,
                        uint8_t valueCount) {
  if (valueCount == 0) {
    return false;
  }

  switch (registerAddress) {
    case kRegisterRailState: {
      const uint8_t nextRailMask = values[0] & kRailMask;
      const bool changed = gState.railMask != nextRailMask;
      gState.railMask = nextRailMask;
      applyRailOutputs(gState.railMask);
      return changed;
    }
    case kRegisterRelayState: {
      const uint8_t nextRelayMask = values[0] & kRelayMask;
      const bool changed = gState.relayMask != nextRelayMask;
      gState.relayMask = nextRelayMask;
      if (!applyRelayOutputs(gState.relayMask)) {
        Serial.println("TCA9534 write FAILED while applying relay state");
      }
      return changed;
    }
    case kRegisterServoEnableMask: {
      const bool changed = gState.servoEnableMask != values[0];
      gState.servoEnableMask = values[0];
      return changed;
    }
    default:
      break;
  }

  if (registerAddress >= kRegisterServo0Value &&
      registerAddress <= kRegisterServo7Value) {
    uint8_t channel = registerAddress - kRegisterServo0Value;
    bool changed = false;
    for (uint8_t index = 0; index < valueCount && channel < kServoCount;
         ++index, ++channel) {
      changed = changed || gState.servoValues[channel] != values[index];
      gState.servoValues[channel] = values[index];
    }
    return changed;
  }

  if (registerAddress >= kRegisterPixelCommandStart &&
      registerAddress <= kRegisterPixelCommandEnd) {
    uint8_t pixelRegister = registerAddress - kRegisterPixelCommandStart;
    for (uint8_t index = 0;
         index < valueCount && pixelRegister < kPixelCommandSize;
         ++index, ++pixelRegister) {
      gPixelCommand[pixelRegister] = values[index];
    }
    return applyPixelCommandIfTriggered();
  }

  return false;
}

bool processCommand(const I2cCommand& command) {
  if (command.size == 0) {
    return false;
  }

  ++gState.receivedCommandCount;

  if (command.size == 1) {
    // Keeps the old one-byte test script useful: one byte means relay mask.
    const uint8_t nextRelayMask = command.bytes[0] & kRelayMask;
    const bool changed = gState.relayMask != nextRelayMask;
    gState.relayMask = nextRelayMask;
    if (!applyRelayOutputs(gState.relayMask)) {
      Serial.println("TCA9534 write FAILED while applying relay state");
    }
    return changed;
  }

  return applyRegisterWrite(command.bytes[0], &command.bytes[1],
                            command.size - 1);
}

void printBinaryNibble(uint8_t value) {
  for (int8_t bit = 3; bit >= 0; --bit) {
    Serial.print((value & (1u << bit)) ? '1' : '0');
  }
}

void printStateSummary() {
  Serial.print("state commands=");
  Serial.print(gState.receivedCommandCount);
  Serial.print(" dropped=");
  noInterrupts();
  const uint32_t dropped = gDroppedCommandCount;
  interrupts();
  Serial.print(dropped);

  Serial.print(" rails=0b");
  printBinaryNibble(gState.railMask);
  Serial.print(" relays=0b");
  printBinaryNibble(gState.relayMask);
  Serial.print(" servo_enable=0x");
  Serial.print(gState.servoEnableMask, HEX);
  Serial.print(" servos=[");
  for (uint8_t index = 0; index < kServoCount; ++index) {
    if (index != 0) {
      Serial.print(',');
    }
    Serial.print(gState.servoValues[index]);
  }
  Serial.print("] pixels=[");
  for (uint8_t line = 0; line < kPixelLineCount; ++line) {
    if (line != 0) {
      Serial.print(',');
    }
    Serial.print(gState.pixelLines[line].active ? 'A' : '-');
    Serial.print(':');
    Serial.print(gState.pixelLines[line].animationId);
    Serial.print('#');
    Serial.print(gState.pixelLines[line].updateCount);
  }
  Serial.println(']');
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);

  configureRailOutputs();
  applyRailOutputs(gState.railMask);

  Wire.setSDA(kPeripheralI2cSdaPin);
  Wire.setSCL(kPeripheralI2cSclPin);
  Wire.begin();
  initializeTca9534();
  applyRelayOutputs(gState.relayMask);

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
  printStateSummary();
}

void loop() {
  I2cCommand command;
  bool changed = false;
  while (popCommand(command)) {
    changed = processCommand(command) || changed;
  }

  if (changed) {
    printStateSummary();
  }

  delay(1);
}
