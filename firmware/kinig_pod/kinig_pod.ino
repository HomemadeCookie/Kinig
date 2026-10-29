/*
  KINIG pod: ESP32 + INMP441 mic + SSD1306 128x64 OLED.
  Target: original 38-pin ESP32 Dev Module, Arduino-ESP32 3.x.
  Libraries: Adafruit GFX, Adafruit SSD1306. Wi-Fi, mDNS, HTTP and I2S
  come with the ESP32 core.

  The pod is a remote microphone. The hub laptop decides everything:
    1. Start its own Wi-Fi (Kinig-Pod). The hub laptop and phones join it.
       Find the hub by mDNS, or by asking each connected device.
    2. POST /pods/hello, show the 4-digit pairing code.
    3. Poll GET /pods/{id}. The host approves the code on /host.
    4. "record": stream chunk_ms of audio per POST /sessions/{id}/audio,
       show the join QR.
    5. "stop": send the last audio, POST /sessions/{id}/stop.
    6. "show_qr": keep showing the join QR until the host clears it.
  The QR comes from the hub as raw modules (GET /pods/{id}/qr), so the pod
  needs no QR library.

  Network name is in config.h. Serial Monitor: 115200 baud.
*/

#include <Arduino.h>
#include <ESPmDNS.h>
#include <ESP_I2S.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_netif.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#include "config.h"

// 100 kHz for both Wire and the SSD1306 transfers, as in the tested sketch.
Adafruit_SSD1306 oled(128, 64, &Wire, -1, 100000UL, 100000UL);
I2SClass microphone;
bool apReady = false;
bool oledReady = false;
bool micReady = false;

String hubBase;
String podId;
uint32_t lastPollMs = 0;
int pollFailures = 0;
String shownKey;

// ------------------------------------------------------------ session state

struct Chunk {
  uint8_t* data;
  size_t length;
  int seq;
};

String sessionId;
int chunkMs = 1000;
int bufferCount = 4;
int micShift = 14;
uint32_t recordStartMs = 0;

volatile bool capturing = false;
TaskHandle_t captureHandle = nullptr;
TaskHandle_t uploadHandle = nullptr;
QueueHandle_t fullQueue = nullptr;
QueueHandle_t freeQueue = nullptr;
uint8_t** buffers = nullptr;
int buffersAllocated = 0;

uint8_t qr[1 + 41 * 41];
int qrSize = 0;
String qrSession;

// ------------------------------------------------------------ tiny JSON

// The hub sends flat JSON with no spaces, e.g. {"state":"record","chunk_ms":1000}.
bool jsonString(const String& body, const char* key, String& out) {
  String pattern = String("\"") + key + "\":\"";
  int start = body.indexOf(pattern);
  if (start < 0) return false;
  start += pattern.length();
  int end = body.indexOf('"', start);
  if (end < 0) return false;
  out = body.substring(start, end);
  return true;
}

bool jsonInt(const String& body, const char* key, int& out) {
  String pattern = String("\"") + key + "\":";
  int start = body.indexOf(pattern);
  if (start < 0) return false;
  start += pattern.length();
  int end = start;
  while (end < (int)body.length() && (isDigit(body.charAt(end)) || body.charAt(end) == '-')) end++;
  if (end == start) return false;
  out = body.substring(start, end).toInt();
  return true;
}

// ------------------------------------------------------------ display

// Redraw only when the screen content changes. Serial gets the same lines.
bool changed(const String& key) {
  if (key == shownKey) return false;
  shownKey = key;
  return true;
}

void showText(const String& title, const String& line1, const String& line2 = "") {
  if (!changed("T|" + title + "|" + line1 + "|" + line2)) return;
  Serial.println(title + " | " + line1 + (line2.length() ? " | " + line2 : ""));
  if (!oledReady) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextWrap(false);
  oled.setTextSize(2);
  oled.setCursor(0, 0);
  oled.print(title);
  oled.setTextSize(1);
  oled.setCursor(0, 30);
  oled.print(line1);
  oled.setCursor(0, 46);
  oled.print(line2);
  oled.display();
}

