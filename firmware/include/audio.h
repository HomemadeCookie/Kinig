#pragma once

#include "remote_config.h"

struct AudioChunk {
  uint8_t* data;
  size_t length;
  int seq;
};

void audioStart(const PodSettings& settings);
void audioStop();
// Blocks until the next chunk is ready. Returns false when recording has stopped
// and the queue is empty.
bool audioTakeChunk(AudioChunk& chunk, uint32_t timeoutMs);
void audioReleaseChunk(AudioChunk& chunk);
