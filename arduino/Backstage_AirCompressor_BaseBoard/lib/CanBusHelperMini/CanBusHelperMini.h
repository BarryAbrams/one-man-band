#ifndef __CANBUSHELPERMINI_H__
#define __CANBUSHELPERMINI_H__

#include <Arduino.h>
#include <Adafruit_MCP2515.h>

#define MSG_TYPE_STATE        0x01
#define MSG_TYPE_SENSOR_VALUE 0x03
#define MSG_TYPE_BOOL_VALUE   0x1F
#define MSG_TYPE_INT_VALUE    0x1E
#define MSG_TYPE_HELLO        0x95
#define MSG_TYPE_PONG         0x07
#define MSG_TYPE_PING         0x08
#define MSG_TYPE_TEXTCMD      0x94
#define MSG_TYPE_VARIABLE   0x0B
#define TEXTCMD_CHUNK         0xF0
#define MSG_TYPE_INTERRUPT        0x96
#define MSG_TYPE_RECALIBRATE 0x0E

#define MSG_TYPE_SERVO_STEVE 0xD5
#define MSG_TYPE_SERVO_STEVE_STATS 0xD7
#define MSG_TYPE_RECEIPT 0xD6

#define MAX_LINE 128

enum PriorityState : uint8_t {
  PRIORITY_HIGH = 0x01,
  PRIORITY_MEDIUM = 0x02,
  PRIORITY_LOW = 0x03,
  PRIORITY_HEARTBEAT = 0x04
};

enum ReceiptType : uint8_t {
  RECEIPT_SUCCESS = 0x01,
  RECEIPT_ERROR = 0x02,
  RECEIPT_WARNING = 0x03
};


enum DeviceState : uint8_t {
  STATE_IDLE = 1,
  STATE_PROCESSING,
  STATE_SUCCESS,
  STATE_FAIL,
  STATE_REBOOTING,
  STATE_RESTARTING,
  STATE_RUNNING,
  STATE_OFF,
  STATE_ERROR = 0x99,
};

typedef void (*MessageCallback)(uint8_t sender,
                                uint8_t recipient,
                                uint8_t messageType,
                                const uint8_t *payload,
                                uint8_t length);

class CanBusHelperMini {
  public:
    CanBusHelperMini(uint8_t csPin, uint8_t intPin, uint8_t resetPin);
    void begin(MessageCallback callback);
    void loop();
    void setAddress(uint8_t deviceID);
    void setFirmwareVersion(uint32_t v);
    uint32_t firmwareVersion() const;
    bool isReady() const { return status; }
    void dumpRegisters(Stream &out);
    void resetController();

    void setState(DeviceState newState);
    DeviceState getState() const { return state; }

    void sendState(DeviceState newState, PriorityState priority = PRIORITY_MEDIUM);
    void sendSensorValue(int pin, uint16_t value, PriorityState priority = PRIORITY_MEDIUM);
    void sendBoolean(int pin, bool value, PriorityState priority = PRIORITY_MEDIUM);
    void sendIntValue(int pin, uint16_t value, PriorityState priority = PRIORITY_MEDIUM);
    void sendInterrupt(int pin, uint8_t state, uint16_t value, PriorityState priority = PRIORITY_HIGH);
    void sendInterrupt(int pin, uint8_t state, uint16_t value1, uint16_t value2, PriorityState priority = PRIORITY_HIGH);
    void sendText(const char *s, PriorityState priority = PRIORITY_LOW);
    void sendReceipt(uint8_t state,
                    uint8_t value,
                    uint8_t value1,
                    ReceiptType type = RECEIPT_SUCCESS,
                    PriorityState priority = PRIORITY_LOW);

    void sendHello();
    bool sendStandard(uint16_t canId, const uint8_t *data, uint8_t len);
    void sendSimple(uint8_t msgType, const uint8_t *data, uint8_t len, PriorityState priority = PRIORITY_MEDIUM);

  private:
    Adafruit_MCP2515 _mcp;

    MessageCallback _callback;

    uint8_t _deviceID;
    uint8_t _intPin;
    uint8_t _csPin;
    uint8_t _resetPin;
    uint32_t _fwVersion = 0;

    char _txLineBuf[96];
    uint8_t _txLineLen = 0;

    char _rxLineBuf[MAX_LINE];
    uint8_t _rxLineLen = 0;

    DeviceState state;

    void _txPushChar(char c, PriorityState priority);
    void _txPushStr(const char *s, PriorityState priority);
    void _txPushU16(uint16_t v, PriorityState priority);

    static CanBusHelperMini *instance;

    void processReceive(int packetSize);
    static void onReceiveStatic(int packetSize);

  bool status;
};

#endif // __CANBUSHELPERMINI_H__