void showPairing(const String& code) {
  if (!changed("P|" + code)) return;
  Serial.println("PAIR CODE " + code + ". Approve it on the hub's /host page.");
  if (!oledReady) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("Pair with hub");
  oled.setTextSize(3);
  oled.setCursor((128 - 18 * (int)code.length()) / 2, 20);
  oled.print(code);
  oled.setTextSize(1);
  oled.setCursor(0, 54);
  oled.print("Approve on laptop");
  oled.display();
}

// QR on the left, two or three short lines on the right.
void showQr(const String& line1, const String& line2, const String& line3) {
  if (!changed("Q|" + qrSession + "|" + line1 + "|" + line2 + "|" + line3)) return;
  if (!oledReady || qrSize == 0) {
    Serial.println(line1 + " " + line2 + " " + line3);
    return;
  }
  int scale = max(1, 64 / (qrSize + 2));
  int box = (qrSize + 2) * scale;
  int top = (64 - box) / 2;
  oled.clearDisplay();
  // Light background, dark modules: phones read it like a printed code.
  oled.fillRect(0, top, box, box, SSD1306_WHITE);
  for (int y = 0; y < qrSize; y++) {
    for (int x = 0; x < qrSize; x++) {
      if (qr[1 + y * qrSize + x]) {
        oled.fillRect((x + 1) * scale, top + (y + 1) * scale, scale, scale, SSD1306_BLACK);
      }
    }
  }
  int left = box + 4;
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextWrap(false);
  oled.setTextSize(1);
  oled.setCursor(left, 8);
  oled.print(line1);
  oled.setCursor(left, 26);
  oled.print(line2);
  oled.setCursor(left, 44);
  oled.print(line3);
  oled.display();
}

// ------------------------------------------------------------ audio tasks

void captureTask(void*) {
  const size_t length = (size_t)chunkMs * BYTES_PER_MS;
  // mic_shift is for the raw 32-bit word. The INMP441 is 24-bit, MSB aligned.
  const int shift = max(0, micShift - 8);
  const float divisor = (float)(1 << shift);
  static int32_t raw[256];
  float lastIn = 0.0f;
  float lastOut = 0.0f;
  int seq = 0;

  while (capturing) {
    uint8_t* buffer = nullptr;
    if (xQueueReceive(freeQueue, &buffer, pdMS_TO_TICKS(200)) != pdTRUE) continue;
    size_t filled = 0;
    while (filled < length && capturing) {
      if (!micReady) {
        // No mic: send silence at the real rate so the flow still works.
        memset(buffer, 0, length);
        filled = length;
        vTaskDelay(pdMS_TO_TICKS(chunkMs));
        break;
      }
      size_t want = min(sizeof(raw), (length - filled) * 2);  // 4 bytes in, 2 out
      size_t got = microphone.readBytes(reinterpret_cast<char*>(raw), want);
      size_t samples = got / sizeof(int32_t);
      for (size_t i = 0; i < samples; i++) {
        // DC blocker, then scale to 16 bits and clip.
        float in = (float)(raw[i] >> 8);
        float out = in - lastIn + 0.995f * lastOut;
        lastIn = in;
        lastOut = out;
        int32_t value = (int32_t)(out / divisor);
        if (value > 32767) value = 32767;
        if (value < -32768) value = -32768;
        buffer[filled++] = (uint8_t)(value & 0xff);
        buffer[filled++] = (uint8_t)((value >> 8) & 0xff);
      }
    }
    if (filled == 0) {
      xQueueSend(freeQueue, &buffer, 0);
      break;
    }
    Chunk chunk = {buffer, filled, seq++};
    xQueueSend(fullQueue, &chunk, portMAX_DELAY);
  }
  captureHandle = nullptr;
  vTaskDelete(nullptr);
}

