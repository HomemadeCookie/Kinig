#pragma once

// Wi-Fi and the hub address. Fill these in for the pocket router.
// "TBD" means the firmware will wait and print a message instead of connecting.
// public_host = "auto" is a hub setting. The ESP32 needs the laptop's IP here.
#define KINIG_WIFI_SSID "TBD"
#define KINIG_WIFI_PASSWORD "TBD"
#define KINIG_HUB_HOST "TBD"
#define KINIG_HUB_PORT 8000

// Pins are TBD until the board is wired. -1 means "not connected".
// Do not invent GPIO numbers, power rails, or part numbers in this file.
constexpr int PIN_MIC_SCK = -1;  // TBD
constexpr int PIN_MIC_WS = -1;   // TBD
constexpr int PIN_MIC_SD = -1;   // TBD
constexpr int PIN_BUTTON = -1;   // TBD
constexpr int PIN_LED = -1;      // TBD
// The LCD bus (SPI or I2C) and its pins are TBD. display.cpp draws to Serial
// until a panel is chosen.

// Used only when the hub cannot be reached or a value does not fit in RAM.
// The capture loop uses the copy stored at session start, not these constants.
constexpr int DEFAULT_CHUNK_MS = 1000;
constexpr int DEFAULT_BUFFER_COUNT = 4;
constexpr int DEFAULT_MIC_SHIFT = 14;

// 16 kHz, 16-bit, mono. 32 bytes per millisecond. Same contract as the hub.
constexpr int SAMPLE_RATE = 16000;
constexpr int BYTES_PER_MS = 32;
