#include "CanBusHelperMini.h"

#define CAN_BAUDRATE 100000
#ifndef CAN_MCP_CLOCK_HZ
#define CAN_MCP_CLOCK_HZ 16000000L
#endif

CanBusHelperMini *CanBusHelperMini::instance = nullptr;

CanBusHelperMini::CanBusHelperMini(uint8_t csPin, uint8_t intPin, uint8_t resetPin)

#if defined(ARDUINO_ARCH_ESP32)
: _mcp(csPin, &SPI)
#else
: _mcp(csPin, intPin)
#endif

{
  instance = this;
  _csPin = csPin;
  _intPin = intPin;
  _resetPin = resetPin;
  _deviceID = 0;
  status = false;
}


void CanBusHelperMini::begin(MessageCallback callback) {
    _callback = callback;

    #ifdef DEBUG_SERIAL
    Serial.println("CanBusHelperMini begin called");
    #endif

    resetController();
}

void CanBusHelperMini::resetController() {
    const uint8_t maxAttempts = 4;

    pinMode(_resetPin, OUTPUT);
    _mcp.end();

    status = false;
    for (uint8_t attempt = 1; attempt <= maxAttempts && !status; attempt++) {
      digitalWrite(_resetPin, LOW);
      delay(10);
      digitalWrite(_resetPin, HIGH);
      delay(25);

      _mcp.setClockFrequency(CAN_MCP_CLOCK_HZ);
      status = _mcp.begin(CAN_BAUDRATE);
      if (!status) {
        Serial.print("MCP2515 begin attempt ");
        Serial.print(attempt);
        Serial.println(" failed");
        delay(50);
      }
    }

    if (!status) {
      Serial.println("ERROR: MCP2515 begin failed");
      return;
    }

    Serial.print("MCP2515 initialized at ");
    Serial.print(CAN_BAUDRATE);
    Serial.print(" bps with ");
    Serial.print(CAN_MCP_CLOCK_HZ);
    Serial.println(" Hz crystal");
    delay(50);

    sendHello();
    Serial.println("CAN hello frame queued");
}

void CanBusHelperMini::setAddress(uint8_t deviceID) {
  _deviceID = deviceID;
}

void CanBusHelperMini::setFirmwareVersion(uint32_t v) {
  _fwVersion = v;
}

uint32_t CanBusHelperMini::firmwareVersion() const {
  return _fwVersion;
}

void CanBusHelperMini::dumpRegisters(Stream &out) {
  _mcp.dumpRegisters(out);
}

void CanBusHelperMini::loop() {
  #ifdef DEBUG_SERIAL
  Serial.println("CanBusHelperMini loop called");
  #endif

  int packetSize = _mcp.parsePacket();

  if (packetSize > 0) {
    processReceive(packetSize);
  }
}

void CanBusHelperMini::onReceiveStatic(int packetSize) {
  if (instance) instance->processReceive(packetSize);
}

void CanBusHelperMini::processReceive(int packetSize) {
  #ifdef DEBUG_SERIAL
  Serial.println("Processing received packet...");
  #endif
  uint32_t id = _mcp.packetId();
  uint8_t msgType = (id >> 16) & 0xFF;
  uint8_t sender  = (id >> 8)  & 0xFF;
  uint8_t recip   = id & 0xFF;

  if (recip != _deviceID && recip != 0xFF) {
    return;
  }

  uint8_t payload[8] = {0};
  for (int i = 0; i < packetSize && i < 8; i++) {
    payload[i] = _mcp.read();
  }

  if (msgType != MSG_TYPE_PING) {
    #ifdef DEBUG_SERIAL
    Serial.print("Received packet: Type=");
    Serial.print(msgType);
    Serial.print(" From=");
    Serial.print(sender);
    Serial.print(" To=");
    Serial.print(recip);
    Serial.print(" Size=");
    Serial.println(packetSize);
    #endif
  }


  if (msgType == MSG_TYPE_PING) {
    sendSimple(MSG_TYPE_PONG, payload, packetSize);
  }

  if (msgType == MSG_TYPE_TEXTCMD && packetSize >= 2) {
        if (payload[0] == TEXTCMD_CHUNK) {

            for (uint8_t i = 1; i < packetSize; i++) {

                char c = (char)payload[i];

                if (c == '\n') {
                    _rxLineBuf[_rxLineLen] = '\0';

                    if (_callback) {
                        _callback(sender,
                                  recip,
                                  MSG_TYPE_TEXTCMD,
                                  (uint8_t*)_rxLineBuf,
                                  _rxLineLen);
                    }

                    _rxLineLen = 0;
                }
                else if (_rxLineLen < MAX_LINE - 1) {
                    _rxLineBuf[_rxLineLen++] = c;
                }
                else {
                    // overflow protection
                    _rxLineLen = 0;
                }
            }
        }

        return; // fully handled here
    }

  if (_callback) _callback(sender, recip, msgType, payload, (uint8_t)packetSize);
}

