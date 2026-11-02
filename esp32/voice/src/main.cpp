// Push-to-talk voice loop over USB serial: the INMP441 sends the user's voice
// to the laptop and the MAX98357A plays the audio the laptop sends back.
// Reuses the validated wiring and I2S settings of esp32/mic and esp32/speaker.
#include <Arduino.h>
#include <driver/i2s.h>
#ifdef CONFIO_WIFI
#include <WiFi.h>
#endif

// Microphone (validated in esp32/mic).
constexpr i2s_port_t kMicPort = I2S_NUM_0;
constexpr int kMicClockPin = 18;
constexpr int kMicWordSelectPin = 5;
constexpr int kMicDataPin = 22;
constexpr int kButtonPin = 23;
constexpr int kHangupPin = 4; // second button: hang up a live call (to GND, internal pull-up)

// Speaker (validated in esp32/speaker).
constexpr i2s_port_t kSpeakerPort = I2S_NUM_1;
constexpr int kSpeakerClockPin = 16;      // RX2 on the DevKit silkscreen
constexpr int kSpeakerWordSelectPin = 17; // TX2 on the DevKit silkscreen
constexpr int kSpeakerDataPin = 19;
constexpr int kVolumePercent = 100; // PCM amplitude, not perceived loudness

constexpr uint32_t kSampleRate = 16000;
constexpr uint32_t kBaudRate = 921600;
constexpr unsigned long kDebounceMs = 50;
constexpr size_t kMicSamples = 256;
constexpr size_t kMaxPlayBytes = 1024; // 512 mono samples per playback frame

// Frames: 4-byte magic, 1-byte type, little-endian uint32 length, payload.
// ESP32 -> laptop uses "MIC1": S start, D voice, F finish, A played, I info, E error,
// H hang-up button pressed (the laptop ignores it when no call is live),
// M call-mode voice (mono channel 1 only, half the serial traffic of D).
// Laptop -> ESP32 uses "SPK1": P mono PCM to play, E end of playback,
// C call mode on (mic streams continuously, button 1 ignored), N call mode off.
constexpr uint8_t kSpeakerMagic[] = {'S', 'P', 'K', '1'};
constexpr size_t kHeaderSize = 9;

#ifdef CONFIO_WIFI
void wifiSendFrame(uint8_t type, const uint8_t *data, uint32_t length);
void wifiPollPlayback();
extern bool wifiBusy;
extern bool wifiOnline;
#endif

void sendFrame(uint8_t type, const uint8_t *data = nullptr, uint32_t length = 0) {
#ifdef CONFIO_WIFI
  wifiSendFrame(type, data, length);
#else
  const uint8_t header[kHeaderSize] = {'M', 'I', 'C', '1', type,
                                       static_cast<uint8_t>(length),
                                       static_cast<uint8_t>(length >> 8),
                                       static_cast<uint8_t>(length >> 16),
                                       static_cast<uint8_t>(length >> 24)};
  Serial.write(header, sizeof(header));
  if (length) Serial.write(data, length);
#endif
}

void sendText(uint8_t type, const char *text) {
#ifdef CONFIO_WIFI
  // Keep boot mic diagnostics visible even before WiFi/TLS connects.
  Serial.println(text);
#endif
  sendFrame(type, reinterpret_cast<const uint8_t *>(text), strlen(text));
}

void fail(const char *operation) {
  sendText('E', operation);
  while (true) delay(1000);
}

void check(esp_err_t result, const char *operation) {
  if (result != ESP_OK) fail(operation);
}

void setupMicrophone() {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = kSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  // Keep the validated stereo framing; a single mic drives only one slot.
  config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = kMicSamples;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = kMicClockPin;
  pins.ws_io_num = kMicWordSelectPin;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kMicDataPin;
  check(i2s_driver_install(kMicPort, &config, 0, nullptr), "Microphone initialization failed");
  check(i2s_set_pin(kMicPort, &pins), "Microphone pin setup failed");
}

