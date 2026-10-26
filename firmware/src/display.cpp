#include "display.h"

#include <qrcode.h>

void displayBegin() {
  Serial.println("LCD driver is TBD. Status and QR are printed on Serial.");
}

void displayClear() {
  Serial.println("---");
}

void displayStatus(const char* line) {
  Serial.println(line);
}

void displayShowQr(const String& url) {
  // Version 6 holds a notes URL with a session token. ECC_LOW keeps it small.
  QRCode code;
  uint8_t buffer[qrcode_getBufferSize(6)];
  int err = qrcode_initText(&code, buffer, 6, ECC_LOW, url.c_str());
  if (err != 0) {
    Serial.println("QR did not fit. Notes URL:");
    Serial.println(url);
    return;
  }
  Serial.println(url);
  for (uint8_t y = 0; y < code.size; y++) {
    for (uint8_t x = 0; x < code.size; x++) {
      Serial.print(qrcode_getModule(&code, x, y) ? "██" : "  ");
    }
    Serial.println();
  }
}
