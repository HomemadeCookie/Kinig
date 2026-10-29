#pragma once

#include <stdint.h>

// Network settings live here. Everything else comes from the hub.

// The pod hosts its own Wi-Fi. The hub laptop and the phones join it.
// No router or campus Wi-Fi is involved. Open network: no password to type.
// The pod is at 192.168.4.1; the laptop usually gets 192.168.4.2.
#define KINIG_AP_SSID "Kinig-Pod"
#define KINIG_AP_PASSWORD ""      // 8+ characters for WPA2, or "" for open
constexpr int KINIG_AP_CHANNEL = 6;
constexpr int KINIG_AP_MAX_CLIENTS = 8;  // laptop + phones; the ESP32 allows up to 10

// Leave empty to find the hub automatically: mDNS (_kinig._tcp, announced by
// the hub), then each device on the pod's Wi-Fi. Set an IP only to force one.
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
