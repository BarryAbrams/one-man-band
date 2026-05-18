#include <SPI.h>
#include <Arduino.h>
#include <Adafruit_MCP2515.h>

#define MISO_PIN 0
#define CS_PIN 1
#define SCK_PIN 2
#define MOSI_PIN 3
#define CAN_RESET 7
#define CAN_INT 8

#define CAN_BAUDRATE (500000)

Adafruit_MCP2515 mcp(CS_PIN, MOSI_PIN, MISO_PIN, SCK_PIN);

bool gMcpReady = false;
unsigned long gLastIdlePrintMs = 0;

void printHexByte(uint8_t value) {
  if (value < 0x10) {
    Serial.print('0');
  }
  Serial.print(value, HEX);
}

bool resetAndBeginMcp() {
  const uint8_t maxAttempts = 4;

  pinMode(CAN_RESET, OUTPUT);
  digitalWrite(CAN_RESET, HIGH);
  mcp.end();

  for (uint8_t attempt = 1; attempt <= maxAttempts; attempt++) {
    digitalWrite(CAN_RESET, LOW);
    delay(10);
    digitalWrite(CAN_RESET, HIGH);
    delay(25);

    if (mcp.begin(CAN_BAUDRATE)) {
      Serial.print("MCP2515 initialized at ");
      Serial.print(CAN_BAUDRATE);
      Serial.println(" bps");
      return true;
    }

    Serial.print("MCP2515 begin attempt ");
    Serial.print(attempt);
    Serial.println(" failed");
    delay(50);
  }

  return false;
}

void setup() {
  Serial.begin(115200);
  while(!Serial) delay(10);

  delay(100);

  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);
  pinMode(CAN_INT, INPUT_PULLUP);


  Serial.println("MCP2515 receive poll test!");

  gMcpReady = resetAndBeginMcp();
  if (!gMcpReady) {
    Serial.println("Error initializing MCP2515.");
    return;
  }
  Serial.println("MCP2515 chip found");
}

void loop() {
  if (!gMcpReady) {
    delay(1000);
    return;
  }

  const int packetSize = mcp.parsePacket();
  if (!packetSize) {
    if (millis() - gLastIdlePrintMs >= 1000) {
      gLastIdlePrintMs = millis();
      Serial.print("waiting, INT=");
      Serial.println(digitalRead(CAN_INT));
    }
    delay(2);
    return;
  }

  Serial.print("rx id=0x");
  Serial.print(mcp.packetId(), HEX);
  Serial.print(mcp.packetExtended() ? " ext" : " std");
  Serial.print(" len=");
  Serial.print(packetSize);
  Serial.print(" data=");

  while (mcp.available()) {
    printHexByte(mcp.read());
    Serial.print(' ');
  }
  Serial.println();
}
