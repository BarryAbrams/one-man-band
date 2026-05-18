#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
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
constexpr uint8_t kPixel4Pin = 26;
constexpr uint8_t kPixel3Pin = 27;
constexpr uint8_t kPixel2Pin = 28;
constexpr uint8_t kPixel1Pin = 29;

constexpr uint8_t kTca9534Address = 0x20;
constexpr uint8_t kTca9534OutputPortRegister = 0x01;
constexpr uint8_t kTca9534PolarityRegister = 0x02;
constexpr uint8_t kTca9534ConfigRegister = 0x03;

constexpr uint8_t kRegisterRailState = 0x01;
constexpr uint8_t kRegisterRelayState = 0x02;
constexpr uint8_t kRegisterPixelCommandStart = 0x40;
constexpr uint8_t kRegisterPixelCommandEnd = 0x4C;
constexpr uint8_t kRegisterPixelTrigger = 0x4B;
constexpr uint8_t kRegisterPixelAnimationId = 0x4C;
constexpr uint8_t kRegisterStatusSnapshot = 0x60;
constexpr uint8_t kStatusSnapshotLength = 8;
constexpr uint8_t kStatusSnapshotMagic = 0xA5;
constexpr uint8_t kStatusSnapshotVersion = 2;
constexpr uint8_t kStatusSnapshotChecksumIndex = kStatusSnapshotLength - 1;

constexpr uint8_t kRailMask = 0x0F;
constexpr uint8_t kFixedRailMask = kRailMask;
constexpr uint8_t kRelayMask = 0x0F;
constexpr uint8_t kPixelLineCount = 4;
constexpr uint16_t kPixelsPerLine = 100;
constexpr uint8_t kPixelCommandSize =
    kRegisterPixelCommandEnd - kRegisterPixelCommandStart + 1;
constexpr uint8_t kPixelAnimationStatic = 0;
constexpr uint8_t kPixelAnimationCandleFlicker = 1;
constexpr unsigned long kCandleFlickerFrameMs = 45;
constexpr unsigned long kRailReassertMs = 250;

constexpr uint8_t kCommandQueueSize = 32;
constexpr uint8_t kRelayQueueSize = 32;
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

struct PixelAnimation {
  bool active = false;
  uint8_t lineMask = 0;
  uint16_t startIndex = 0;
  uint16_t count = 0;
  Rgb baseRgb = {};
  uint8_t hueVariation = 0;
  uint8_t seed = 0;
  uint8_t startIntensity = 0;
  uint8_t targetIntensity = 0;
  unsigned long rampStartMs = 0;
  uint16_t rampDurationMs = 0;
  unsigned long nextFrameMs = 0;
  uint16_t frameCounter = 0;
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
  uint8_t railMask = kFixedRailMask;
  uint8_t relayMask = 0x00;
  PixelLineState pixelLines[kPixelLineCount] = {};
  uint32_t receivedCommandCount = 0;
  uint32_t droppedCommandCount = 0;
};

ControllerState gState;
uint8_t gPixelCommand[kPixelCommandSize] = {};
bool gTca9534Ready = false;
PixelAnimation gPixelAnimations[kPixelLineCount] = {};
unsigned long gLastRailReassertMs = 0;

constexpr RailOutput kRailOutputs[] = {
    {static_cast<uint8_t>(1u << 0), k12vAEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 1), k12vBEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 2), k12vCEnablePin, HIGH},
    {static_cast<uint8_t>(1u << 3), k8vEnablePin, LOW},
};

Adafruit_NeoPixel pixelLines[kPixelLineCount] = {
    Adafruit_NeoPixel(kPixelsPerLine, kPixel1Pin, NEO_RGB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerLine, kPixel2Pin, NEO_RGB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerLine, kPixel3Pin, NEO_RGB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerLine, kPixel4Pin, NEO_RGB + NEO_KHZ800),
};

