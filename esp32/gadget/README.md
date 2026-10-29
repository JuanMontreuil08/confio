# Gadget button test

Connect the Look button between GPIO 27 and GND. The firmware uses the ESP32's
internal pull-up resistor; no external resistor is needed.

Open this folder in VS Code with PlatformIO, connect the ESP32 by USB, and use
PlatformIO's Upload action. Open the Serial Monitor at 115200 baud. Each button
press should print `Ver ejecutado` once. If it does not, disconnect USB and
check the wiring and button orientation.