void uploadTask(void*) {
  HTTPClient http;
  http.setReuse(true);
  http.setTimeout(5000);
  const String base = hubBase + "/sessions/" + sessionId + "/audio?seq=";
  while (true) {
    Chunk chunk;
    if (xQueueReceive(fullQueue, &chunk, pdMS_TO_TICKS(200)) != pdTRUE) {
      if (!capturing && captureHandle == nullptr) break;  // drained
      continue;
    }
    int code = 0;
    for (int attempt = 0; attempt < 3; attempt++) {
      http.begin(base + String(chunk.seq));
      http.addHeader("Content-Type", "application/octet-stream");
      code = http.POST(chunk.data, chunk.length);
      http.end();
      if (code == 200 || code == 409) break;
      delay(200 * (attempt + 1));
    }
    xQueueSend(freeQueue, &chunk.data, 0);
    if (code == 409) capturing = false;  // the host already closed the session
    else if (code != 200) Serial.printf("Chunk %d dropped (HTTP %d)\n", chunk.seq, code);
  }
  uploadHandle = nullptr;
  vTaskDelete(nullptr);
}

bool streaming() {
  return captureHandle != nullptr || uploadHandle != nullptr;
}

void freeBuffers() {
  if (fullQueue) vQueueDelete(fullQueue);
  if (freeQueue) vQueueDelete(freeQueue);
  fullQueue = freeQueue = nullptr;
  if (buffers) {
    for (int i = 0; i < buffersAllocated; i++) free(buffers[i]);
    free(buffers);
  }
  buffers = nullptr;
  buffersAllocated = 0;
}

bool startStreaming() {
  const size_t length = (size_t)chunkMs * BYTES_PER_MS;
  buffers = (uint8_t**)calloc(bufferCount, sizeof(uint8_t*));
  for (int i = 0; buffers && i < bufferCount; i++) {
    // Leave room for Wi-Fi and HTTP. Two buffers is the least that streams.
    if (ESP.getFreeHeap() < length + 40000) break;
    buffers[i] = (uint8_t*)malloc(length);
    if (!buffers[i]) break;
    buffersAllocated++;
  }
  if (buffersAllocated < 2) {
    freeBuffers();
    Serial.printf("Not enough RAM for chunk_ms=%d. Lower it in hub/config/pod.toml.\n", chunkMs);
    return false;
  }
  fullQueue = xQueueCreate(buffersAllocated, sizeof(Chunk));
  freeQueue = xQueueCreate(buffersAllocated, sizeof(uint8_t*));
  for (int i = 0; i < buffersAllocated; i++) xQueueSend(freeQueue, &buffers[i], 0);
  Serial.printf("Streaming session %s: chunk_ms=%d buffers=%d mic_shift=%d\n",
                sessionId.c_str(), chunkMs, buffersAllocated, micShift);
  capturing = true;
  recordStartMs = millis();
  // Core 1 captures (with loop). Core 0 uploads, next to the Wi-Fi stack.
  xTaskCreatePinnedToCore(captureTask, "kinig-cap", 6144, nullptr, 2, &captureHandle, 1);
  xTaskCreatePinnedToCore(uploadTask, "kinig-up", 8192, nullptr, 1, &uploadHandle, 0);
  return true;
}

// Stop capture, let the upload task send what is queued, free the buffers.
void stopStreaming() {
  capturing = false;
  uint32_t started = millis();
  while (streaming() && millis() - started < 15000) delay(20);
  if (streaming()) Serial.println("Upload did not finish in 15 s.");
  else freeBuffers();
}

// ------------------------------------------------------------ hub calls

