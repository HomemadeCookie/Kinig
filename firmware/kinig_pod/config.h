#pragma once

#include <stdint.h>

// Wi-Fi goes in secrets.h. Everything else comes from the hub.

// The pocket router (or phone hotspot) the hub laptop is also on.
// Put the real values in secrets.h next to this file (gitignored):
//   #define KINIG_WIFI_SSID "name"
//   #define KINIG_WIFI_PASSWORD "password"
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef KINIG_WIFI_SSID
#define KINIG_WIFI_SSID "TBD"
#define KINIG_WIFI_PASSWORD "TBD"
#endif

// Leave empty to find the hub by mDNS (_kinig._tcp, announced by the hub at
// startup). Set the laptop's IP, e.g. "192.168.4.2", only if mDNS is blocked.
#define KINIG_HUB_HOST ""
#define KINIG_HUB_PORT 8000

// Wiring, from the tested mic + OLED sketch (KinigMicOLED_Fixed.ino).
// OLED SSD1306 128x64: VCC 3V3, GND, SDA 21, SCL 22, address 0x3C.
// INMP441: VDD 3V3, GND, SCK 26, WS 25, SD 33, L/R to GND (left slot).
constexpr int OLED_SDA = 21;
constexpr int OLED_SCL = 22;
constexpr uint8_t OLED_ADDRESS = 0x3C;
constexpr int MIC_BCLK = 26;
constexpr int MIC_WS = 25;
constexpr int MIC_DATA = 33;

// 16 kHz, 16-bit, mono, little-endian. Same contract as docs/audio-format.md.
constexpr uint32_t SAMPLE_RATE = 16000;
constexpr int BYTES_PER_MS = 32;

// How often the pod asks the hub what to do.
constexpr uint32_t POLL_INTERVAL_MS = 700;
