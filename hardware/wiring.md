# Wiring

No board is wired yet. Every pin in `firmware/include/config.h` is `-1` (TBD).

Do not copy pin numbers from a different microphone board into the firmware until this table is filled in from the real PCB.

| Signal | Pin | Notes |
|---|---|---|
| Mic SCK | TBD | I2S bit clock |
| Mic WS | TBD | I2S word select |
| Mic SD | TBD | I2S data in |
| Button | TBD | Active low, with the internal pull-up |
| LED | TBD | On when the pin is high |
| LCD | TBD | Bus (SPI or I2C) and pins are not chosen. The firmware prints the QR on the serial monitor until a panel is known. |
| Power | TBD | |

Until the button pin is set, type `s` in the serial monitor to press the button.
