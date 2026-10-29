#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <esp_camera.h>
#include <esp_http_server.h>
#include "wifi_credentials.h"

WebServer server(80);
SemaphoreHandle_t cameraMutex;

esp_err_t streamVideo(httpd_req_t *request) {
  httpd_resp_set_type(request, "multipart/x-mixed-replace; boundary=frame");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  while (true) {
    xSemaphoreTake(cameraMutex, portMAX_DELAY);
    camera_fb_t *frame = esp_camera_fb_get();
    if (!frame) {
      xSemaphoreGive(cameraMutex);
      return ESP_FAIL;
    }
    char header[96];
    int length = snprintf(header, sizeof(header),
                          "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                          static_cast<unsigned int>(frame->len));
    esp_err_t result = httpd_resp_send_chunk(request, header, length);
    if (result == ESP_OK) result = httpd_resp_send_chunk(request, reinterpret_cast<const char *>(frame->buf), frame->len);
    if (result == ESP_OK) result = httpd_resp_send_chunk(request, "\r\n", 2);
    esp_camera_fb_return(frame);
    xSemaphoreGive(cameraMutex);
    if (result != ESP_OK) break;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  return ESP_OK;
}

void sendPhoto() {
  xSemaphoreTake(cameraMutex, portMAX_DELAY);
  camera_fb_t *frame = esp_camera_fb_get();
  if (!frame) {
    xSemaphoreGive(cameraMutex);
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(frame->len);
  server.send(200, "image/jpeg", "");
  WiFiClient client = server.client();
  client.write(frame->buf, frame->len);
  esp_camera_fb_return(frame);
  xSemaphoreGive(cameraMutex);
}

void setup() {
  Serial.begin(115200);
  pinMode(4, OUTPUT);
  digitalWrite(4, LOW);

  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;
  config.pin_d1 = 18;
  config.pin_d2 = 19;
  config.pin_d3 = 21;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = 0;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sccb_sda = 26;
  config.pin_sccb_scl = 27;
  config.pin_pwdn = 32;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 10;
  config.fb_count = psramFound() ? 2 : 1;

  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("Camera initialization failed");
    return;
  }
  cameraMutex = xSemaphoreCreateMutex();
  if (!cameraMutex) {
    Serial.println("Camera mutex initialization failed");
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Camera URL: http://");
  Serial.println(WiFi.localIP());

  server.on("/", []() {
    server.send(200, "text/html; charset=utf-8", R"HTML(<!doctype html>
<html lang="es"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cámara Confio</title><h1>Cámara Confio</h1>
<p>Vista en vivo</p>
<img id="view" alt="Vista de la cámara" style="max-width:100%">
<p><a href="/photo" download="confio.jpg">Tomar y descargar foto</a></p>
<script>document.getElementById('view').src = 'http://' + location.hostname + ':81/stream';</script>
</html>)HTML");
  });
  server.on("/photo", sendPhoto);
  server.begin();

  httpd_config_t streamConfig = HTTPD_DEFAULT_CONFIG();
  streamConfig.server_port = 81;
  httpd_handle_t streamServer = nullptr;
  if (httpd_start(&streamServer, &streamConfig) == ESP_OK) {
    httpd_uri_t streamUri = {};
    streamUri.uri = "/stream";
    streamUri.method = HTTP_GET;
    streamUri.handler = streamVideo;
    httpd_register_uri_handler(streamServer, &streamUri);
  } else {
    Serial.println("Video server failed to start");
  }
}

void loop() {
  server.handleClient();
}
