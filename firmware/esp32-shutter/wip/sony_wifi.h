// The Sony half: shutter over the camera's own Wi-Fi access point.
//
// Canon bodies are driven by pretending to be a BR-E1 remote over BLE. Sony has
// no equivalent -- the a6500's Bluetooth does location sync only, and its menu
// has no remote item at all. What it does have is "Smart Remote Embedded", a
// PlayMemories app that turns the camera into an access point and serves the
// Sony Camera Remote API over HTTP. So the same two verbs, FOCUS and SHOOT,
// reach this body through an entirely different radio.
//
// Two consequences worth knowing before wiring anything to this:
//
// **One radio.** While the board is on the camera's access point it cannot be on
// any other network, which is why the host link stays on USB.
//
// **The app is started by hand.** The access point only exists while that app is
// running on the camera, and the camera drops it on power-off. No API brings it
// back; someone walks over and starts it again. That is Sony's cost, not this
// file's.

#ifndef REBOT_SONY_WIFI_H_
#define REBOT_SONY_WIFI_H_

#include <Arduino.h>

class SonyCamera {
public:
  // Credentials live in NVS so a camera swap is a serial command, not a
  // reflash. The board is usually inside a machine when the SSID changes.
  void begin();
  bool hasCredentials() const;
  void setCredentials(const String &ssid, const String &password);
  String ssid() const;

  bool isConnected() const;

  // Join the access point and find the API endpoint. Safe to call when already
  // connected -- it returns immediately.
  bool connect(String *error);

  bool focus(String *error);
  bool shoot(String *error);

private:
  String _ssid;
  String _password;
  //: Filled by discovery. Empty until `connect()` has succeeded once.
  String _endpoint;

  bool discoverEndpoint(String *error);
  bool call(const char *method, const char *params, String *response, String *error);
};

#endif
