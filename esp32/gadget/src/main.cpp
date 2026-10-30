#include <Arduino.h>

constexpr unsigned long kDebounceMs = 50;

struct Button {
  int pin;
  const char* message;
  bool lastReading;
  bool stableState;
  unsigned long lastChangeMs;
};

Button buttons[] = {
    {26, "Llamar conectado", HIGH, HIGH, 0},
    {27, "Ver ejecutado", HIGH, HIGH, 0},
};

void setup() {
  Serial.begin(115200);
  for (const Button& button : buttons) {
    pinMode(button.pin, INPUT_PULLUP);
  }
}

void loop() {
  for (Button& button : buttons) {
    const bool reading = digitalRead(button.pin);
    if (reading != button.lastReading) {
      button.lastChangeMs = millis();
      button.lastReading = reading;
    }

    if (reading != button.stableState &&
        millis() - button.lastChangeMs >= kDebounceMs) {
      button.stableState = reading;
      if (button.stableState == LOW) {
        Serial.println(button.message);
      }
    }
  }
}
