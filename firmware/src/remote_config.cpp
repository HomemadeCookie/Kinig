#include "remote_config.h"

#include "config.h"

PodSettings fallbackSettings() {
  PodSettings settings;
  settings.chunkMs = DEFAULT_CHUNK_MS;
  settings.bufferCount = DEFAULT_BUFFER_COUNT;
  settings.micShift = DEFAULT_MIC_SHIFT;
  settings.fromHub = false;
  return settings;
}

static bool jsonInt(const String& body, const char* key, int& out) {
  String pattern = String("\"") + key + "\":";
  int start = body.indexOf(pattern);
  if (start < 0) return false;
  start += pattern.length();
  while (start < (int)body.length() && body.charAt(start) == ' ') start++;
  int end = start;
  while (end < (int)body.length() && (isDigit(body.charAt(end)) || body.charAt(end) == '-')) end++;
  if (end == start) return false;
  out = body.substring(start, end).toInt();
  return true;
}

PodSettings settingsFromHubJson(const String& body) {
  PodSettings settings = fallbackSettings();
  int chunkMs = 0;
  int bufferCount = 0;
  int micShift = 0;
  if (!jsonInt(body, "chunk_ms", chunkMs)) return settings;
  if (!jsonInt(body, "buffer_count", bufferCount)) return settings;
  if (!jsonInt(body, "mic_shift", micShift)) return settings;
  if (chunkMs < 100 || bufferCount < 1 || micShift < 0 || micShift > 31) return settings;
  settings.chunkMs = chunkMs;
  settings.bufferCount = bufferCount;
  settings.micShift = micShift;
  settings.fromHub = true;
  if (!settingsFitHeap(settings)) return fallbackSettings();
  return settings;
}

size_t chunkBytes(const PodSettings& settings) {
  return (size_t)settings.chunkMs * (size_t)BYTES_PER_MS;
}

bool settingsFitHeap(const PodSettings& settings) {
  // chunk_ms × 32 bytes × buffer_count must fit in the largest free block.
  size_t need = chunkBytes(settings) * (size_t)settings.bufferCount;
  size_t room = ESP.getMaxAllocHeap();
  return need > 0 && need <= room;
}
