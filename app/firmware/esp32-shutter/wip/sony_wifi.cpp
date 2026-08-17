#include "sony_wifi.h"

#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

namespace {

// NVS namespace of our own. The Canon library keeps its paired address in
// ArduinoNvs' default namespace; sharing one would make a camera swap on one
// side capable of disturbing the other.
const char *NVS_NAMESPACE = "rebot";
const char *KEY_SSID = "wifi_ssid";
const char *KEY_PASSWORD = "wifi_psk";

// Joining takes a few seconds and the camera is an access point with exactly
// one client, so there is nothing to contend with. Past this it is not slow,
// it is wrong: wrong password, or the app was never started.
const unsigned long JOIN_TIMEOUT_MS = 15000;

// SSDP is a UDP multicast question with no delivery guarantee, so it is asked
// more than once before being believed.
const int SSDP_ATTEMPTS = 3;
const unsigned long SSDP_REPLY_WAIT_MS = 2000;

// Sony's own documentation uses this as the example address and every body
// this has been seen on answers there. Used only when discovery fails, and it
// is a guess that gets validated by the next call rather than trusted.
const char *FALLBACK_ENDPOINT = "http://192.168.122.1:8080/sony/camera";

const char *SSDP_TARGET = "urn:schemas-sony-com:service:ScalarWebAPI:1";

String extractTag(const String &xml, const char *tag) {
  String open = String("<") + tag + ">";
  String close = String("</") + tag + ">";
  int start = xml.indexOf(open);
  if (start < 0) {
    return "";
  }
  start += open.length();
  int end = xml.indexOf(close, start);
  return end < 0 ? "" : xml.substring(start, end);
}

}  // namespace

void SonyCamera::begin() {
  Preferences prefs;
  if (prefs.begin(NVS_NAMESPACE, true)) {
    _ssid = prefs.getString(KEY_SSID, "");
    _password = prefs.getString(KEY_PASSWORD, "");
    prefs.end();
  }
}

bool SonyCamera::hasCredentials() const {
  return _ssid.length() > 0;
}

String SonyCamera::ssid() const {
  return _ssid;
}

void SonyCamera::setCredentials(const String &ssid, const String &password) {
  _ssid = ssid;
  _password = password;
  _endpoint = "";

  Preferences prefs;
  if (prefs.begin(NVS_NAMESPACE, false)) {
    prefs.putString(KEY_SSID, ssid);
    prefs.putString(KEY_PASSWORD, password);
    prefs.end();
  }

  // Drop any existing association: the credentials that are stored and the
  // network the radio is actually on have to be the same thing, or STATUS
  // answers about one while SHOOT uses the other.
  WiFi.disconnect(false, true);
}

bool SonyCamera::isConnected() const {
  return WiFi.status() == WL_CONNECTED && _endpoint.length() > 0;
}

bool SonyCamera::connect(String *error) {
  if (isConnected()) {
    return true;
  }

  if (!hasCredentials()) {
    *error = "no wifi credentials stored";
    return false;
  }

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(_ssid.c_str(), _password.c_str());

    unsigned long deadline = millis() + JOIN_TIMEOUT_MS;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
      delay(100);
    }

    if (WiFi.status() != WL_CONNECTED) {
      // The three ways this fails are indistinguishable from here -- wrong
      // password, app not started, out of range -- so the message names all
      // three rather than guessing one.
      *error = "cannot join " + _ssid + " (app not started, wrong key, or out of range)";
      return false;
    }
  }

  return discoverEndpoint(error);
}

