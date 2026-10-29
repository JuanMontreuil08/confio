#include <Arduino.h>

constexpr int kLookButtonPin = 27;
constexpr unsigned long kDebounceMs = 50;

bool lastReading = HIGH;
bool stableState = HIGH;
unsigned long lastChangeMs = 0;

void setup() {
  Serial.begin(115200);
  pinMode(kLookButtonPin, INPUT_PULLUP);
}

void loop() {
  const bool reading = digitalRead(kLookButtonPin);
  if (reading != lastReading) {
    lastChangeMs = millis();
    lastReading = reading;
  }

  if (reading != stableState && millis() - lastChangeMs >= kDebounceMs) {
    stableState = reading;
    if (stableState == LOW) {
      Serial.println("Ver ejecutado");
    }
  }
}
