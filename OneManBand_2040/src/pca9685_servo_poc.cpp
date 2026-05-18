#include <Adafruit_PWMServoDriver.h>
#include <Arduino.h>
#include <Wire.h>

namespace {
constexpr unsigned long kSerialBaudRate = 115200;

constexpr uint8_t kI2cSdaPin = 4;
constexpr uint8_t kI2cSclPin = 5;
constexpr uint8_t k8vEnablePin = 12;

constexpr uint8_t kPca9685Address = 0x43;
constexpr uint8_t kServoChannelCount = 8;
constexpr uint16_t kServoPulseMinTicks = 205;
constexpr uint16_t kServoPulseMaxTicks = 410;
constexpr uint16_t kSweepStepMs = 700;

Adafruit_PWMServoDriver servoDriver(kPca9685Address, Wire);

unsigned long gLastSweepStepMs = 0;
uint8_t gSweepStep = 0;

void set8vSupplyEnabled(bool enabled) {
  digitalWrite(k8vEnablePin, enabled ? LOW : HIGH);
}

uint16_t mapServoValueToTicks(uint8_t value) {
  const uint16_t span = kServoPulseMaxTicks - kServoPulseMinTicks;
  return static_cast<uint16_t>(kServoPulseMinTicks +
                               ((static_cast<uint32_t>(span) * value) / 255u));
}

void setServoChannelOff(uint8_t channel) {
  servoDriver.setPWM(channel, 0, 4096);
}

void setAllServoChannelsOff() {
  for (uint8_t channel = 0; channel < kServoChannelCount; ++channel) {
    setServoChannelOff(channel);
  }
}

void runServoSweep() {
  const unsigned long now = millis();
  if (now - gLastSweepStepMs < kSweepStepMs) {
    return;
  }
  gLastSweepStepMs = now;

  constexpr uint8_t kPositions[] = {32, 127, 224, 127};
  constexpr uint8_t kPositionCount = sizeof(kPositions) / sizeof(kPositions[0]);
  const uint8_t channel = (gSweepStep / kPositionCount) % kServoChannelCount;
  const uint8_t value = kPositions[gSweepStep % kPositionCount];
  const uint16_t pulseTicks = mapServoValueToTicks(value);

  setAllServoChannelsOff();
  servoDriver.setPWM(channel, 0, pulseTicks);

  Serial.print("servo channel=");
  Serial.print(channel);
  Serial.print(" value=");
  Serial.print(value);
  Serial.print(" ticks=");
  Serial.println(pulseTicks);

  ++gSweepStep;
}
}  // namespace

void setup() {
  Serial.begin(kSerialBaudRate);
  delay(2000);
  Serial.println();
  Serial.println("Adafruit PCA9685 servo POC at 0x43");

  pinMode(k8vEnablePin, OUTPUT);
  set8vSupplyEnabled(true);
  Serial.println("8V servo supply enabled on GP12");

  Wire.setSDA(kI2cSdaPin);
  Wire.setSCL(kI2cSclPin);
  Wire.begin();
  Serial.println("I2C initialized on GP4/GP5");

  servoDriver.begin();
  servoDriver.setOscillatorFrequency(27000000);
  servoDriver.setPWMFreq(50);
  delay(10);
  setAllServoChannelsOff();
  Serial.println("PCA9685 initialized for 50 Hz servo PWM");
}

void loop() {
  runServoSweep();
}
