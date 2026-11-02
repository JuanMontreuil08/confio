#pragma once
#include "../../common/cloud_connection.h"
CloudConnection cloud;

void cameraEvent(WStype_t type, uint8_t *data, size_t length) {
  if (type == WStype_CONNECTED) {
    cloud.connected = true;
    Serial.println("Cloud camera connected");
  } else if (type == WStype_DISCONNECTED) {
    cloud.connected = false;
    Serial.println("Cloud camera disconnected; reconnecting");
  } else if (type == WStype_TEXT && length == 40 && !memcmp(data, "capture:", 8)) {
    for (size_t i = 8; i < length; ++i) if (!isxdigit(data[i])) return;
    // Drain the two stale buffers exactly as the original laptop tool does.
    camera_fb_t *frame = nullptr;
    xSemaphoreTake(cameraMutex, portMAX_DELAY);
    for (int n = 0; n < 3; ++n) {
      if (frame) esp_camera_fb_return(frame);
      frame = esp_camera_fb_get();
      if (!frame) break;
    }
    uint8_t *message = frame ? static_cast<uint8_t *>(ps_malloc(frame->len + 32)) : nullptr;
    if (!message && frame) message = static_cast<uint8_t *>(malloc(frame->len + 32));
    if (message) {
      memcpy(message, data + 8, 32);
      memcpy(message + 32, frame->buf, frame->len);
      cloud.socket.sendBIN(message, frame->len + 32);
      free(message);
    } else {
      cloud.socket.sendTXT("error:capture");
    }
    if (frame) esp_camera_fb_return(frame);
    xSemaphoreGive(cameraMutex);
  }
}