bool startAccessPoint() {
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);  // steadier uploads
  const char* password = strlen(KINIG_AP_PASSWORD) >= 8 ? KINIG_AP_PASSWORD : nullptr;
  if (!WiFi.softAP(KINIG_AP_SSID, password, KINIG_AP_CHANNEL, 0, KINIG_AP_MAX_CLIENTS)) {
    showText("Error", "Wi-Fi did not", "start");
    delay(2000);
    return false;
  }
  Serial.print("Wi-Fi ");
  Serial.print(KINIG_AP_SSID);
  Serial.print(" is up. Pod IP ");
  Serial.println(WiFi.softAPIP());
  MDNS.begin("kinig-pod");
  return true;
}

// A device on our Wi-Fi that answers GET /host/state on the hub port is the hub.
bool probeConnectedDevices() {
  wifi_sta_list_t stations = {};
  if (esp_wifi_ap_get_sta_list(&stations) != ESP_OK || stations.num == 0) return false;
  esp_netif_pair_mac_ip_t pairs[ESP_WIFI_MAX_CONN_NUM] = {};
  int count = min((int)stations.num, (int)ESP_WIFI_MAX_CONN_NUM);
  for (int i = 0; i < count; i++) memcpy(pairs[i].mac, stations.sta[i].mac, 6);
  esp_netif_t* ap = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
  if (ap == nullptr || esp_netif_dhcps_get_clients_by_mac(ap, count, pairs) != ESP_OK) return false;
  for (int i = 0; i < count; i++) {
    if (pairs[i].ip.addr == 0) continue;
    String base = "http://" + IPAddress(pairs[i].ip.addr).toString() + ":" + String(KINIG_HUB_PORT);
    HTTPClient http;
    http.setConnectTimeout(800);
    http.setTimeout(800);
    http.begin(base + "/host/state");
    int code = http.GET();
    http.end();
    if (code == 200) {
      hubBase = base;
      return true;
    }
  }
  return false;
}

bool findHub() {
  if (String(KINIG_HUB_HOST).length() > 0) {
    hubBase = String("http://") + KINIG_HUB_HOST + ":" + String(KINIG_HUB_PORT);
    return true;
  }
  int devices = WiFi.softAPgetStationNum();
  if (devices == 0) {
    showText("Join Wi-Fi", "On the laptop:", KINIG_AP_SSID);
    delay(500);
    return false;
  }
  showText("Finding", "Looking for", "the hub");
  int found = MDNS.queryService("kinig", "tcp");
  if (found > 0) {
    hubBase = "http://" + MDNS.address(0).toString() + ":" + String(MDNS.port(0));
  } else if (!probeConnectedDevices()) {
    showText("No hub", "Run the hub on", "the laptop");
    delay(1500);
    return false;
  }
  Serial.println("Hub found at " + hubBase);
  return true;
}

bool hello() {
  HTTPClient http;
  http.setTimeout(4000);
  http.begin(hubBase + "/pods/hello");
  http.addHeader("Content-Type", "application/json");
  int code = http.POST("{\"mac\":\"" + WiFi.macAddress() + "\"}");
  String body = http.getString();
  http.end();
  if (code != 200 || !jsonString(body, "pod_id", podId)) {
    showText("No hub", "Hello failed", "HTTP " + String(code));
    hubBase = "";  // look again; the laptop may have changed
    delay(1500);
    return false;
  }
  Serial.println("Pod id " + podId);
  return true;
}

bool fetchQr() {
  HTTPClient http;
  http.setTimeout(4000);
  http.begin(hubBase + "/pods/" + podId + "/qr");
  int code = http.GET();
  bool ok = false;
  if (code == 200) {
    int length = http.getSize();
    if (length > 1 && length <= (int)sizeof(qr)) {
      WiFiClient* stream = http.getStreamPtr();
      stream->setTimeout(3000);
      int got = stream->readBytes(qr, length);
      int size = qr[0];
      ok = got == length && length == 1 + size * size;
      if (ok) qrSize = size;
    }
  }
  http.end();
  if (!ok) Serial.printf("QR download failed (HTTP %d)\n", code);
  return ok;
}