bool SonyCamera::discoverEndpoint(String *error) {
  WiFiUDP udp;
  udp.begin(0);

  String request = String("M-SEARCH * HTTP/1.1\r\n") + "HOST: 239.255.255.250:1900\r\n" +
                   "MAN: \"ssdp:discover\"\r\n" + "MX: 1\r\n" + "ST: " + SSDP_TARGET + "\r\n\r\n";

  String location;
  for (int attempt = 0; attempt < SSDP_ATTEMPTS && location.length() == 0; attempt++) {
    udp.beginPacket(IPAddress(239, 255, 255, 250), 1900);
    udp.print(request);
    udp.endPacket();

    unsigned long deadline = millis() + SSDP_REPLY_WAIT_MS;
    while (millis() < deadline && location.length() == 0) {
      int size = udp.parsePacket();
      if (size <= 0) {
        delay(20);
        continue;
      }

      char buffer[512];
      int read = udp.read(buffer, sizeof(buffer) - 1);
      buffer[read < 0 ? 0 : read] = '\0';

      String reply(buffer);
      int at = reply.indexOf("LOCATION:");
      if (at < 0) {
        at = reply.indexOf("Location:");
      }
      if (at < 0) {
        continue;
      }
      int end = reply.indexOf('\r', at);
      location = reply.substring(at + 9, end < 0 ? reply.length() : end);
      location.trim();
    }
  }

  udp.stop();

  if (location.length() > 0) {
    HTTPClient http;
    http.setTimeout(4000);
    if (http.begin(location) && http.GET() == HTTP_CODE_OK) {
      // The description lists one URL per API service; the camera service is
      // the one that takes pictures, and its path is *not* fixed across bodies
      // -- which is the whole reason discovery happens rather than a constant.
      String base = extractTag(http.getString(), "av:X_ScalarWebAPI_ActionList_URL");
      if (base.length() > 0) {
        base.trim();
        if (base.endsWith("/")) {
          base.remove(base.length() - 1);
        }
        _endpoint = base + "/camera";
      }
    }
    http.end();
  }

  if (_endpoint.length() == 0) {
    _endpoint = FALLBACK_ENDPOINT;
  }

  // Discovery is only believed once the endpoint has answered something. An
  // endpoint that was guessed and never tried is the kind of state that makes
  // the first frame of a shoot the test.
  String response;
  String probe_error;
  if (!call("getAvailableApiList", "", &response, &probe_error)) {
    _endpoint = "";
    *error = "joined " + _ssid + " but no camera API answered (" + probe_error + ")";
    return false;
  }

  // Many bodies refuse to shoot until recording mode has been entered, and the
  // ones that do not simply report the method as unsupported. Either way the
  // answer here changes nothing, so it is not checked.
  String ignored;
  String ignored_error;
  call("startRecMode", "", &ignored, &ignored_error);

  return true;
}

bool SonyCamera::call(const char *method, const char *params, String *response, String *error) {
  if (_endpoint.length() == 0) {
    *error = "no endpoint";
    return false;
  }

  String body = String("{\"method\":\"") + method + "\",\"params\":[" + params +
                "],\"id\":1,\"version\":\"1.0\"}";

  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(_endpoint)) {
    *error = "cannot open " + _endpoint;
    return false;
  }
  http.addHeader("Content-Type", "application/json");

  int status = http.POST(body);
  if (status != HTTP_CODE_OK) {
    *error = String("http ") + status;
    http.end();
    return false;
  }

  *response = http.getString();
  http.end();

  // The API answers 200 for refusals too, with an `error` member instead of a
  // `result` member. Reporting on the status code alone would turn "the camera
  // said no" into a frame the host believes it took.
  if (response->indexOf("\"error\"") >= 0) {
    *error = *response;
    return false;
  }
  if (response->indexOf("\"result\"") < 0) {
    *error = "no result in reply";
    return false;
  }
  return true;
}

bool SonyCamera::focus(String *error) {
  if (!connect(error)) {
    return false;
  }
  String response;
  return call("actHalfPressShutter", "", &response, error);
}

bool SonyCamera::shoot(String *error) {
  if (!connect(error)) {
    return false;
  }

  String response;
  if (call("actTakePicture", "", &response, error)) {
    return true;
  }

  // "Long shooting" is not a failure: the camera accepted the frame and is
  // still working on it (long exposure, or writing a big raw). The API says so
  // by refusing with error code 40403, and the follow-up call is what collects
  // the result. Treating it as a miss is how a shoot ends up retrying frames it
  // already took.
  if (error->indexOf("40403") >= 0) {
    String ignored;
    return call("awaitTakePicture", "", &ignored, error);
  }
  return false;
}
