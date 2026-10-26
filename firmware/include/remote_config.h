#pragma once

#include <Arduino.h>

struct PodSettings {
  int chunkMs;
  int bufferCount;
  int micShift;
  bool fromHub;
};

PodSettings fallbackSettings();
PodSettings settingsFromHubJson(const String& body);
bool settingsFitHeap(const PodSettings& settings);
size_t chunkBytes(const PodSettings& settings);