void postStop(const String& id) {
  HTTPClient http;
  http.setTimeout(10000);
  http.begin(hubBase + "/sessions/" + id + "/stop");
  int code = http.POST("");
  http.end();
  Serial.printf("Session stop: HTTP %d\n", code);
}

String clock(uint32_t ms) {
  uint32_t seconds = ms / 1000;
  char text[8];
  snprintf(text, sizeof(text), "%02lu:%02lu", (unsigned long)(seconds / 60), (unsigned long)(seconds % 60));
  return String(text);
}

void ensureQr(const String& id) {
  if (qrSession == id && qrSize > 0) return;
  qrSize = 0;
  if (fetchQr()) qrSession = id;
}

void handleState(const String& body) {
  String state;
  if (!jsonString(body, "state", state)) return;
  String id;
  jsonString(body, "session_id", id);

  if (state == "pairing") {
    String code;
    jsonString(body, "code", code);
    showPairing(code);
  } else if (state == "ready") {
    if (streaming()) stopStreaming();
    sessionId = "";
    showText("KINIG", "Ready. Press", "Start on laptop");
  } else if (state == "record") {
    if (id != sessionId || !streaming()) {
      if (streaming()) stopStreaming();
      sessionId = id;
      jsonInt(body, "chunk_ms", chunkMs);
      jsonInt(body, "buffer_count", bufferCount);
      jsonInt(body, "mic_shift", micShift);
      if (!startStreaming()) {
        showText("Error", "Low RAM. Lower", "chunk_ms");
        return;
      }
    }
    ensureQr(id);
    showQr("REC " + clock(millis() - recordStartMs), "Wi-Fi:", KINIG_AP_SSID);
  } else if (state == "stop") {
    showText("Stopping", "Sending the", "last audio");
    if (streaming()) stopStreaming();
    postStop(id);
  } else if (state == "show_qr") {
    if (streaming()) stopStreaming();
    sessionId = id;
    String status;
    jsonString(body, "status", status);
    ensureQr(id);
    String label = status == "done" ? "Notes ok" : status == "error" ? "Error" : "Working";
    showQr(label, "Wi-Fi:", KINIG_AP_SSID);
  }
}

void poll() {
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(hubBase + "/pods/" + podId);
  int code = http.GET();
  String body = code == 200 ? http.getString() : String();
  http.end();

  if (code == 404) {
    // Hub restarted or the host pressed Forget. Pair again.
    if (streaming()) stopStreaming();
    podId = "";
    sessionId = "";
    return;
  }
  if (code != 200) {
    if (++pollFailures >= 5) {
      showText("No hub", "Lost the hub", "Searching again");
      hubBase = "";
      podId = "";
      pollFailures = 0;
    }
    return;
  }
  pollFailures = 0;
  handleState(body);
}

// ------------------------------------------------------------ setup / loop

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nKINIG pod starting");

  if (Wire.begin(OLED_SDA, OLED_SCL, 100000)) {
    Wire.setTimeOut(50);
    oledReady = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, true, false);
  }
  Serial.println(oledReady ? "OLED: ready" : "OLED: not found, using Serial only");

  microphone.setPins(MIC_BCLK, MIC_WS, -1, MIC_DATA);
  micReady = microphone.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT,
                              I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
  Serial.println(micReady ? "Mic: ready" : "Mic: FAILED, sending silence");

  showText("KINIG", "Starting", micReady ? "mic ok" : "mic missing");
}

void loop() {
  if (!apReady) {
    apReady = startAccessPoint();
    return;
  }
  if (hubBase.isEmpty()) {
    findHub();
    return;
  }
  if (podId.isEmpty()) {
    hello();
    return;
  }
  if (millis() - lastPollMs >= POLL_INTERVAL_MS) {
    lastPollMs = millis();
    poll();
  }
  delay(20);
}
