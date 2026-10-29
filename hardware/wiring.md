# Wiring

From the tested mic + OLED sketch (`KinigMicOLED_Fixed.ino`, 2026-10-29). The pins are in `firmware/kinig_pod/config.h`.

| Part | Signal | ESP32 pin |
|---|---|---|
| SSD1306 OLED 128x64 (I2C, 0x3C) | VCC | 3V3 |
| | GND | GND |
| | SDA | GPIO21 |
| | SCL | GPIO22 |
| INMP441 mic (I2S) | VDD | 3V3 |
| | GND | GND |
| | SCK | GPIO26 |
| | WS | GPIO25 |
| | SD | GPIO33 |
| | L/R | GND (left slot) |

The pod has no button. Start and Stop are on the hub's `/host` page.