volatile I2cCommand gCommandQueue[kCommandQueueSize];
volatile uint8_t gCommandHead = 0;
volatile uint8_t gCommandTail = 0;
volatile uint32_t gQueuedCommandCount = 0;
volatile uint32_t gDroppedCommandCount = 0;
volatile uint8_t gLastRegisterPointer = kRegisterStatusSnapshot;
volatile uint8_t gStatusSnapshot[kStatusSnapshotLength] = {};
volatile uint8_t gRelayQueue[kRelayQueueSize] = {};
volatile uint8_t gRelayHead = 0;
volatile uint8_t gRelayTail = 0;
volatile uint32_t gDroppedRelayCount = 0;

void printBinaryNibble(uint8_t value);

void writeRailOutput(const RailOutput& output, bool enabled) {
  const uint8_t disabledLevel = output.enabledLevel == HIGH ? LOW : HIGH;
  digitalWrite(output.pin, enabled ? output.enabledLevel : disabledLevel);
}

void configureRailOutputs() {
  for (const RailOutput& output : kRailOutputs) {
    pinMode(output.pin, OUTPUT);
  }
}

void applyRailOutputs(uint8_t railMask) {
  for (const RailOutput& output : kRailOutputs) {
    writeRailOutput(output, (railMask & output.mask) != 0);
  }
}

