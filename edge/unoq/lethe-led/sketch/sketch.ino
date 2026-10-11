// Lethe LED: the MCU side of the UNO Q. Python (main.py) calls set_result(true/false) over the Bridge.
// Built-in LED on = last removal verified (certificate VALID, tampered copy INVALID, re-execution matched).
// Blinking = something failed. If you want the 8x13 matrix instead, swap in the App Lab LED-matrix example code.
#include "Arduino_RouterBridge.h"

bool ok = false;
bool failed = false;

void set_result(bool good) {
  ok = good;
  failed = !good;
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);  // HIGH = off on this board
  Bridge.begin();
  Bridge.provide("set_result", set_result);
}

void loop() {
  if (ok) {
    digitalWrite(LED_BUILTIN, LOW);
  } else if (failed) {
    digitalWrite(LED_BUILTIN, (millis() / 200) % 2 ? LOW : HIGH);
  }
  delay(20);
}
