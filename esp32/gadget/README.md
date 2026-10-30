# Gadget button test

Connect the Call button between GPIO 26 and GND, and the Look button between
GPIO 27 and GND. Both buttons can share a ground rail. The firmware uses the ESP32's
internal pull-up resistor; no external resistor is needed.

Open this folder in VS Code with PlatformIO, connect the ESP32 by USB, and use
PlatformIO's Upload action. Open the Serial Monitor at 115200 baud. Each button
press should print `Llamar conectado` (Call) or `Ver ejecutado` (Look) once,
with independent 50 ms debouncing. Holding a button does not repeat the message.
If it does not work, disconnect USB and
check the wiring and button orientation.
