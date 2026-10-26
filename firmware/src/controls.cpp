#include "controls.h"

#include <Arduino.h>

#include "config.h"

static LedPattern gPattern = LedPattern::Off;
static bool gLedOn = false;
static uint32_t gNextToggle = 0;
static bool gReady = false;

void controlsBegin() {
  if (PIN_BUTTON >= 0) pinMode(PIN_BUTTON, INPUT_PULLUP);
  if (PIN_LED >= 0) pinMode(PIN_LED, OUTPUT);
  gReady = false;
}

void controlsSetPattern(LedPattern pattern) {
  gPattern = pattern;
}

static uint32_t intervalFor(LedPattern pattern) {
  if (pattern == LedPattern::Idle) return 800;
  if (pattern == LedPattern::Processing) return 250;
  if (pattern == LedPattern::Error) return 80;
  return 0;
}

void controlsTick() {
  uint32_t now = millis();
  bool solid = gPattern == LedPattern::Recording;
  bool dark = gPattern == LedPattern::Off || gPattern == LedPattern::ShowQr;
  if (solid || dark) {
    gLedOn = solid;
  } else if (now >= gNextToggle) {
    gLedOn = !gLedOn;
    gNextToggle = now + intervalFor(gPattern);
  }
  if (PIN_LED >= 0) digitalWrite(PIN_LED, gLedOn ? HIGH : LOW);
}

bool controlsButtonPressed() {
  if (PIN_BUTTON >= 0) {
    bool down = digitalRead(PIN_BUTTON) == LOW;
    bool pressed = down && !gReady;
    gReady = down;
    return pressed;
  }
  if (!Serial.available()) return false;
  char key = (char)Serial.read();
  return key == 's' || key == 'S';
  return false;
}