void setupSpeaker() {
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  config.sample_rate = kSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 4;
  config.dma_buf_len = 256;
  config.tx_desc_auto_clear = true; // silence when no audio is queued
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = kSpeakerClockPin;
  pins.ws_io_num = kSpeakerWordSelectPin;
  pins.data_out_num = kSpeakerDataPin;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  check(i2s_driver_install(kSpeakerPort, &config, 0, nullptr), "Speaker initialization failed");
  check(i2s_set_pin(kSpeakerPort, &pins), "Speaker pin setup failed");
  check(i2s_zero_dma_buffer(kSpeakerPort), "Speaker buffer clear failed");
}

void writeSpeaker(const int16_t *stereo, size_t bytes) {
  const uint8_t *cursor = reinterpret_cast<const uint8_t *>(stereo);
  while (bytes) {
    size_t written = 0;
    check(i2s_write(kSpeakerPort, cursor, bytes, &written, pdMS_TO_TICKS(1000)), "Speaker write failed");
    if (!written) fail("Speaker write timed out");
    cursor += written;
    bytes -= written;
  }
}

// Plays mono PCM, duplicated into both I2S slots like esp32/speaker.
void playMono(const uint8_t *data, size_t bytes) {
  static int16_t stereo[kMaxPlayBytes];
  const size_t count = bytes / sizeof(int16_t);
  for (size_t i = 0; i < count; ++i) {
    int16_t sample;
    memcpy(&sample, data + i * sizeof(sample), sizeof(sample));
    const int16_t scaled = static_cast<int32_t>(sample) * kVolumePercent / 100;
    stereo[2 * i] = scaled;
    stereo[2 * i + 1] = scaled;
  }
  writeSpeaker(stereo, count * 2 * sizeof(int16_t));
}

void finishPlayback() {
  // Push silence through the DMA queue so the last samples are heard without a click.
  static int16_t silence[512] = {};
  for (int i = 0; i < 4; ++i) writeSpeaker(silence, sizeof(silence));
}

bool playing = false;
bool callMode = false;
bool recording = false;

// Reads laptop frames byte by byte; returns after handling at most one frame.
void pollPlayback() {
#ifdef CONFIO_WIFI
  wifiPollPlayback();
#else
  static uint8_t header[kHeaderSize];
  static uint8_t payload[kMaxPlayBytes];
  static size_t headerFill = 0;
  static size_t payloadFill = 0;
  static uint32_t payloadLength = 0;

  while (Serial.available()) {
    const uint8_t byte = Serial.read();
    if (headerFill < kHeaderSize) {
      // Resynchronize on the magic marker if any byte does not match.
      if (headerFill < sizeof(kSpeakerMagic) && byte != kSpeakerMagic[headerFill]) {
        headerFill = byte == kSpeakerMagic[0] ? 1 : 0;
        if (headerFill) header[0] = byte;
        continue;
      }
      header[headerFill++] = byte;
      if (headerFill < kHeaderSize) continue;
      payloadLength = header[5] | header[6] << 8 | header[7] << 16 | static_cast<uint32_t>(header[8]) << 24;
      const uint8_t type = header[4];
      if ((type != 'P' && type != 'E' && type != 'C' && type != 'N') || payloadLength > kMaxPlayBytes ||
          payloadLength % 2) {
        headerFill = 0; // corrupt header: look for the next marker
        continue;
      }
      payloadFill = 0;
      if (payloadLength) continue;
    } else {
      payload[payloadFill++] = byte;
      if (payloadFill < payloadLength) continue;
    }

    const uint8_t type = header[4];
    headerFill = 0;
    if (type == 'P') {
      playing = true;
      playMono(payload, payloadLength);
      sendFrame('A'); // one acknowledgement per played frame paces the laptop
    } else if (type == 'E') {
      finishPlayback();
      playing = false;
      sendFrame('A');
    } else {
      // During a call the phone audio arrives as a continuous stream of P frames.
      callMode = type == 'C';
      if (callMode) i2s_zero_dma_buffer(kMicPort);
      sendText('I', callMode ? "Call mode on" : "Call mode off");
    }
    return;
  }
#endif
}

