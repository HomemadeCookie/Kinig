#include <Arduino.h>

#include "audio.h"
#include "config.h"
#include "controls.h"
#include "display.h"
#include "net.h"

// Untested on hardware. Pins, the LCD, and the Wi-Fi name are TBD in config.h.

enum class PodState { Idle, Recording, Processing, ShowQr, Error };

static PodState gState = PodState::Idle;
static HubSession gSession;
static TaskHandle_t gUploadTask = nullptr;
static volatile bool gUploading = false;

static void enter(PodState next, const char* label, LedPattern pattern) {
  gState = next;
  displayStatus(label);
  controlsSetPattern(pattern);
}

static void uploadTask(void*) {
  while (gUploading) {
    AudioChunk chunk;
    if (!audioTakeChunk(chunk, 200)) continue;
    bool sent = netPostChunk(gSession, chunk.seq, chunk.data, chunk.length);
    audioReleaseChunk(chunk);
    if (!sent) {
      enter(PodState::Error, "Upload failed", LedPattern::Error);
      gUploading = false;
      break;
    }
  }
  gUploadTask = nullptr;
  vTaskDelete(nullptr);
}

static void startRecording() {
  if (!netConnect()) {
    enter(PodState::Error, "No Wi-Fi", LedPattern::Error);
    return;
  }
  gSession = netStartSession();
  if (!gSession.ok) {
    enter(PodState::Error, "Hub unreachable", LedPattern::Error);
    return;
  }
  audioStart(gSession.settings);
  gUploading = true;
  // Core 0 uploads. Capture stays on core 1 inside audioStart.
  xTaskCreatePinnedToCore(uploadTask, "kinig-up", 8192, nullptr, 1, &gUploadTask, 0);
  enter(PodState::Recording, "Recording", LedPattern::Recording);
}

static void stopRecording() {
  // Let the upload task finish the chunk it is sending, then free the buffers.
  gUploading = false;
  for (int i = 0; i < 400 && gUploadTask != nullptr; i++) delay(20);
  audioStop();
  enter(PodState::Processing, "Processing", LedPattern::Processing);
  bool stopped = netStopSession(gSession);
  if (!stopped || gSession.notesUrl.length() == 0) {
    enter(PodState::Error, "Stop failed", LedPattern::Error);
    return;
  }
  displayShowQr(gSession.notesUrl);
  enter(PodState::ShowQr, "Scan for notes", LedPattern::ShowQr);
}

void setup() {
  Serial.begin(115200);
  displayBegin();
  controlsBegin();
  enter(PodState::Idle, "Idle. Press the button, or type s if the button pin is TBD.", LedPattern::Idle);
}

void loop() {
  controlsTick();
  if (!controlsButtonPressed()) {
    delay(10);
    return;
  }
  if (gState == PodState::Idle || gState == PodState::Error || gState == PodState::ShowQr) {
    if (gState != PodState::Idle) {
      enter(PodState::Idle, "Idle", LedPattern::Idle);
      if (gState == PodState::Idle) return;
    }
    startRecording();
    return;
  }
  if (gState == PodState::Recording) stopRecording();
}
