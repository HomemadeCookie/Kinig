#pragma once

#include <Arduino.h>

// The panel is TBD. These functions print status and a QR to Serial so the
// firmware compiles before a display library is chosen. Replace displayShowQr
// with the real LCD driver once the part is known. Do not invent a panel here.

void displayBegin();
void displayStatus(const char* line);
void displayShowQr(const String& url);
void displayClear();
