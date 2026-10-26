#pragma once

#include <Arduino.h>

#include "remote_config.h"

struct HubSession {
  String id;
  String token;
  String notesUrl;
  PodSettings settings;
  bool ok;
};

bool netConnect();
HubSession netStartSession();
bool netPostChunk(const HubSession& session, int seq, const uint8_t* data, size_t length);
bool netStopSession(const HubSession& session);