void CanBusHelperMini::sendHello() {
  uint8_t data[1] = { (uint8_t)(0xFF) }; 

  sendSimple(MSG_TYPE_HELLO, data, 1, PRIORITY_MEDIUM);
}

void CanBusHelperMini::sendReceipt(uint8_t state, uint8_t value, uint8_t value1, ReceiptType type, PriorityState priority) {
  uint8_t data[4] = {
    state,
    value,
    value1,
    (uint8_t)type
  };

  sendSimple(MSG_TYPE_RECEIPT, data, 4, priority);
}

void CanBusHelperMini::sendText(const char *s, PriorityState priority) {
  uint8_t buf[7];              // 1 marker + 6 chars
  buf[0] = TEXTCMD_CHUNK;

  while (*s) {
    uint8_t n = 1;

    while (*s && n < 7) {      // <-- WAS 8
      buf[n++] = (uint8_t)*s++;
    }

    sendSimple(MSG_TYPE_TEXTCMD, buf, n, priority);
    delayMicroseconds(300);
  }

  // newline
  buf[0] = TEXTCMD_CHUNK;
  buf[1] = '\n';
  sendSimple(MSG_TYPE_TEXTCMD, buf, 2, priority);
}

// --- tiny decimal writer (no sendPrintf) ---
// static char* utoa10_local(char* p, uint16_t v) {
//   char tmp[6];
//   uint8_t n = 0;
//   do { tmp[n++] = '0' + (v % 10); v /= 10; } while (v && n < sizeof(tmp));
//   while (n--) *p++ = tmp[n];
//   *p = 0;
//   return p;
// }

// void CanBusHelperMini::_txPushChar(char c, PriorityState priority) {
//   if (c == '\r') return;

//   // newline => flush
//   if (c == '\n') {
//     flushText(priority);
//     return;
//   }

//   if (_txLineLen < sizeof(_txLineBuf) - 1) {
//     _txLineBuf[_txLineLen++] = c;
//     _txLineBuf[_txLineLen] = 0;
//   } else {
//     // buffer full: flush, then start new line with this char
//     flushText(priority);
//     if (_txLineLen < sizeof(_txLineBuf) - 1) {
//       _txLineBuf[_txLineLen++] = c;
//       _txLineBuf[_txLineLen] = 0;
//     }
//   }
// }

// void CanBusHelperMini::_txPushStr(const char *s, PriorityState priority) {
//   while (*s) _txPushChar(*s++, priority);
// }

// void CanBusHelperMini::_txPushU16(uint16_t v, PriorityState priority) {
//   char tmp[6];
//   char* p = tmp;
//   utoa10_local(p, v);
//   _txPushStr(tmp, priority);
// }

// void CanBusHelperMini::flushText(PriorityState priority) {
//   if (_txLineLen == 0) return;
//   _txLineBuf[_txLineLen] = 0;

//   // sendText() should append newline on-wire (or you can add '\n' here)
//   sendText(_txLineBuf, priority);

//   _txLineLen = 0;
//   _txLineBuf[0] = 0;
// }

// ----- public sendPrint/sendPrintln -----

// void CanBusHelperMini::sendPrint(const char *s, PriorityState priority) {
//   if (!s) return;
//   _txPushStr(s, priority);
// }

// void CanBusHelperMini::sendPrint(char c, PriorityState priority) {
//   _txPushChar(c, priority);
// }

