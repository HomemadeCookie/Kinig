#include "audio.h"

#include <driver/i2s.h>
#include <string.h>

#include "config.h"

static PodSettings gSettings;
static QueueHandle_t gFull = nullptr;
static QueueHandle_t gFree = nullptr;
static uint8_t** gBuffers = nullptr;
static int gBufferCount = 0;
static volatile bool gRunning = false;
static TaskHandle_t gCaptureTask = nullptr;

static bool pinsReady() {
  return PIN_MIC_SCK >= 0 && PIN_MIC_WS >= 0 && PIN_MIC_SD >= 0;
}

static bool installI2s() {
  if (!pinsReady()) {
    Serial.println("Mic pins are TBD. Capture will send silence until config.h is filled in.");
    return false;
  }
  i2s_config_t config = {};
  config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 4;
  config.dma_buf_len = 256;
  config.use_apll = false;

  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_MIC_SCK;
  pins.ws_io_num = PIN_MIC_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = PIN_MIC_SD;

  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) return false;
  return true;
}

static int16_t shiftSample(int32_t raw, int shift) {
  // The INMP441 puts a 24-bit sample in a 32-bit word. mic_shift comes from the hub.
  return (int16_t)(raw >> shift);
}

static void fillFromMic(uint8_t* dest, size_t length) {
  size_t filled = 0;
  int shift = gSettings.micShift;
  while (filled + 1 < length && gRunning) {
    int32_t raw = 0;
    size_t got = 0;
    i2s_read(I2S_NUM_0, &raw, sizeof(raw), &got, pdMS_TO_TICKS(100));
    if (got != sizeof(raw)) continue;
    int16_t sample = shiftSample(raw, shift);
    dest[filled] = (uint8_t)(sample & 0xff);
    dest[filled + 1] = (uint8_t)((sample >> 8) & 0xff);
    filled += 2;
  }
  while (filled < length) dest[filled++] = 0;
}

static void captureTask(void*) {
  int seq = 0;
  size_t length = chunkBytes(gSettings);
  bool mic = installI2s();
  while (gRunning) {
    uint8_t* buffer = nullptr;
    if (xQueueReceive(gFree, &buffer, pdMS_TO_TICKS(200)) != pdTRUE) continue;
    if (mic) {
      fillFromMic(buffer, length);
    } else {
      memset(buffer, 0, length);
      vTaskDelay(pdMS_TO_TICKS(gSettings.chunkMs));
    }
    AudioChunk chunk;
    chunk.data = buffer;
    chunk.length = length;
    chunk.seq = seq++;
    xQueueSend(gFull, &chunk, portMAX_DELAY);
  }
  if (mic) i2s_driver_uninstall(I2S_NUM_0);
  gCaptureTask = nullptr;
  vTaskDelete(nullptr);
}

void audioStart(const PodSettings& settings) {
  audioStop();
  gSettings = settings;
  gBufferCount = settings.bufferCount;
  size_t length = chunkBytes(settings);
  gBuffers = new uint8_t*[gBufferCount];
  gFull = xQueueCreate(gBufferCount, sizeof(AudioChunk));
  gFree = xQueueCreate(gBufferCount, sizeof(uint8_t*));
  for (int i = 0; i < gBufferCount; i++) {
    gBuffers[i] = new uint8_t[length];
    xQueueSend(gFree, &gBuffers[i], 0);
  }
  gRunning = true;
  // Core 1 captures. Core 0 uploads. The queue is the only link.
  xTaskCreatePinnedToCore(captureTask, "kinig-cap", 8192, nullptr, 2, &gCaptureTask, 1);
}

void audioStop() {
  gRunning = false;
  for (int i = 0; i < 50 && gCaptureTask != nullptr; i++) vTaskDelay(pdMS_TO_TICKS(20));
  if (gFull) {
    vQueueDelete(gFull);
    gFull = nullptr;
  }
  if (gFree) {
    vQueueDelete(gFree);
    gFree = nullptr;
  }
  if (gBuffers) {
    for (int i = 0; i < gBufferCount; i++) delete[] gBuffers[i];
    delete[] gBuffers;
    gBuffers = nullptr;
  }
  gBufferCount = 0;
}

bool audioTakeChunk(AudioChunk& chunk, uint32_t timeoutMs) {
  if (!gFull) return false;
  return xQueueReceive(gFull, &chunk, pdMS_TO_TICKS(timeoutMs)) == pdTRUE;
}

void audioReleaseChunk(AudioChunk& chunk) {
  if (gFree && chunk.data) xQueueSend(gFree, &chunk.data, 0);
  chunk.data = nullptr;
}
