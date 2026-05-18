#include <Arduino.h>
#include <Adafruit_PWMServoDriver.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>

namespace {
constexpr uint8_t kNeoPixelPin = 16;
constexpr uint16_t kStatusPixelCount = 1;
constexpr uint16_t kPixelsPerRail = 100;
constexpr uint8_t kPixelRailCount = 4;

constexpr uint8_t kI2c0SdaPin = 4;
constexpr uint8_t kI2c0SclPin = 5;
constexpr uint8_t kI2c1SdaPin = 14;
constexpr uint8_t kI2c1SclPin = 15;
constexpr uint8_t kPiControlAddress = 0x12;
constexpr uint8_t k12vCAlarmPin = 6;
constexpr uint8_t k12vCEnablePin = 9;
constexpr uint8_t k12vBEnablePin = 10;
constexpr uint8_t k12vAEnablePin = 11;
constexpr uint8_t k8vEnablePin = 12;
constexpr uint8_t k8vAlarmPin = 13;

constexpr uint8_t kPixel4Pin = 26;
constexpr uint8_t kPixel3Pin = 27;
constexpr uint8_t kPixel2Pin = 28;
constexpr uint8_t kPixel1Pin = 29;

constexpr uint8_t kRailEnablePins[] = {
    k12vCEnablePin,
    k12vBEnablePin,
    k12vAEnablePin,
};

constexpr uint8_t kAlarmInputPins[] = {
    k12vCAlarmPin,
    k8vAlarmPin,
};

constexpr uint8_t kPca9685Address = 0x43;
constexpr uint8_t kTca9534Address = 0x20;

constexpr uint8_t kTca9534OutputPortRegister = 0x01;
constexpr uint8_t kTca9534PolarityRegister = 0x02;
constexpr uint8_t kTca9534ConfigRegister = 0x03;

constexpr unsigned long kSerialBaudRate = 115200;
constexpr uint16_t kEnablePulseMs = 250;
constexpr uint16_t kSolenoidPulseMs = 150;
constexpr uint8_t kProtocolVersion = 2;

// Quick bring-up helper: each enabled PCA9685 channel is swept one at a time.
// Set to false to return to normal Pi-controlled servo behavior.
constexpr bool kServoProofOfConceptEnabled = false;
constexpr uint8_t kServoProofOfConceptMask = 0xFF;
constexpr uint16_t kServoProofOfConceptStepMs = 700;

constexpr uint8_t kRailBit12vA = 1u << 0;
constexpr uint8_t kRailBit12vB = 1u << 1;
constexpr uint8_t kRailBit12vC = 1u << 2;
constexpr uint8_t kRailBit8v = 1u << 3;
constexpr uint8_t kFixedRailState = kRailBit12vB | kRailBit12vC | kRailBit8v;
constexpr uint8_t kSolenoidMask = 0x0F;

constexpr uint8_t kRegisterProtocolVersion = 0x00;
constexpr uint8_t kRegisterRailState = 0x01;
constexpr uint8_t kRegisterSolenoidState = 0x02;
constexpr uint8_t kRegisterAlarmState = 0x03;
constexpr uint8_t kRegisterInaPresence = 0x04;
constexpr uint8_t kRegisterServoEnableMask = 0x05;
constexpr uint8_t kRegisterServo0Value = 0x10;
constexpr uint8_t kRegisterServo7Value = 0x17;
constexpr uint8_t kRegisterIna0VoltageLow = 0x20;
constexpr uint8_t kRegisterIna0VoltageHigh = 0x21;
constexpr uint8_t kRegisterIna0CurrentLow = 0x22;
constexpr uint8_t kRegisterIna0CurrentHigh = 0x23;
constexpr uint8_t kRegisterIna1VoltageLow = 0x24;
constexpr uint8_t kRegisterIna1VoltageHigh = 0x25;
constexpr uint8_t kRegisterIna1CurrentLow = 0x26;
constexpr uint8_t kRegisterIna1CurrentHigh = 0x27;
constexpr uint8_t kRegisterPixelCommandStart = 0x40;
constexpr uint8_t kRegisterPixelRailMask = 0x40;
constexpr uint8_t kRegisterPixelStartIndex = 0x41;
constexpr uint8_t kRegisterPixelCount = 0x42;
constexpr uint8_t kRegisterPixelFromRed = 0x43;
constexpr uint8_t kRegisterPixelFromGreen = 0x44;
constexpr uint8_t kRegisterPixelFromBlue = 0x45;
constexpr uint8_t kRegisterPixelToRed = 0x46;
constexpr uint8_t kRegisterPixelToGreen = 0x47;
constexpr uint8_t kRegisterPixelToBlue = 0x48;
constexpr uint8_t kRegisterPixelDurationLow = 0x49;
constexpr uint8_t kRegisterPixelDurationHigh = 0x4A;
constexpr uint8_t kRegisterPixelTrigger = 0x4B;
constexpr uint8_t kRegisterPixelAnimationId = 0x4C;
constexpr uint8_t kRegisterPixelCommandEnd = kRegisterPixelAnimationId;
constexpr uint8_t kRegisterStatusSnapshotStart = 0x60;
constexpr uint8_t kStatusSnapshotByteCount = 24;
constexpr uint8_t kRegisterStatusSnapshotEnd =
    kRegisterStatusSnapshotStart + kStatusSnapshotByteCount - 1;
constexpr uint8_t kStatusSnapshotChecksumIndex = kStatusSnapshotByteCount - 1;

constexpr uint8_t kPixelAnimationFade = 0;
constexpr uint8_t kPixelAnimationCandleFlicker = 1;
constexpr uint8_t kPixelAnimationLightningStrike = 2;
constexpr unsigned long kCandleFlickerFrameMs = 45;
constexpr unsigned long kLightningStrikeFrameMs = 35;

constexpr uint8_t kServoChannelCount = 8;
constexpr uint16_t kServoPulseMinTicks = 205;
constexpr uint16_t kServoPulseMaxTicks = 410;
constexpr uint8_t kPixelCommandByteCount =
    kRegisterPixelCommandEnd - kRegisterPixelCommandStart + 1;

struct PixelColor {
  uint8_t red;
  uint8_t green;
  uint8_t blue;
};

struct PixelAnimation {
  bool active;
  uint8_t railMask;
  uint16_t startIndex;
  uint16_t count;
  PixelColor fromColor;
  PixelColor toColor;
  unsigned long startMs;
  unsigned long nextFrameMs;
  uint16_t durationMs;
  uint8_t animationId;
  uint8_t hueVariation;
  uint8_t seed;
  uint8_t startIntensity;
  uint8_t targetIntensity;
  unsigned long rampStartMs;
  uint16_t rampDurationMs;
  uint16_t randomState;
  uint16_t frameCounter;
};

volatile uint8_t gRequestedRailState = kFixedRailState;
volatile uint8_t gRequestedSolenoidState = 0x00;
volatile uint8_t gRequestedServoEnableMask = 0x00;
volatile uint8_t gRequestedServoValues[kServoChannelCount] = {127, 127, 127, 127,
                                                              127, 127, 127, 127};
volatile uint8_t gRequestedPixelCommand[kPixelCommandByteCount] = {};
volatile uint8_t gLastRegisterPointer = kRegisterProtocolVersion;
volatile bool gPendingControlApply = false;
volatile bool gPendingPixelCommand = false;
volatile uint8_t gStatusSnapshot[kStatusSnapshotByteCount] = {};
uint8_t gStatusSnapshotSequence = 0;
uint8_t gAppliedRailState = 0x00;
uint8_t gAppliedSolenoidState = 0x00;
uint8_t gAppliedServoEnableMask = 0x00;
uint8_t gAppliedServoValues[kServoChannelCount] = {127, 127, 127, 127,
                                                   127, 127, 127, 127};
uint16_t gInaVoltageMillivolts[2] = {0, 0};
int16_t gInaCurrentMilliamps[2] = {0, 0};
PixelAnimation gPixelAnimation = {};
unsigned long gLastServoProofOfConceptStepMs = 0;
uint8_t gServoProofOfConceptStep = 0;
bool gPca9685Present = false;

Adafruit_NeoPixel pixel(kStatusPixelCount, kNeoPixelPin, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel pixelRails[kPixelRailCount] = {
    Adafruit_NeoPixel(kPixelsPerRail, kPixel1Pin, NEO_GRB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerRail, kPixel2Pin, NEO_GRB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerRail, kPixel3Pin, NEO_GRB + NEO_KHZ800),
    Adafruit_NeoPixel(kPixelsPerRail, kPixel4Pin, NEO_GRB + NEO_KHZ800),
};
Adafruit_PWMServoDriver servoDriver(kPca9685Address, Wire);

bool writeI2cRegisters(uint8_t address, uint8_t startReg, const uint8_t* data,
                       size_t length);

void showColor(uint32_t color) {
  pixel.setPixelColor(0, color);
  pixel.show();
}

void set12vARailEnabled(bool enabled) {
  digitalWrite(k12vAEnablePin, enabled ? HIGH : LOW);
}

void set12vBRailEnabled(bool enabled) {
  digitalWrite(k12vBEnablePin, enabled ? HIGH : LOW);
}

void set12vCRailEnabled(bool enabled) {
  digitalWrite(k12vCEnablePin, enabled ? HIGH : LOW);
}

void set8vRailEnabled(bool enabled) {
  digitalWrite(k8vEnablePin, enabled ? LOW : HIGH);
}

void configureOutputsLow(const uint8_t* pins, size_t count) {
  for (size_t index = 0; index < count; ++index) {
    pinMode(pins[index], OUTPUT);
    digitalWrite(pins[index], LOW);
  }
}

void configureInputs(const uint8_t* pins, size_t count) {
  for (size_t index = 0; index < count; ++index) {
    pinMode(pins[index], INPUT);
  }
}

uint32_t railColor(Adafruit_NeoPixel& rail, const PixelColor& color) {
  return rail.Color(color.red, color.green, color.blue);
}

PixelColor interpolateColor(const PixelColor& fromColor,
                            const PixelColor& toColor, uint8_t progress) {
  auto interpolate = [progress](uint8_t start, uint8_t end) -> uint8_t {
    const int16_t delta = static_cast<int16_t>(end) - static_cast<int16_t>(start);
    return static_cast<uint8_t>(static_cast<int16_t>(start) +
                                ((delta * progress) / 255));
  };

  return {
      .red = interpolate(fromColor.red, toColor.red),
      .green = interpolate(fromColor.green, toColor.green),
      .blue = interpolate(fromColor.blue, toColor.blue),
  };
}

uint8_t maxColorChannel(const PixelColor& color) {
  return max<uint8_t>(color.red, max<uint8_t>(color.green, color.blue));
}

uint8_t minColorChannel(const PixelColor& color) {
  return min<uint8_t>(color.red, min<uint8_t>(color.green, color.blue));
}

uint8_t rgbHue(const PixelColor& color) {
  const uint8_t maxChannel = maxColorChannel(color);
  const uint8_t minChannel = minColorChannel(color);
  const uint8_t delta = maxChannel - minChannel;

  if (delta == 0) {
    return 0;
  }

  int16_t hue = 0;
  if (maxChannel == color.red) {
    hue = (43 * (static_cast<int16_t>(color.green) -
                 static_cast<int16_t>(color.blue))) /
          delta;
  } else if (maxChannel == color.green) {
    hue = 85 + (43 * (static_cast<int16_t>(color.blue) -
                      static_cast<int16_t>(color.red))) /
                     delta;
  } else {
    hue = 171 + (43 * (static_cast<int16_t>(color.red) -
                       static_cast<int16_t>(color.green))) /
                      delta;
  }

  if (hue < 0) {
    hue += 255;
  }
  return static_cast<uint8_t>(hue);
}

uint8_t rgbSaturation(const PixelColor& color) {
  const uint8_t maxChannel = maxColorChannel(color);
  if (maxChannel == 0) {
    return 0;
  }

  return static_cast<uint8_t>(
      ((maxChannel - minColorChannel(color)) * 255u) / maxChannel);
}

PixelColor hsvColor(uint8_t hue, uint8_t saturation, uint8_t value) {
  const uint32_t packed =
      Adafruit_NeoPixel::ColorHSV(static_cast<uint16_t>(hue) * 257u,
                                  saturation, value);
  const uint32_t rgb = Adafruit_NeoPixel::gamma32(packed);
  return {
      .red = static_cast<uint8_t>((rgb >> 16) & 0xFF),
      .green = static_cast<uint8_t>((rgb >> 8) & 0xFF),
      .blue = static_cast<uint8_t>(rgb & 0xFF),
  };
}

uint8_t nextAnimationRandomByte(PixelAnimation& animation) {
  animation.randomState =
      static_cast<uint16_t>((animation.randomState * 2053u) + 13849u);
  return static_cast<uint8_t>(animation.randomState >> 8);
}

uint8_t animationHashByte(uint8_t seed, uint8_t railIndex, uint16_t pixelIndex,
                          uint16_t frameCounter, uint8_t salt) {
  uint16_t value =
      static_cast<uint16_t>((static_cast<uint16_t>(seed) << 8) |
                            (railIndex * 41u));
  value ^= static_cast<uint16_t>(pixelIndex * 109u);
  value ^= static_cast<uint16_t>(frameCounter * 251u);
  value ^= static_cast<uint16_t>(salt * 199u);
  value ^= static_cast<uint16_t>(value << 7);
  value ^= static_cast<uint16_t>(value >> 9);
  value = static_cast<uint16_t>((value * 2053u) + 13849u);
  return static_cast<uint8_t>(value >> 8);
}

uint8_t scaleColorChannel(uint8_t channel, uint8_t scale) {
  return static_cast<uint8_t>((static_cast<uint16_t>(channel) * scale) / 255u);
}

uint8_t interpolateByte(uint8_t start, uint8_t end, uint8_t progress) {
  const int16_t delta = static_cast<int16_t>(end) - static_cast<int16_t>(start);
  return static_cast<uint8_t>(static_cast<int16_t>(start) +
                              ((delta * progress) / 255));
}

void fillPixelRange(uint8_t railIndex, uint16_t startIndex, uint16_t count,
                    const PixelColor& color) {
  if (railIndex >= kPixelRailCount || startIndex >= kPixelsPerRail) {
    return;
  }

  const uint16_t endIndex =
      min<uint16_t>(kPixelsPerRail, static_cast<uint16_t>(startIndex + count));
  const uint32_t packedColor = railColor(pixelRails[railIndex], color);

  for (uint16_t pixelIndex = startIndex; pixelIndex < endIndex; ++pixelIndex) {
    pixelRails[railIndex].setPixelColor(pixelIndex, packedColor);
  }
}

void showTargetedPixelRails(uint8_t railMask) {
  for (uint8_t railIndex = 0; railIndex < kPixelRailCount; ++railIndex) {
    if ((railMask & static_cast<uint8_t>(1u << railIndex)) != 0) {
      pixelRails[railIndex].show();
    }
  }
}

void applyPixelColorToTargets(uint8_t railMask, uint16_t startIndex,
                              uint16_t count, const PixelColor& color) {
  for (uint8_t railIndex = 0; railIndex < kPixelRailCount; ++railIndex) {
    if ((railMask & static_cast<uint8_t>(1u << railIndex)) != 0) {
      fillPixelRange(railIndex, startIndex, count, color);
    }
  }
  showTargetedPixelRails(railMask);
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

void applyCandleFrame(PixelAnimation& animation, unsigned long now) {
  const uint8_t intensity = currentCandleIntensity(animation, now);
  const uint8_t baseHue = rgbHue(animation.fromColor);
  const uint8_t saturation = rgbSaturation(animation.fromColor);
  const uint8_t baseValue = maxColorChannel(animation.fromColor);
  const uint16_t endIndex =
      min<uint16_t>(kPixelsPerRail,
                    static_cast<uint16_t>(animation.startIndex +
                                          animation.count));

  for (uint8_t railIndex = 0; railIndex < kPixelRailCount; ++railIndex) {
    if ((animation.railMask & static_cast<uint8_t>(1u << railIndex)) == 0) {
      continue;
    }

    for (uint16_t pixelIndex = animation.startIndex; pixelIndex < endIndex;
         ++pixelIndex) {
      const int16_t variation =
          (static_cast<int16_t>(animationHashByte(
               animation.seed, railIndex, pixelIndex, animation.frameCounter,
               3)) -
           127) *
          static_cast<int16_t>(animation.hueVariation) / 255;
      const uint8_t hue = static_cast<uint8_t>(baseHue + variation);
      const uint8_t brightnessNoise =
          animationHashByte(animation.seed, railIndex, pixelIndex,
                            animation.frameCounter, 17);
      const uint8_t brightnessScale =
          static_cast<uint8_t>(145u + ((brightnessNoise * 110u) / 255u));
      const uint8_t value =
          scaleColorChannel(scaleColorChannel(baseValue, brightnessScale),
                            intensity);
      const PixelColor color = hsvColor(hue, saturation, value);
      pixelRails[railIndex].setPixelColor(pixelIndex,
                                          railColor(pixelRails[railIndex],
                                                    color));
    }
  }

  showTargetedPixelRails(animation.railMask);
  ++animation.frameCounter;
}

void initializePixelRails() {
  for (uint8_t railIndex = 0; railIndex < kPixelRailCount; ++railIndex) {
    pixelRails[railIndex].begin();
    pixelRails[railIndex].setBrightness(64);
    pixelRails[railIndex].clear();
    pixelRails[railIndex].show();
  }
}

void flashPixelRail(uint8_t railIndex, const PixelColor& color) {
  fillPixelRange(railIndex, 0, kPixelsPerRail, color);
  pixelRails[railIndex].show();
  delay(150);
  pixelRails[railIndex].clear();
  pixelRails[railIndex].show();
  delay(75);
}

void runPixelRailSelfTest() {
  constexpr PixelColor kRed = {.red = 255, .green = 0, .blue = 0};
  constexpr PixelColor kGreen = {.red = 0, .green = 255, .blue = 0};
  constexpr PixelColor kBlue = {.red = 0, .green = 0, .blue = 255};

  Serial.println("Pixel rail RGB self-test start");
  for (uint8_t railIndex = 0; railIndex < kPixelRailCount; ++railIndex) {
    Serial.print("Testing PIX_");
    Serial.println(railIndex + 1);
    flashPixelRail(railIndex, kRed);
    flashPixelRail(railIndex, kGreen);
    flashPixelRail(railIndex, kBlue);
  }
  Serial.println("Pixel rail RGB self-test complete");
}

void updatePixelAnimation() {
  if (!gPixelAnimation.active) {
    return;
  }

  const unsigned long now = millis();
  const unsigned long elapsed = now - gPixelAnimation.startMs;

  if (gPixelAnimation.animationId == kPixelAnimationCandleFlicker) {
    if (now < gPixelAnimation.nextFrameMs) {
      return;
    }
    gPixelAnimation.nextFrameMs = now + kCandleFlickerFrameMs;
    applyCandleFrame(gPixelAnimation, now);
    return;
  }

  if (gPixelAnimation.animationId == kPixelAnimationLightningStrike) {
    if (now < gPixelAnimation.nextFrameMs) {
      return;
    }
    gPixelAnimation.nextFrameMs = now + kLightningStrikeFrameMs;

    const uint16_t durationMs =
        gPixelAnimation.durationMs == 0 ? 850 : gPixelAnimation.durationMs;
    if (elapsed >= durationMs) {
      gPixelAnimation.active = false;
      applyPixelColorToTargets(gPixelAnimation.railMask,
                               gPixelAnimation.startIndex,
                               gPixelAnimation.count,
                               gPixelAnimation.fromColor);
      return;
    }

    uint8_t flashScale = 0;
    if (elapsed < 70 || (elapsed >= 145 && elapsed < 210) ||
        (elapsed >= 275 && elapsed < 320)) {
      flashScale = static_cast<uint8_t>(
          205u + ((nextAnimationRandomByte(gPixelAnimation) * 50u) / 255u));
    } else if (elapsed < 420) {
      flashScale = static_cast<uint8_t>(
          80u + ((nextAnimationRandomByte(gPixelAnimation) * 80u) / 255u));
    } else {
      const uint16_t tailElapsed = elapsed - 420;
      const uint16_t tailDuration = max<uint16_t>(1, durationMs - 420);
      flashScale =
          static_cast<uint8_t>(max<int16_t>(0, 70 - ((tailElapsed * 70) /
                                                     tailDuration)));
    }

    const PixelColor flashColor =
        maxColorChannel(gPixelAnimation.toColor) == 0
            ? PixelColor{.red = 220, .green = 235, .blue = 255}
            : gPixelAnimation.toColor;
    const PixelColor color =
        interpolateColor(gPixelAnimation.fromColor, flashColor, flashScale);

    applyPixelColorToTargets(gPixelAnimation.railMask,
                             gPixelAnimation.startIndex,
                             gPixelAnimation.count,
                             color);
    return;
  }

  const uint8_t progress =
      (gPixelAnimation.durationMs == 0 || elapsed >= gPixelAnimation.durationMs)
          ? 255
          : static_cast<uint8_t>((elapsed * 255UL) / gPixelAnimation.durationMs);

  const PixelColor color =
      interpolateColor(gPixelAnimation.fromColor, gPixelAnimation.toColor,
                       progress);
  applyPixelColorToTargets(gPixelAnimation.railMask, gPixelAnimation.startIndex,
                           gPixelAnimation.count, color);

  if (progress == 255) {
    gPixelAnimation.active = false;
  }
}

bool writeI2cRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool isI2cDeviceAtAddress(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

uint16_t mapServoValueToTicks(uint8_t value) {
  const uint16_t span = kServoPulseMaxTicks - kServoPulseMinTicks;
  return static_cast<uint16_t>(kServoPulseMinTicks +
                               ((static_cast<uint32_t>(span) * value) / 255u));
}

bool setPca9685ChannelPwm(uint8_t channel, uint16_t onCount, uint16_t offCount) {
  if (!gPca9685Present) {
    return false;
  }

  servoDriver.setPWM(channel, onCount, offCount);
  return true;
}

bool setPca9685ChannelOff(uint8_t channel) {
  if (!gPca9685Present) {
    return false;
  }

  servoDriver.setPWM(channel, 0, 4096);
  return true;
}

bool writeI2cRegisters(uint8_t address, uint8_t startReg, const uint8_t* data,
                       size_t length) {
  Wire.beginTransmission(address);
  Wire.write(startReg);
  Wire.write(data, length);
  return Wire.endTransmission() == 0;
}

bool readI2cRegisters(uint8_t address, uint8_t startReg, uint8_t* data,
                      size_t length) {
  Wire.beginTransmission(address);
  Wire.write(startReg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  const size_t bytesRead =
      Wire.requestFrom(address, static_cast<uint8_t>(length));
  if (bytesRead != length) {
    while (Wire.available()) {
      Wire.read();
    }
    return false;
  }

  for (size_t index = 0; index < length; ++index) {
    data[index] = Wire.read();
  }
  return true;
}

void scanI2cBus(TwoWire& bus, const char* label) {
  Serial.print(label);
  Serial.println(" scan start");

  for (uint8_t address = 0x03; address < 0x78; ++address) {
    bus.beginTransmission(address);
    if (bus.endTransmission() == 0) {
      Serial.print("  found device at 0x");
      if (address < 0x10) {
        Serial.print('0');
      }
      Serial.println(address, HEX);
    }
  }

  Serial.print(label);
  Serial.println(" scan complete");
}

void initializePca9685() {
  gPca9685Present = isI2cDeviceAtAddress(kPca9685Address);
  if (!gPca9685Present) {
    Serial.println("PCA9685 init: FAILED, no device at 0x43");
    return;
  }

  servoDriver.begin();
  servoDriver.setOscillatorFrequency(27000000);
  servoDriver.setPWMFreq(50);
  delay(10);

  bool channelsOk = true;
  for (uint8_t channel = 0; channel < kServoChannelCount; ++channel) {
    channelsOk = setPca9685ChannelOff(channel) && channelsOk;
  }

  Serial.print("PCA9685 init: ");
  Serial.println(channelsOk ? "OK" : "FAILED");
}

void initializeTca9534() {
  constexpr uint8_t kSolenoidOutputsLow = 0x00;
  constexpr uint8_t kPolarityNormal = 0x00;
  constexpr uint8_t kLowerNibbleOutputs = 0xF0;

  const bool outputOk = writeI2cRegister(kTca9534Address,
                                         kTca9534OutputPortRegister,
                                         kSolenoidOutputsLow);
  const bool polarityOk = writeI2cRegister(kTca9534Address,
                                           kTca9534PolarityRegister,
                                           kPolarityNormal);
  const bool configOk = writeI2cRegister(kTca9534Address, kTca9534ConfigRegister,
                                         kLowerNibbleOutputs);

  Serial.print("TCA9534 init: ");
  Serial.println((outputOk && polarityOk && configOk) ? "OK" : "FAILED");
}

bool writeTca9534Outputs(uint8_t value) {
  return writeI2cRegister(kTca9534Address, kTca9534OutputPortRegister, value);
}

void applyRailState(uint8_t railState) {
  (void)railState;
  set12vARailEnabled(false);
  set12vBRailEnabled(true);
  set12vCRailEnabled(true);
  set8vRailEnabled(true);
  gAppliedRailState = kFixedRailState;
}

void applySolenoidState(uint8_t solenoidState) {
  const uint8_t maskedState = solenoidState & kSolenoidMask;
  if (writeTca9534Outputs(maskedState)) {
    gAppliedSolenoidState = maskedState;
  } else {
    Serial.println("TCA9534 write FAILED while applying Pi command");
  }
}

void applyServoState(uint8_t servoEnableMask, const uint8_t* servoValues) {
  bool allOk = true;

  for (uint8_t channel = 0; channel < kServoChannelCount; ++channel) {
    bool channelOk = false;
    if ((servoEnableMask & static_cast<uint8_t>(1u << channel)) != 0) {
      const uint16_t pulseTicks = mapServoValueToTicks(servoValues[channel]);
      channelOk = setPca9685ChannelPwm(channel, 0, pulseTicks);
    } else {
      channelOk = setPca9685ChannelOff(channel);
    }

    allOk = channelOk && allOk;
    if (channelOk) {
      gAppliedServoValues[channel] = servoValues[channel];
    }
  }

  if (allOk) {
    gAppliedServoEnableMask = servoEnableMask;
  } else {
    Serial.println("PCA9685 write FAILED while applying Pi command");
  }
}

void applyPendingPixelCommand() {
  if (!gPendingPixelCommand) {
    return;
  }

  uint8_t command[kPixelCommandByteCount] = {};
  noInterrupts();
  for (uint8_t index = 0; index < kPixelCommandByteCount; ++index) {
    command[index] = gRequestedPixelCommand[index];
  }
  gPendingPixelCommand = false;
  interrupts();

  const uint8_t railMask =
      command[kRegisterPixelRailMask - kRegisterPixelCommandStart] & 0x0F;
  const uint16_t startIndex =
      min<uint16_t>(command[kRegisterPixelStartIndex - kRegisterPixelCommandStart],
                    kPixelsPerRail - 1);
  uint16_t count = command[kRegisterPixelCount - kRegisterPixelCommandStart];
  if (count == 0 || startIndex + count > kPixelsPerRail) {
    count = kPixelsPerRail - startIndex;
  }

  const PixelColor fromColor = {
      .red = command[kRegisterPixelFromRed - kRegisterPixelCommandStart],
      .green = command[kRegisterPixelFromGreen - kRegisterPixelCommandStart],
      .blue = command[kRegisterPixelFromBlue - kRegisterPixelCommandStart],
  };
  const PixelColor toColor = {
      .red = command[kRegisterPixelToRed - kRegisterPixelCommandStart],
      .green = command[kRegisterPixelToGreen - kRegisterPixelCommandStart],
      .blue = command[kRegisterPixelToBlue - kRegisterPixelCommandStart],
  };
  const uint16_t durationMs =
      static_cast<uint16_t>(
          command[kRegisterPixelDurationLow - kRegisterPixelCommandStart]) |
      (static_cast<uint16_t>(
           command[kRegisterPixelDurationHigh - kRegisterPixelCommandStart])
       << 8);
  const uint8_t animationId =
      command[kRegisterPixelAnimationId - kRegisterPixelCommandStart];

  if (railMask == 0) {
    return;
  }

  if (animationId > kPixelAnimationLightningStrike) {
    Serial.print("Unsupported pixel animation id=");
    Serial.println(animationId);
    return;
  }

  const unsigned long now = millis();
  const bool updatingCandle =
      animationId == kPixelAnimationCandleFlicker && gPixelAnimation.active &&
      gPixelAnimation.animationId == kPixelAnimationCandleFlicker;
  const uint8_t candleStartIntensity =
      updatingCandle ? currentCandleIntensity(gPixelAnimation, now) : 0;
  const uint16_t candleFrameCounter =
      updatingCandle ? gPixelAnimation.frameCounter : 0;

  gPixelAnimation = {
      .active = true,
      .railMask = railMask,
      .startIndex = startIndex,
      .count = count,
      .fromColor = fromColor,
      .toColor = toColor,
      .startMs = now,
      .nextFrameMs = 0,
      .durationMs = durationMs,
      .animationId = animationId,
      .hueVariation = toColor.red,
      .seed = toColor.green,
      .startIntensity = static_cast<uint8_t>(
          animationId == kPixelAnimationCandleFlicker ? candleStartIntensity : 0),
      .targetIntensity = static_cast<uint8_t>(
          animationId == kPixelAnimationCandleFlicker ? toColor.blue : 0),
      .rampStartMs = now,
      .rampDurationMs = static_cast<uint16_t>(
          animationId == kPixelAnimationCandleFlicker ? durationMs : 0),
      .randomState =
          static_cast<uint16_t>((static_cast<uint16_t>(toColor.green) << 8) |
                                (startIndex ^ count ^ railMask)),
      .frameCounter = static_cast<uint16_t>(
          animationId == kPixelAnimationCandleFlicker ? candleFrameCounter : 0),
  };

  if (animationId == kPixelAnimationCandleFlicker) {
    applyCandleFrame(gPixelAnimation, now);
  } else {
    applyPixelColorToTargets(railMask, startIndex, count, fromColor);
  }

  Serial.print("Pixel animation started, id=");
  Serial.print(animationId);
  Serial.print(" rails=0b");
  Serial.print(railMask, BIN);
  Serial.print(" start=");
  Serial.print(startIndex);
  Serial.print(" count=");
  Serial.print(count);
  Serial.print(" durationMs=");
  Serial.println(durationMs);
}

void applyPendingControlState() {
  if (!gPendingControlApply) {
    return;
  }

  noInterrupts();
  const uint8_t requestedRailState = gRequestedRailState;
  const uint8_t requestedSolenoidState = gRequestedSolenoidState;
  const uint8_t requestedServoEnableMask = gRequestedServoEnableMask;
  uint8_t requestedServoValues[kServoChannelCount] = {};
  for (uint8_t index = 0; index < kServoChannelCount; ++index) {
    requestedServoValues[index] = gRequestedServoValues[index];
  }
  gPendingControlApply = false;
  interrupts();

  applyRailState(requestedRailState);
  applySolenoidState(requestedSolenoidState);
  applyServoState(requestedServoEnableMask, requestedServoValues);

  Serial.print("Pi control applied, rails=0b");
  Serial.print(gAppliedRailState, BIN);
  Serial.print(" solenoids=0b");
  Serial.print(gAppliedSolenoidState, BIN);
  Serial.print(" servos=0b");
  Serial.println(gAppliedServoEnableMask, BIN);
}

void runServoProofOfConcept() {
  if (!kServoProofOfConceptEnabled) {
    return;
  }

  const unsigned long now = millis();
  if (now - gLastServoProofOfConceptStepMs < kServoProofOfConceptStepMs) {
    return;
  }
  gLastServoProofOfConceptStepMs = now;

  constexpr uint8_t kPositions[] = {32, 127, 224, 127};
  constexpr uint8_t kPositionCount = sizeof(kPositions) / sizeof(kPositions[0]);
  const uint8_t rawChannel = gServoProofOfConceptStep / kPositionCount;
  const uint8_t positionIndex = gServoProofOfConceptStep % kPositionCount;
  const uint8_t channel = rawChannel % kServoChannelCount;
  const uint8_t enableMask = static_cast<uint8_t>(1u << channel);
  const uint8_t enabledChannelMask = enableMask & kServoProofOfConceptMask;

  uint8_t servoValues[kServoChannelCount] = {};
  for (uint8_t index = 0; index < kServoChannelCount; ++index) {
    servoValues[index] = 127;
  }
  servoValues[channel] = kPositions[positionIndex];

  applyServoState(enabledChannelMask, servoValues);

  Serial.print("Servo POC channel=");
  Serial.print(channel);
  Serial.print(" value=");
  Serial.print(servoValues[channel]);
  Serial.print(" enableMask=0b");
  Serial.println(enabledChannelMask, BIN);

  ++gServoProofOfConceptStep;
}

uint8_t getAlarmStateBits() {
  uint8_t alarmState = 0;
  if (digitalRead(k12vCAlarmPin) == HIGH) {
    alarmState |= 1u << 0;
  }
  if (digitalRead(k8vAlarmPin) == HIGH) {
    alarmState |= 1u << 1;
  }
  return alarmState;
}

uint8_t getInaPresenceBits() {
  return 0;
}

uint8_t snapshotChecksum(const uint8_t* snapshot) {
  uint8_t checksum = 0;
  for (uint8_t index = 0; index < kStatusSnapshotChecksumIndex; ++index) {
    checksum = static_cast<uint8_t>(checksum + snapshot[index]);
  }
  return checksum;
}

void writeU16LittleEndian(uint8_t* snapshot, uint8_t offset, uint16_t value) {
  snapshot[offset] = static_cast<uint8_t>(value & 0xFF);
  snapshot[offset + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

void writeI16LittleEndian(uint8_t* snapshot, uint8_t offset, int16_t value) {
  writeU16LittleEndian(snapshot, offset, static_cast<uint16_t>(value));
}

void rebuildStatusSnapshot() {
  uint8_t snapshot[kStatusSnapshotByteCount] = {};
  snapshot[0] = kProtocolVersion;
  snapshot[1] = ++gStatusSnapshotSequence;
  snapshot[2] = gAppliedRailState;
  snapshot[3] = gAppliedSolenoidState;
  snapshot[4] = getAlarmStateBits();
  snapshot[5] = getInaPresenceBits();
  snapshot[6] = gAppliedServoEnableMask;
  for (uint8_t channel = 0; channel < kServoChannelCount; ++channel) {
    snapshot[7 + channel] = gAppliedServoValues[channel];
  }
  writeU16LittleEndian(snapshot, 15, gInaVoltageMillivolts[0]);
  writeI16LittleEndian(snapshot, 17, gInaCurrentMilliamps[0]);
  writeU16LittleEndian(snapshot, 19, gInaVoltageMillivolts[1]);
  writeI16LittleEndian(snapshot, 21, gInaCurrentMilliamps[1]);
  snapshot[kStatusSnapshotChecksumIndex] = snapshotChecksum(snapshot);

  noInterrupts();
  for (uint8_t index = 0; index < kStatusSnapshotByteCount; ++index) {
    gStatusSnapshot[index] = snapshot[index];
  }
  interrupts();
}

void onPiI2cReceive(int byteCount) {
  if (byteCount <= 0) {
    return;
  }

  const int registerAddress = Wire1.read();
  --byteCount;
  gLastRegisterPointer = static_cast<uint8_t>(registerAddress);

  if (byteCount <= 0) {
    return;
  }

  switch (registerAddress) {
    case kRegisterRailState:
      while (Wire1.available()) {
        Wire1.read();
      }
      break;
    case kRegisterSolenoidState:
      gRequestedSolenoidState =
          static_cast<uint8_t>(Wire1.read()) & kSolenoidMask;
      gPendingControlApply = true;
      break;
    case kRegisterServoEnableMask:
      gRequestedServoEnableMask = static_cast<uint8_t>(Wire1.read());
      gPendingControlApply = true;
      break;
    default:
      if (registerAddress >= kRegisterPixelCommandStart &&
          registerAddress <= kRegisterPixelCommandEnd) {
        uint8_t pixelRegister = static_cast<uint8_t>(registerAddress);
        while (Wire1.available() && pixelRegister <= kRegisterPixelCommandEnd) {
          gRequestedPixelCommand[pixelRegister - kRegisterPixelCommandStart] =
              static_cast<uint8_t>(Wire1.read());
          if (pixelRegister == kRegisterPixelTrigger &&
              gRequestedPixelCommand[pixelRegister - kRegisterPixelCommandStart] !=
                  0) {
            gPendingPixelCommand = true;
          }
          ++pixelRegister;
        }
        while (Wire1.available()) {
          Wire1.read();
        }
      } else if (registerAddress >= kRegisterServo0Value &&
                 registerAddress <= kRegisterServo7Value) {
        uint8_t servoRegister = static_cast<uint8_t>(registerAddress);
        while (Wire1.available() && servoRegister <= kRegisterServo7Value) {
          gRequestedServoValues[servoRegister - kRegisterServo0Value] =
              static_cast<uint8_t>(Wire1.read());
          ++servoRegister;
          gPendingControlApply = true;
        }
        while (Wire1.available()) {
          Wire1.read();
        }
      } else {
        while (Wire1.available()) {
          Wire1.read();
        }
      }
      break;
  }
}

void onPiI2cRequest() {
  if (gLastRegisterPointer >= kRegisterStatusSnapshotStart &&
      gLastRegisterPointer <= kRegisterStatusSnapshotEnd) {
    uint8_t snapshot[kStatusSnapshotByteCount] = {};
    noInterrupts();
    for (uint8_t index = 0; index < kStatusSnapshotByteCount; ++index) {
      snapshot[index] = gStatusSnapshot[index];
    }
    interrupts();

    const uint8_t offset =
        static_cast<uint8_t>(gLastRegisterPointer - kRegisterStatusSnapshotStart);
    Wire1.write(&snapshot[offset], kStatusSnapshotByteCount - offset);
    return;
  }

  uint8_t response = 0xFF;

  switch (gLastRegisterPointer) {
    case kRegisterProtocolVersion:
      response = kProtocolVersion;
      break;
    case kRegisterRailState:
      response = gAppliedRailState;
      break;
    case kRegisterSolenoidState:
      response = gAppliedSolenoidState;
      break;
    case kRegisterAlarmState:
      response = getAlarmStateBits();
      break;
    case kRegisterInaPresence:
      response = getInaPresenceBits();
      break;
    case kRegisterServoEnableMask:
      response = gAppliedServoEnableMask;
      break;
    case kRegisterIna0VoltageLow:
      response = static_cast<uint8_t>(gInaVoltageMillivolts[0] & 0xFF);
      break;
    case kRegisterIna0VoltageHigh:
      response = static_cast<uint8_t>((gInaVoltageMillivolts[0] >> 8) & 0xFF);
      break;
    case kRegisterIna0CurrentLow:
      response = static_cast<uint8_t>(gInaCurrentMilliamps[0] & 0xFF);
      break;
    case kRegisterIna0CurrentHigh:
      response = static_cast<uint8_t>((gInaCurrentMilliamps[0] >> 8) & 0xFF);
      break;
    case kRegisterIna1VoltageLow:
      response = static_cast<uint8_t>(gInaVoltageMillivolts[1] & 0xFF);
      break;
    case kRegisterIna1VoltageHigh:
      response = static_cast<uint8_t>((gInaVoltageMillivolts[1] >> 8) & 0xFF);
      break;
    case kRegisterIna1CurrentLow:
      response = static_cast<uint8_t>(gInaCurrentMilliamps[1] & 0xFF);
      break;
    case kRegisterIna1CurrentHigh:
      response = static_cast<uint8_t>((gInaCurrentMilliamps[1] >> 8) & 0xFF);
      break;
    case kRegisterPixelTrigger:
      response = gPixelAnimation.active ? 1 : 0;
      break;
    default:
      if (gLastRegisterPointer >= kRegisterServo0Value &&
          gLastRegisterPointer <= kRegisterServo7Value) {
        response =
            gAppliedServoValues[gLastRegisterPointer - kRegisterServo0Value];
      } else if (gLastRegisterPointer >= kRegisterPixelCommandStart &&
                 gLastRegisterPointer <= kRegisterPixelCommandEnd) {
        response = gRequestedPixelCommand[gLastRegisterPointer -
                                          kRegisterPixelCommandStart];
      }
      break;
  }

  Wire1.write(response);
}

void pulseOutputHigh(uint8_t pin, const char* label) {
  Serial.print("Pulsing ");
  Serial.println(label);
  digitalWrite(pin, HIGH);
  delay(kEnablePulseMs);
  digitalWrite(pin, LOW);
  delay(kEnablePulseMs);
}

void run12vRailSelfTest() {
  Serial.println("12V rail self-test start");
  pulseOutputHigh(k12vAEnablePin, "12V_A_ENABLE");
  pulseOutputHigh(k12vBEnablePin, "12V_B_ENABLE");
  pulseOutputHigh(k12vCEnablePin, "12V_C_ENABLE");
  Serial.println("12V rail self-test complete");
}

void pulseSolenoid(uint8_t bit, const char* label) {
  const uint8_t activeMask = static_cast<uint8_t>(1u << bit);
  Serial.print("Pulsing ");
  Serial.println(label);

  const bool onOk = writeTca9534Outputs(activeMask);
  delay(kSolenoidPulseMs);
  const bool offOk = writeTca9534Outputs(0x00);
  delay(kSolenoidPulseMs);

  if (!(onOk && offOk)) {
    Serial.print(label);
    Serial.println(" pulse FAILED");
  }
}

void runSolenoidSelfTest() {
  Serial.println("Solenoid self-test start");
  pulseSolenoid(0, "SOLENOID_P0");
  pulseSolenoid(1, "SOLENOID_P1");
  pulseSolenoid(2, "SOLENOID_P2");
  pulseSolenoid(3, "SOLENOID_P3");
  writeTca9534Outputs(0x00);
  Serial.println("Solenoid self-test complete");
}

}  // namespace

void setup() {
  Serial.begin(kSerialBaudRate);
  delay(2000);
  Serial.println();
  Serial.println("OneManBand_2040 bring-up start");

  configureOutputsLow(kRailEnablePins, sizeof(kRailEnablePins));
  pinMode(k8vEnablePin, OUTPUT);
  set8vRailEnabled(false);
  configureInputs(kAlarmInputPins, sizeof(kAlarmInputPins));
  Serial.println("GPIO defaults applied");

  applyRailState(kFixedRailState);
  Serial.println("Fixed rails applied: 12V_B, 12V_C, and 8V enabled; 12V_A disabled");
  initializePixelRails();
  runPixelRailSelfTest();

  Wire.setSDA(kI2c0SdaPin);
  Wire.setSCL(kI2c0SclPin);
  Wire.begin();
  Serial.println("I2C_0 initialized on GP4/GP5");
  scanI2cBus(Wire, "I2C_0");

  initializePca9685();
  initializeTca9534();
  if (kServoProofOfConceptEnabled) {
    Serial.println("Servo proof-of-concept sweep enabled");
  }

  pixel.begin();
  pixel.setBrightness(32);
  showColor(pixel.Color(255, 255, 0));
  Serial.println("Status pixel set to yellow");

  applyRailState(kFixedRailState);
  Serial.println("Fixed rails confirmed");
  rebuildStatusSnapshot();

  Wire1.setSDA(kI2c1SdaPin);
  Wire1.setSCL(kI2c1SclPin);
  Wire1.begin(kPiControlAddress);
  Wire1.onReceive(onPiI2cReceive);
  Wire1.onRequest(onPiI2cRequest);
  Serial.print("I2C_1 Pi control ready at 0x");
  Serial.println(kPiControlAddress, HEX);
  scanI2cBus(Wire1, "I2C_1");

  Serial.println("Setup complete");
  runSolenoidSelfTest();
  rebuildStatusSnapshot();
}

void loop() {
  applyPendingPixelCommand();
  applyPendingControlState();
  runServoProofOfConcept();
  updatePixelAnimation();
  showColor(pixel.Color(0, 0, 255));

  applyPendingPixelCommand();
  applyPendingControlState();
  updatePixelAnimation();
  rebuildStatusSnapshot();
  delay(50);
}