void pollMicrophone() {
#ifdef CONFIO_WIFI
  if (!wifiOnline) return;
#endif
  static bool previousReading = HIGH;
  static bool stableState = HIGH;
  static unsigned long changedAt = 0;
  static int32_t raw[kMicSamples];
  static int16_t pcm[kMicSamples];

  const bool reading = digitalRead(kButtonPin);
  if (reading != previousReading) {
    changedAt = millis();
    previousReading = reading;
  }
  if (reading != stableState && millis() - changedAt >= kDebounceMs) {
    stableState = reading;
    if (callMode) {
      recording = false; // button 1 does nothing during a call
    } else if (stableState == LOW && !playing
#ifdef CONFIO_WIFI
               && !wifiBusy
#endif
    ) {
      // The button is ignored while the agent speaks.
      i2s_zero_dma_buffer(kMicPort);
      recording = true;
      sendFrame('S');
    } else if (stableState == HIGH && recording) {
      recording = false;
      sendFrame('F');
    }
  }
  if (!recording && !callMode) return;

  // In a call, playback writes block the loop for up to ~32 ms, so drain every
  // buffered mic block (8 ms each, 128 ms of DMA) instead of reading only one.
  for (int block = 0; block < (callMode ? 16 : 1); ++block) {
    size_t bytesRead = 0;
    if (i2s_read(kMicPort, raw, sizeof(raw), &bytesRead, callMode ? 0 : pdMS_TO_TICKS(30)) != ESP_OK) return;
    const size_t count = bytesRead / sizeof(raw[0]);
    if (!count) return;
    // In call mode keep only channel 1 (even slots); the second slot is silent.
    const size_t step = callMode ? 2 : 1;
    size_t out = 0;
    for (size_t i = 0; i < count; i += step) {
      const int32_t sample = raw[i] >> 16;
      pcm[out++] = static_cast<int16_t>(constrain(sample, -32768, 32767));
    }
    sendFrame(callMode ? 'M' : 'D', reinterpret_cast<const uint8_t *>(pcm), out * sizeof(pcm[0]));
  }
}

// Reports each press of the hang-up button; the laptop decides whether a call is live.
void pollHangup() {
  static bool previousReading = HIGH;
  static bool stableState = HIGH;
  static unsigned long changedAt = 0;
  const bool reading = digitalRead(kHangupPin);
  if (reading != previousReading) {
    changedAt = millis();
    previousReading = reading;
  }
  if (reading != stableState && millis() - changedAt >= kDebounceMs) {
    stableState = reading;
    if (stableState == LOW) sendFrame('H');
  }
}

#ifdef CONFIO_WIFI
#include "wifi_transport.h"
#endif

void setup() {
  Serial.setRxBufferSize(16384); // must be set before begin()
  Serial.setTxBufferSize(4096);  // keeps call-mode mic frames from blocking the loop
  Serial.begin(kBaudRate);
  pinMode(kButtonPin, INPUT_PULLUP);
  pinMode(kHangupPin, INPUT_PULLUP);
  setupMicrophone();
#ifndef SKIP_SPEAKER
  setupSpeaker();
#endif
  {  // Boot mic check: peak raw value over ~0.5 s of room noise (0 = no signal, check wiring)
    static int32_t raw[kMicSamples];
    int32_t peak = 0;
    for (int n = 0; n < 32; ++n) {
      size_t bytesRead = 0;
      i2s_read(kMicPort, raw, sizeof(raw), &bytesRead, pdMS_TO_TICKS(100));
      for (size_t i = 0; i < bytesRead / 4; ++i) peak = max(peak, abs(raw[i] >> 8));
    }
    char text[48];
    snprintf(text, sizeof(text), "Mic check peak %ld", static_cast<long>(peak));
    sendText('I', text);
  }
  sendText('I', "Voice loop ready");
#ifdef CONFIO_WIFI
  wifiSetup();
#endif
}

void loop() {
  pollPlayback();
  pollMicrophone();
  pollHangup();
  if (!playing) delay(1);
}
