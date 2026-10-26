#include "net.h"

#include <HTTPClient.h>
#include <WiFi.h>

#include "config.h"
#include "controls.h"

static HTTPClient gHttp;

static String hubBase() {
  return String("http://") + KINIG_HUB_HOST + ":" + String(KINIG_HUB_PORT);
}

static bool jsonString(const String& body, const char* key, String& out) {
  String pattern = String("\"") + key + "\":\"";
  int start = body.indexOf(pattern);
  if (start < 0) return false;
  start += pattern.length();
  int end = body.indexOf('"', start);
  if (end < 0) return false;
  out = body.substring(start, end);
  return true;
}

bool netConnect() {
  if (String(KINIG_WIFI_SSID) == "TBD" || String(KINIG_HUB_HOST) == "TBD") {
    Serial.println("Wi-Fi SSID or hub host is TBD. Set them in include/config.h.");
    return false;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(KINIG_WIFI_SSID, KINIG_WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi did not connect.");
    return false;
  }
  Serial.print("Pod IP ");
  Serial.println(WiFi.localIP());
  gHttp.setReuse(true);
  gHttp.setTimeout(2000);
  return true;
}

HubSession netStartSession() {
  HubSession session;
  session.ok = false;
  session.settings = fallbackSettings();
  if (WiFi.status() != WL_CONNECTED) return session;

  gHttp.begin(hubBase() + "/sessions");
  gHttp.addHeader("Content-Type", "application/json");
  int code = gHttp.POST("{\"client\":\"esp32\"}");
  String body = gHttp.getString();
  gHttp.end();
  if (code != 200) {
    Serial.println("Hub did not start a session. Using config.h defaults.");
    return session;
  }
  if (!jsonString(body, "session_id", session.id)) return session;
  if (!jsonString(body, "token", session.token)) return session;
  jsonString(body, "notes_url", session.notesUrl);
  session.settings = settingsFromHubJson(body);
  session.ok = true;
  Serial.print("Session ");
  Serial.print(session.id);
  Serial.print(" chunk_ms ");
  Serial.println(session.settings.chunkMs);
  return session;
}

bool netPostChunk(const HubSession& session, int seq, const uint8_t* data, size_t length) {
  String url = hubBase() + "/sessions/" + session.id + "/audio?seq=" + String(seq);
  for (int attempt = 0; attempt < 3; attempt++) {
    gHttp.begin(url);
    gHttp.addHeader("Content-Type", "application/octet-stream");
    int code = gHttp.POST(const_cast<uint8_t*>(data), length);
    gHttp.end();
    if (code == 200) return true;
    controlsSetPattern(LedPattern::Error);
    delay(200 * (attempt + 1));
  }
  return false;
}

bool netStopSession(const HubSession& session) {
  gHttp.begin(hubBase() + "/sessions/" + session.id + "/stop");
  int code = gHttp.POST("");
  gHttp.end();
  return code == 200;
}
