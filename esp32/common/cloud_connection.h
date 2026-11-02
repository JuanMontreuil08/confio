#pragma once
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <time.h>
#ifdef CONFIO_TEST_CONFIG
#include "wifi_credentials.example.h"
#include "cloud_config.example.h"
#else
#include "wifi_credentials.h"
#include "cloud_config.h"
#endif
#include "cloud_roots.h"

// One outbound TLS connection. No model/provider key ever lives on the device.
class CloudConnection {
 public:
  WebSocketsClient socket;
  bool connected = false;
  void begin(const char *path, WebSocketsClient::WebSocketClientEvent handler) {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    configTime(0, 0, "time.cloudflare.com", "pool.ntp.org");
    socket.onEvent(handler);
    socket.beginSslWithCA(CONFIO_HOST, 443, path, kCloudRoots, "");
    headers = String("Authorization: Bearer ") + CONFIO_TOKEN + "\r\n";
    socket.setExtraHeaders(headers.c_str());
    socket.setReconnectInterval(5000);
    socket.enableHeartbeat(20000, 10000, 2);
  }
  void poll() {
    // Certificate validity requires a real clock. Never fall back to insecure TLS.
    if (WiFi.status() == WL_CONNECTED && time(nullptr) > 1735689600) socket.loop();
    else if (connected) {
      connected = false;
      socket.disconnect();
    }
  }
 private:
  String headers;
};
