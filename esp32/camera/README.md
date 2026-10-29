# ESP32-CAM test

This test targets the common AI Thinker ESP32-CAM with an OV2640 camera.
Connect the camera and laptop to the same 2.4 GHz Wi-Fi network.

1. Copy `include/wifi_credentials.example.h` to `include/wifi_credentials.h`.
2. Enter the network name and password in the copied file. It is ignored by Git.
3. Open this folder in PlatformIO, connect the camera over a data-capable USB
   cable, and upload the firmware.
4. Open the serial monitor at 115200 baud. Press reset if necessary and open the
   printed URL in a browser. The page displays a live video stream; the photo
   link downloads a new image using the browser's download settings.

The camera page has no authentication and is intended for a trusted local
network. Do not expose it to the internet.
