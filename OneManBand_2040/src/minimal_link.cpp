#include <Arduino.h>
#include <Wire.h>

void receiveEvent(int howMany) 
{
  if (Wire.available()) { // loop through all but the last
    char c = Wire.read(); // receive byte as a character
    Serial.print(c);         // print the character
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  Wire.begin(0x12);

  Wire.onReceive(receiveEvent);
}

void loop() {

}
