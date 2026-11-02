#pragma once
#include "../../common/cloud_connection.h"

CloudConnection cloud;
struct AudioFrame {
  uint8_t type;
  uint32_t generation;
  uint16_t length;
  uint8_t payload[kMaxPlayBytes];
};
struct SpeakerAck {
  uint8_t type;
  uint32_t generation;
  uint32_t pcmBytes;
  uint16_t peak;
};
QueueHandle_t speakerQueue;
QueueHandle_t speakerAcks;
volatile bool wifiAudioFailed = false;
volatile uint32_t wifiGeneration = 0;
bool wifiBusy = false;
bool wifiOnline = false;

// I2S writes run off the networking loop, so full-duplex mic and cancel remain responsive.
void wifiSpeakerTask(void *) {
  AudioFrame frame;
  uint32_t pcmBytes = 0;
  uint16_t peak = 0;
  uint32_t generation = wifiGeneration;
  while (true) {
    if (xQueueReceive(speakerQueue, &frame, portMAX_DELAY) != pdTRUE) continue;
    if (frame.generation != wifiGeneration) continue;
    if (generation != frame.generation) { pcmBytes = 0; peak = 0; generation = frame.generation; }
    if (frame.type == 'P') {
      playMono(frame.payload, frame.length);
      pcmBytes += frame.length;
      for (size_t i = 0; i < frame.length; i += 2) {
        int16_t sample;
        memcpy(&sample, frame.payload + i, sizeof(sample));
        peak = max(peak, static_cast<uint16_t>(abs(static_cast<int32_t>(sample))));
      }
    } else if (frame.type == 'E') finishPlayback();
    const SpeakerAck ack = {frame.type, frame.generation, frame.type == 'E' ? pcmBytes : 0, peak};
    if (frame.type == 'E') { pcmBytes = 0; peak = 0; }
    if (frame.generation == wifiGeneration &&
        xQueueSend(speakerAcks, &ack, pdMS_TO_TICKS(100)) != pdTRUE) wifiAudioFailed = true;
  }
}

void wifiSendFrame(uint8_t type, const uint8_t *data, uint32_t length) {
  if (!cloud.connected) return;
  uint8_t frame[kHeaderSize + kMicSamples * sizeof(int16_t)];
  if (length > sizeof(frame) - kHeaderSize) return;
  const uint8_t header[kHeaderSize] = {'M', 'I', 'C', '1', type,
    static_cast<uint8_t>(length), static_cast<uint8_t>(length >> 8),
    static_cast<uint8_t>(length >> 16), static_cast<uint8_t>(length >> 24)};
  memcpy(frame, header, kHeaderSize);
  if (length) memcpy(frame + kHeaderSize, data, length);
  if (!cloud.socket.sendBIN(frame, kHeaderSize + length)) wifiAudioFailed = true;
}

void wifiEvent(WStype_t type, uint8_t *data, size_t length) {
  if (type == WStype_CONNECTED) {
    cloud.connected = wifiOnline = true;
    wifiBusy = playing = callMode = recording = false;
    wifiAudioFailed = false;
    i2s_zero_dma_buffer(kMicPort);
    Serial.println("Cloud connected");
    sendText('I', "Voice loop ready");
  } else if (type == WStype_DISCONNECTED) {
    ++wifiGeneration;
    cloud.connected = wifiOnline = false;
    wifiBusy = playing = callMode = recording = false;
    xQueueReset(speakerQueue);
    xQueueReset(speakerAcks);
    i2s_zero_dma_buffer(kSpeakerPort);
    Serial.println("Cloud disconnected; reconnecting");
  } else if (type == WStype_BIN) {
    if (length < kHeaderSize || memcmp(data, "SPK1", 4)) { wifiAudioFailed = true; return; }
    const uint32_t bytes = data[5] | data[6] << 8 | data[7] << 16 | static_cast<uint32_t>(data[8]) << 24;
    if (bytes != length - kHeaderSize || bytes > kMaxPlayBytes || bytes % 2) { wifiAudioFailed = true; return; }
    const uint8_t kind = data[4];
    if (kind == 'P' || kind == 'E') {
      AudioFrame frame = {};
      frame.type = kind;
      frame.generation = wifiGeneration;
      frame.length = bytes;
      memcpy(frame.payload, data + kHeaderSize, bytes);
      if (kind == 'P') playing = true;
      if (xQueueSend(speakerQueue, &frame, 0) != pdTRUE) wifiAudioFailed = true;
    } else if (!bytes && kind == 'B') {
      wifiBusy = true;
    } else if (!bytes && (kind == 'C' || kind == 'N')) {
      callMode = kind == 'C';
      recording = false;
      if (callMode) i2s_zero_dma_buffer(kMicPort);
      else wifiBusy = playing = false;
    } else wifiAudioFailed = true;
  }
}

void wifiSetup() {
  speakerQueue = xQueueCreate(20, sizeof(AudioFrame));
  speakerAcks = xQueueCreate(24, sizeof(SpeakerAck));
  if (!speakerQueue || !speakerAcks) fail("WiFi audio queue allocation failed");
  if (xTaskCreatePinnedToCore(wifiSpeakerTask, "speaker", 4096, nullptr, 2, nullptr, 1) != pdPASS)
    fail("WiFi speaker task failed");
  cloud.begin("/device", wifiEvent);
}

void wifiPollPlayback() {
  cloud.poll();
  SpeakerAck ack;
  while (xQueueReceive(speakerAcks, &ack, 0) == pdTRUE) {
    if (ack.generation != wifiGeneration) continue;
    if (ack.type == 'E') { playing = false; wifiBusy = false; }
    sendFrame('A');
    if (ack.type == 'E' && ack.pcmBytes) {
      char text[96];
      snprintf(text, sizeof(text), "Speaker wrote %lu PCM bytes; peak %u/32767",
               static_cast<unsigned long>(ack.pcmBytes), ack.peak);
      sendText('I', text);
    }
  }
  if (wifiAudioFailed) {
    cloud.socket.disconnect();
    wifiAudioFailed = false;
  }
}