void forceRailsEnabled() {
  gState.railMask = kFixedRailMask;
  applyRailOutputs(kFixedRailMask);
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

uint8_t maxRgbChannel(const Rgb& color) {
  return max<uint8_t>(color.r, max<uint8_t>(color.g, color.b));
}

uint8_t minRgbChannel(const Rgb& color) {
  return min<uint8_t>(color.r, min<uint8_t>(color.g, color.b));
}

uint8_t rgbHue(const Rgb& color) {
  const uint8_t maxChannel = maxRgbChannel(color);
  const uint8_t minChannel = minRgbChannel(color);
  const uint8_t delta = maxChannel - minChannel;

  if (delta == 0) {
    return 0;
  }

  int16_t hue = 0;
  if (maxChannel == color.r) {
    hue = (43 * (static_cast<int16_t>(color.g) -
                 static_cast<int16_t>(color.b))) /
          delta;
  } else if (maxChannel == color.g) {
    hue = 85 + (43 * (static_cast<int16_t>(color.b) -
                      static_cast<int16_t>(color.r))) /
                     delta;
  } else {
    hue = 171 + (43 * (static_cast<int16_t>(color.r) -
                       static_cast<int16_t>(color.g))) /
                      delta;
  }

  if (hue < 0) {
    hue += 255;
  }
  return static_cast<uint8_t>(hue);
}

uint8_t rgbSaturation(const Rgb& color) {
  const uint8_t maxChannel = maxRgbChannel(color);
  if (maxChannel == 0) {
    return 0;
  }
  return static_cast<uint8_t>(((maxChannel - minRgbChannel(color)) * 255u) /
                              maxChannel);
}

Rgb hsvRgb(uint8_t hue, uint8_t saturation, uint8_t value) {
  const uint32_t packed =
      Adafruit_NeoPixel::ColorHSV(static_cast<uint16_t>(hue) * 257u,
                                  saturation, value);
  const uint32_t rgb = Adafruit_NeoPixel::gamma32(packed);
  return {
      .r = static_cast<uint8_t>((rgb >> 16) & 0xFF),
      .g = static_cast<uint8_t>((rgb >> 8) & 0xFF),
      .b = static_cast<uint8_t>(rgb & 0xFF),
  };
}

uint8_t animationHashByte(uint8_t seed, uint8_t lineIndex, uint16_t pixelIndex,
                          uint16_t frameCounter, uint8_t salt) {
  uint16_t value =
      static_cast<uint16_t>((static_cast<uint16_t>(seed) << 8) |
                            (lineIndex * 41u));
  value ^= static_cast<uint16_t>(pixelIndex * 109u);
  value ^= static_cast<uint16_t>(frameCounter * 251u);
  value ^= static_cast<uint16_t>(salt * 199u);
  value ^= static_cast<uint16_t>(value << 7);
  value ^= static_cast<uint16_t>(value >> 9);
  value = static_cast<uint16_t>((value * 2053u) + 13849u);
  return static_cast<uint8_t>(value >> 8);
}

uint8_t scaleByte(uint8_t value, uint8_t scale) {
  return static_cast<uint8_t>((static_cast<uint16_t>(value) * scale) / 255u);
}

uint8_t interpolateByte(uint8_t start, uint8_t end, uint8_t progress) {
  const int16_t delta = static_cast<int16_t>(end) - static_cast<int16_t>(start);
  return static_cast<uint8_t>(static_cast<int16_t>(start) +
                              ((delta * progress) / 255));
}

uint8_t currentCandleIntensity(const PixelAnimation& animation,
                               unsigned long now) {
  if (animation.rampDurationMs == 0) {
    return animation.targetIntensity;
  }

  const unsigned long elapsed = now - animation.rampStartMs;
  const uint8_t progress =
      elapsed >= animation.rampDurationMs
          ? 255
          : static_cast<uint8_t>((elapsed * 255UL) / animation.rampDurationMs);
  return interpolateByte(animation.startIntensity, animation.targetIntensity,
                         progress);
}

void showTargetedPixelLines(uint8_t lineMask) {
  for (uint8_t lineIndex = 0; lineIndex < kPixelLineCount; ++lineIndex) {
    if ((lineMask & static_cast<uint8_t>(1u << lineIndex)) != 0) {
      pixelLines[lineIndex].show();
    }
  }
}

void fillPixelRange(uint8_t lineIndex, uint16_t startIndex, uint16_t count,
                    const Rgb& color) {
  if (lineIndex >= kPixelLineCount || startIndex >= kPixelsPerLine) {
    return;
  }

  const uint16_t endIndex =
      min<uint16_t>(kPixelsPerLine, static_cast<uint16_t>(startIndex + count));
  const uint32_t packedColor =
      pixelLines[lineIndex].Color(color.r, color.g, color.b);
  for (uint16_t pixelIndex = startIndex; pixelIndex < endIndex; ++pixelIndex) {
    pixelLines[lineIndex].setPixelColor(pixelIndex, packedColor);
  }
}

void applyStaticPixels(uint8_t lineMask, uint16_t startIndex, uint16_t count,
                       const Rgb& color) {
  for (uint8_t lineIndex = 0; lineIndex < kPixelLineCount; ++lineIndex) {
    if ((lineMask & static_cast<uint8_t>(1u << lineIndex)) != 0) {
      fillPixelRange(lineIndex, startIndex, count, color);
    }
  }
  showTargetedPixelLines(lineMask);
}

void applyCandleFrame(PixelAnimation& animation, unsigned long now) {
  const uint8_t intensity = currentCandleIntensity(animation, now);
  const uint8_t baseHue = rgbHue(animation.baseRgb);
  const uint8_t saturation = rgbSaturation(animation.baseRgb);
  const uint8_t baseValue = maxRgbChannel(animation.baseRgb);
  const uint16_t endIndex =
      min<uint16_t>(kPixelsPerLine,
                    static_cast<uint16_t>(animation.startIndex +
                                          animation.count));

  for (uint8_t lineIndex = 0; lineIndex < kPixelLineCount; ++lineIndex) {
    if ((animation.lineMask & static_cast<uint8_t>(1u << lineIndex)) == 0) {
      continue;
    }

    for (uint16_t pixelIndex = animation.startIndex; pixelIndex < endIndex;
         ++pixelIndex) {
      const int16_t variation =
          (static_cast<int16_t>(animationHashByte(
               animation.seed, lineIndex, pixelIndex, animation.frameCounter,
               3)) -
           127) *
          static_cast<int16_t>(animation.hueVariation) / 255;
      const uint8_t hue = static_cast<uint8_t>(baseHue + variation);
      const uint8_t brightnessNoise =
          animationHashByte(animation.seed, lineIndex, pixelIndex,
                            animation.frameCounter, 17);
      const uint8_t brightnessScale =
          static_cast<uint8_t>(145u + ((brightnessNoise * 110u) / 255u));
      const uint8_t value =
          scaleByte(scaleByte(baseValue, brightnessScale), intensity);
      const Rgb color = hsvRgb(hue, saturation, value);
      pixelLines[lineIndex].setPixelColor(
          pixelIndex, pixelLines[lineIndex].Color(color.r, color.g, color.b));
    }
  }

  showTargetedPixelLines(animation.lineMask);
  ++animation.frameCounter;
}

void initializePixelLines() {
  for (uint8_t lineIndex = 0; lineIndex < kPixelLineCount; ++lineIndex) {
    pixelLines[lineIndex].begin();
    pixelLines[lineIndex].setBrightness(64);
    pixelLines[lineIndex].clear();
    pixelLines[lineIndex].show();
  }
}

bool updatePixelAnimations() {
  const unsigned long now = millis();
  for (uint8_t lineIndex = 0; lineIndex < kPixelLineCount; ++lineIndex) {
    PixelAnimation& animation = gPixelAnimations[lineIndex];
    if (!animation.active || now < animation.nextFrameMs) {
      continue;
    }

    animation.nextFrameMs = now + kCandleFlickerFrameMs;
    applyCandleFrame(animation, now);

    const bool rampComplete =
        animation.rampDurationMs == 0 ||
        now - animation.rampStartMs >= animation.rampDurationMs;
    if (rampComplete && animation.targetIntensity == 0) {
      const Rgb black = {};
      applyStaticPixels(static_cast<uint8_t>(1u << lineIndex),
                        animation.startIndex, animation.count, black);
      animation.active = false;
      gState.pixelLines[lineIndex].active = false;
      return true;
    }

    return false;
  }
  return false;
}

uint8_t snapshotChecksum(const uint8_t* snapshot) {
  uint8_t checksum = 0;
  for (uint8_t index = 0; index < kStatusSnapshotChecksumIndex; ++index) {
    checksum = static_cast<uint8_t>(checksum + snapshot[index]);
  }
  return static_cast<uint8_t>(0u - checksum);
}

void rebuildStatusSnapshot() {
  uint8_t snapshot[kStatusSnapshotLength] = {};
  snapshot[0] = kStatusSnapshotMagic;
  snapshot[1] = kStatusSnapshotVersion;
  snapshot[2] = gState.railMask;
  snapshot[3] = gState.relayMask;
  for (uint8_t line = 0; line < kPixelLineCount; ++line) {
    if (gState.pixelLines[line].active) {
      snapshot[4] |= static_cast<uint8_t>(1u << line);
    }
  }
  snapshot[5] = gTca9534Ready ? 1 : 0;
  snapshot[kStatusSnapshotChecksumIndex] = snapshotChecksum(snapshot);

  noInterrupts();
  for (uint8_t index = 0; index < kStatusSnapshotLength; ++index) {
    gStatusSnapshot[index] = snapshot[index];
  }
  interrupts();
}

bool queueIsFull() {
  const uint8_t nextHead =
      static_cast<uint8_t>((gCommandHead + 1) % kCommandQueueSize);
  return nextHead == gCommandTail;
}

bool relayQueueIsFull() {
  const uint8_t nextHead =
      static_cast<uint8_t>((gRelayHead + 1) % kRelayQueueSize);
  return nextHead == gRelayTail;
}

void pushRelayCommand(uint8_t relayMask) {
  if (relayQueueIsFull()) {
    ++gDroppedRelayCount;
    return;
  }

  gRelayQueue[gRelayHead] = relayMask & kRelayMask;
  gRelayHead = static_cast<uint8_t>((gRelayHead + 1) % kRelayQueueSize);
}

void receiveEvent(int byteCount) {
  if (byteCount <= 0) {
    return;
  }

  uint8_t bytes[kMaxCommandBytes] = {};
  uint8_t size = 0;
  while (Wire1.available() && size < kMaxCommandBytes) {
    bytes[size] = static_cast<uint8_t>(Wire1.read());
    ++size;
  }
  while (Wire1.available()) {
    Wire1.read();
    ++gDroppedCommandCount;
  }

  if (size == 0) {
    return;
  }

  if (byteCount == 1) {
    gLastRegisterPointer = bytes[0];
    return;
  }

  if (size >= 2 && bytes[0] == kRegisterRelayState) {
    pushRelayCommand(bytes[1]);
    ++gQueuedCommandCount;
    return;
  }

  if (queueIsFull()) {
    ++gDroppedCommandCount;
    return;
  }

  volatile I2cCommand& command = gCommandQueue[gCommandHead];
  command.size = size;
  for (uint8_t index = 0; index < size; ++index) {
    command.bytes[index] = bytes[index];
  }

  gCommandHead = static_cast<uint8_t>((gCommandHead + 1) % kCommandQueueSize);
  ++gQueuedCommandCount;
  (void)byteCount;
}

void requestEvent() {
  if (gLastRegisterPointer == kRegisterStatusSnapshot) {
    uint8_t snapshot[kStatusSnapshotLength] = {};
    noInterrupts();
    for (uint8_t index = 0; index < kStatusSnapshotLength; ++index) {
      snapshot[index] = gStatusSnapshot[index];
    }
    interrupts();
    Wire1.write(snapshot, kStatusSnapshotLength);
    return;
  }

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

bool popRelayCommand(uint8_t& relayMask) {
  noInterrupts();
  if (gRelayHead == gRelayTail) {
    interrupts();
    return false;
  }

  relayMask = gRelayQueue[gRelayTail];
  gRelayTail = static_cast<uint8_t>((gRelayTail + 1) % kRelayQueueSize);
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

  const uint8_t targetMask = gPixelCommand[0] & 0x0F;
  if (targetMask == 0) {
    gPixelCommand[kRegisterPixelTrigger - kRegisterPixelCommandStart] = 0;
    return false;
  }

  const uint16_t startIndex = min<uint16_t>(gPixelCommand[1], kPixelsPerLine - 1);
  uint16_t count = gPixelCommand[2];
  if (count == 0 || startIndex + count > kPixelsPerLine) {
    count = kPixelsPerLine - startIndex;
  }
  const Rgb baseRgb = {gPixelCommand[3], gPixelCommand[4], gPixelCommand[5]};
  const Rgb paramRgb = {gPixelCommand[6], gPixelCommand[7], gPixelCommand[8]};
  const uint16_t durationMs = littleEndianU16(gPixelCommand[9], gPixelCommand[10]);
  const uint8_t animationId =
      gPixelCommand[kRegisterPixelAnimationId - kRegisterPixelCommandStart];
  const unsigned long now = millis();

  if (animationId == kPixelAnimationCandleFlicker) {
    for (uint8_t line = 0; line < kPixelLineCount; ++line) {
      if ((targetMask & static_cast<uint8_t>(1u << line)) == 0) {
        continue;
      }
      PixelAnimation& animation = gPixelAnimations[line];
      const bool updatingCandle = animation.active;
      animation = {
          .active = true,
          .lineMask = static_cast<uint8_t>(1u << line),
          .startIndex = startIndex,
          .count = count,
          .baseRgb = baseRgb,
          .hueVariation = paramRgb.r,
          .seed = static_cast<uint8_t>(paramRgb.g + line),
          .startIntensity = static_cast<uint8_t>(
              updatingCandle ? currentCandleIntensity(animation, now) : 0),
          .targetIntensity = paramRgb.b,
          .rampStartMs = now,
          .rampDurationMs = durationMs,
          .nextFrameMs = now,
          .frameCounter =
              static_cast<uint16_t>(updatingCandle ? animation.frameCounter : 0),
      };
      applyCandleFrame(animation, now);
    }
  } else if (animationId == kPixelAnimationStatic) {
    for (uint8_t line = 0; line < kPixelLineCount; ++line) {
      if ((targetMask & static_cast<uint8_t>(1u << line)) != 0) {
        gPixelAnimations[line].active = false;
      }
    }
    applyStaticPixels(targetMask, startIndex, count, baseRgb);
  } else {
    Serial.print("Unsupported pixel animation id=");
    Serial.println(animationId);
    gPixelCommand[kRegisterPixelTrigger - kRegisterPixelCommandStart] = 0;
    return false;
  }

  for (uint8_t line = 0; line < kPixelLineCount; ++line) {
    if ((targetMask & static_cast<uint8_t>(1u << line)) == 0) {
      continue;
    }

    PixelLineState& pixelLine = gState.pixelLines[line];
    pixelLine.active =
        animationId == kPixelAnimationCandleFlicker ? paramRgb.b > 0
                                                    : maxRgbChannel(baseRgb) > 0;
    pixelLine.animationId = animationId;
    pixelLine.start = static_cast<uint8_t>(startIndex);
    pixelLine.count = static_cast<uint8_t>(count);
    pixelLine.startRgb = baseRgb;
    pixelLine.endRgb = paramRgb;
    pixelLine.durationMs = durationMs;
    ++pixelLine.updateCount;
  }

  gPixelCommand[kRegisterPixelTrigger - kRegisterPixelCommandStart] = 0;
  Serial.print("Pixel animation started, id=");
  Serial.print(animationId);
  Serial.print(" lines=0b");
  printBinaryNibble(targetMask);
  Serial.print(" start=");
  Serial.print(startIndex);
  Serial.print(" count=");
  Serial.print(count);
  Serial.print(" durationMs=");
  Serial.println(durationMs);
  return true;
}

bool applyRegisterWrite(uint8_t registerAddress, const uint8_t* values,
                        uint8_t valueCount) {
  if (valueCount == 0) {
    return false;
  }

  switch (registerAddress) {
    case kRegisterRailState: {
      forceRailsEnabled();
      return false;
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
    default:
      break;
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

  return applyRegisterWrite(command.bytes[0], &command.bytes[1],
                            command.size - 1);
}

bool applyRelayCommand(uint8_t relayMask) {
  ++gState.receivedCommandCount;
  const bool changed = gState.relayMask != relayMask;
  gState.relayMask = relayMask;
  if (!applyRelayOutputs(gState.relayMask)) {
    Serial.println("TCA9534 write FAILED while applying relay state");
  }
  return changed;
}

bool applyQueuedRelayCommands() {
  bool changed = false;
  uint8_t relayMask = 0;
  while (popRelayCommand(relayMask)) {
    changed = applyRelayCommand(relayMask) || changed;
  }
  return changed;
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
  const uint32_t droppedRelays = gDroppedRelayCount;
  interrupts();
  Serial.print(dropped);
  Serial.print(" relay_dropped=");
  Serial.print(droppedRelays);

  Serial.print(" rails=0b");
  printBinaryNibble(gState.railMask);
  Serial.print(" relays=0b");
  printBinaryNibble(gState.relayMask);
  Serial.print(" pixels=[");
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

  configureRailOutputs();
  forceRailsEnabled();
  initializePixelLines();

  Wire.setSDA(kPeripheralI2cSdaPin);
  Wire.setSCL(kPeripheralI2cSclPin);
  Wire.begin();
  initializeTca9534();
  applyRelayOutputs(gState.relayMask);
  rebuildStatusSnapshot();

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
  const unsigned long now = millis();
  if (now - gLastRailReassertMs >= kRailReassertMs) {
    forceRailsEnabled();
    gLastRailReassertMs = now;
  }

  bool changed = applyQueuedRelayCommands();

  I2cCommand command;
  while (popCommand(command)) {
    changed = processCommand(command) || changed;
  }

  changed = applyQueuedRelayCommands() || changed;
  changed = updatePixelAnimations() || changed;
  changed = applyQueuedRelayCommands() || changed;

  if (changed) {
    rebuildStatusSnapshot();
    printStateSummary();
  }
}
