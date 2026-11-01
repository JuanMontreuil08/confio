#include <Arduino.h>
#include <driver/i2s.h>
#include "audio_info.h"

constexpr i2s_port_t kPort = I2S_NUM_1;
constexpr int kBitClockPin = 16;   // RX2 on the DevKit silkscreen
constexpr int kWordSelectPin = 17; // TX2 on the DevKit silkscreen
constexpr int kDataPin = 19;
constexpr int kVolumePercent = 80; // PCM amplitude, not perceived loudness
constexpr size_t kChunkSamples = 256;

extern const uint8_t audioStart[] asm("_binary_data_voice_pcm_start");
extern const uint8_t audioEnd[] asm("_binary_data_voice_pcm_end");

void check(esp_err_t result, const char *operation) {
  if (result == ESP_OK) return;
  Serial.printf("ERROR %s: %s\n", operation, esp_err_to_name(result));
  while (true) delay(1000);
}

void writeAudio(const int16_t *samples, size_t bytes) {
  const uint8_t *cursor = reinterpret_cast<const uint8_t *>(samples);
  while (bytes) {
    size_t written = 0;
    check(i2s_write(kPort, cursor, bytes, &written, pdMS_TO_TICKS(1000)), "i2s_write");
    if (!written) {
      Serial.println("ERROR: no se pudo enviar audio al amplificador");
      while (true) delay(1000);
    }
    cursor += written;
    bytes -= written;
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("Prueba MAX98357A: BCLK=RX2(16), LRC=TX2(17), DIN=D19");
  Serial.printf("Tu WAV se reproduce al %d%% de amplitud y se repite tras 5 segundos.\n", kVolumePercent);
  Serial.println("No usa WiFi, boton, microfono, Telnyx ni Cartesia.");
  if (static_cast<size_t>(audioEnd - audioStart) != kAudioSamples * sizeof(int16_t)) {
    Serial.println("ERROR: el audio integrado no coincide con sus metadatos");
    while (true) delay(1000);
  }

  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX);
  config.sample_rate = kAudioSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 4;
  config.dma_buf_len = kChunkSamples;
  config.tx_desc_auto_clear = true;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = kBitClockPin;
  pins.ws_io_num = kWordSelectPin;
  pins.data_out_num = kDataPin;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  check(i2s_driver_install(kPort, &config, 0, nullptr), "i2s_driver_install");
  check(i2s_set_pin(kPort, &pins), "i2s_set_pin");
  check(i2s_zero_dma_buffer(kPort), "i2s_zero_dma_buffer");
  delay(3000);
}

void loop() {
  static int16_t stereo[kChunkSamples * 2];
  Serial.println("Reproduciendo tu voz...");
  for (size_t offset = 0; offset < kAudioSamples; offset += kChunkSamples) {
    const size_t count = min(kChunkSamples, static_cast<size_t>(kAudioSamples) - offset);
    for (size_t i = 0; i < count; ++i) {
      int16_t sample;
      memcpy(&sample, audioStart + (offset + i) * sizeof(sample), sizeof(sample));
      const int16_t scaled = static_cast<int32_t>(sample) * kVolumePercent / 100;
      // Duplicate voice into both slots so any amp channel selection plays it.
      stereo[2 * i] = scaled;
      stereo[2 * i + 1] = scaled;
    }
    writeAudio(stereo, count * 2 * sizeof(int16_t));
  }
  // Push silence through the complete DMA queue before clearing it.
  memset(stereo, 0, sizeof(stereo));
  for (int i = 0; i < 4; ++i) writeAudio(stereo, sizeof(stereo));
  delay(100);
  check(i2s_zero_dma_buffer(kPort), "i2s_zero_dma_buffer");
  Serial.println("Fin. Repite en 5 segundos.");
  delay(5000);
}