// void CanBusHelperMini::sendPrint(uint16_t v, PriorityState priority) {
//   _txPushU16(v, priority);
// }

// void CanBusHelperMini::sendPrint(int v, PriorityState priority) {
//   if (v < 0) {
//     _txPushChar('-', priority);
//     v = -v;
//   }
//   _txPushU16((uint16_t)v, priority);
// }

// void CanBusHelperMini::sendPrintln(PriorityState priority) {
//   flushText(priority);
// }

// void CanBusHelperMini::sendPrintln(const char *s, PriorityState priority) {
//   sendPrint(s, priority);
//   flushText(priority);
// }

// void CanBusHelperMini::sendPrintln(uint16_t v, PriorityState priority) {
//   sendPrint(v, priority);
//   flushText(priority);
// }

// void CanBusHelperMini::sendPrintln(int v, PriorityState priority) {
//   sendPrint(v, priority);
//   flushText(priority);
// }

void CanBusHelperMini::setState(DeviceState s) { 
  state = s; 
  sendState(s); 
}

void CanBusHelperMini::sendState(DeviceState s, PriorityState priority) {
  uint8_t v = (uint8_t)s;
  sendSimple(MSG_TYPE_STATE, &v, 1, priority);
}

void CanBusHelperMini::sendSensorValue(int pin, uint16_t val, PriorityState priority) {
  uint8_t data[3] = { (uint8_t)(pin & 0xFF), (uint8_t)(val >> 8), (uint8_t)(val & 0xFF) };

  sendSimple(MSG_TYPE_SENSOR_VALUE, data, 3, priority);
}

void CanBusHelperMini::sendBoolean(int pin, bool value, PriorityState priority) {
  uint8_t data[2] = { (uint8_t)(pin & 0xFF), value ? (uint8_t)1 : (uint8_t)0 };  
  sendSimple(MSG_TYPE_BOOL_VALUE, data, 2, priority);
}

void CanBusHelperMini::sendIntValue(int pin, uint16_t val, PriorityState priority) {
  uint8_t data[3] = { (uint8_t)(pin & 0xFF), (uint8_t)(val >> 8), (uint8_t)(val & 0xFF) };

  sendSimple(MSG_TYPE_INT_VALUE, data, 3, priority);
}

void CanBusHelperMini::sendInterrupt(int pin, uint8_t state, uint16_t val, PriorityState priority) {
  uint8_t data[4] = {
    (uint8_t)(pin & 0xFF),
    (uint8_t)state,
    (uint8_t)(val >> 8),
    (uint8_t)(val & 0xFF)
  };

  sendSimple(MSG_TYPE_INTERRUPT, data, 4, priority);
}

void CanBusHelperMini::sendInterrupt(int pin, uint8_t state, uint16_t value1, uint16_t value2, PriorityState priority) {
  uint8_t data[6] = {
    (uint8_t)(pin & 0xFF),
    (uint8_t)state,
    (uint8_t)(value1 >> 8),
    (uint8_t)(value1 & 0xFF),
    (uint8_t)(value2 >> 8),
    (uint8_t)(value2 & 0xFF)
  };

  sendSimple(MSG_TYPE_INTERRUPT, data, 6, priority);
}

void CanBusHelperMini::sendSimple(uint8_t msgType, const uint8_t *data, uint8_t len, PriorityState priority) {
  uint32_t id = ((uint32_t)msgType << 16) | ((uint32_t)_deviceID << 8) | 0xFA;

  _mcp.beginExtendedPacket(id);

  _mcp.write((uint8_t)priority);

  for (uint8_t i = 0; i < len; i++) {
    _mcp.write(data[i]);
  }

  _mcp.endPacket();
  
  delayMicroseconds(500);
}

bool CanBusHelperMini::sendStandard(uint16_t canId, const uint8_t *data, uint8_t len) {
  if (!status) return false;

  if (!_mcp.beginPacket(canId & 0x7FF)) {
    return false;
  }

  for (uint8_t i = 0; i < len && i < 8; i++) {
    _mcp.write(data[i]);
  }

  bool ok = _mcp.endPacket() == 1;
  delayMicroseconds(500);
  return ok;
}
