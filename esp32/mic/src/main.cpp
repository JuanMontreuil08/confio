#include <Arduino.h>
#include <driver/i2s.h>

constexpr int kButtonPin = 23;
constexpr int kClockPin = 18;
constexpr int kWordSelectPin = 5;
constexpr int kDataPin = 22;
constexpr uint32_t kSampleRate = 16000;
constexpr unsigned long kDebounceMs = 50;
constexpr size_t kSamples = 256;
constexpr i2s_port_t kPort = I2S_NUM_0;

// Frames consist of a magic marker, a type, a little-endian length and payload.
void sendFrame(uint8_t type, const uint8_t *data, uint32_t length) {
  const uint8_t header[] = {'M', 'I', 'C', '1', type,
                            static_cast<uint8_t>(length),
                            static_cast<uint8_t>(length >> 8),
                            static_cast<uint8_t>(length >> 16),
                            static_cast<uint8_t>(length >> 24)};
  Serial.write(header, sizeof(header));
  if (length) Serial.write(data, length);
}

void setup() {
  Serial.begin(921600);
  pinMode(kButtonPin, INPUT_PULLUP);
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = kSampleRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  // Keep the validated stereo framing; a single mic drives only one slot.
  config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = kSamples;
  config.use_apll = false;
  config.tx_desc_auto_clear = false;
  config.fixed_mclk = 0;
  i2s_pin_config_t pins = {};
  pins.bck_io_num = kClockPin;
  pins.ws_io_num = kWordSelectPin;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kDataPin;
  if (i2s_driver_install(kPort, &config, 0, nullptr) != ESP_OK ||
      i2s_set_pin(kPort, &pins) != ESP_OK) {
    const char message[] = "Microphone initialization failed";
    sendFrame('E', reinterpret_cast<const uint8_t *>(message), sizeof(message) - 1);
    while (true) delay(1000);
  }
}

void loop() {
  static bool previousReading = HIGH;
  static bool stableState = HIGH;
  static unsigned long changedAt = 0;
  static int32_t raw[kSamples];
  static int16_t pcm[kSamples];

  bool reading = digitalRead(kButtonPin);
  if (reading != previousReading) {
    changedAt = millis();
    previousReading = reading;
  }
  if (reading != stableState && millis() - changedAt >= kDebounceMs) {
    stableState = reading;
    if (stableState == LOW) {
      i2s_zero_dma_buffer(kPort);
      sendFrame('S', nullptr, 0);
    } else {
      sendFrame('F', nullptr, 0);
    }
  }
  if (stableState == LOW) {
    size_t bytesRead = 0;
    if (i2s_read(kPort, raw, sizeof(raw), &bytesRead, pdMS_TO_TICKS(30)) == ESP_OK) {
      size_t count = bytesRead / sizeof(raw[0]);
      for (size_t i = 0; i < count; ++i) {
        int32_t sample = raw[i] >> 16;
        pcm[i] = static_cast<int16_t>(constrain(sample, -32768, 32767));
      }
      if (count) sendFrame('D', reinterpret_cast<const uint8_t *>(pcm), count * sizeof(pcm[0]));
    }
  } else {
    delay(5);
  }
}
