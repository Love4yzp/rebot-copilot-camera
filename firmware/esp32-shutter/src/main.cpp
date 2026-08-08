// Shutter bridge: USB CDC line protocol in, Canon BLE remote out.
//
// ESP32-Canon-BLE-Remote is a *library*, not firmware. It knows how to speak
// Canon's BLE remote protocol; it has no opinion about when to fire. Its own
// example wires that decision to a GPIO button interrupt. Here the decision
// comes from the host over USB, which is the only part this file adds.
//
// Protocol (see backend/shutter/protocol.py, which must agree):
//
//     host -> board:  #<id> <COMMAND>\n
//     board -> host:  #<id> OK\n
//                     #<id> ERR <reason>\n
//     board -> host:  READY <version>\n      (unprompted, on boot)
//
// The id is echoed so a reply that arrives after the host gave up cannot be
// mistaken for the next command's success. READY tells the host the board
// reset and therefore lost its BLE pairing, without the host having to poll.

#include <Arduino.h>
#include <CanonBLERemote.h>
#include <Preferences.h>
#include "smart_camera.h"

// Deployment-dependent, so they come from platformio.ini rather than from here
// -- a second machine in the same room needs its own BLE name, and two boards
// answering to one name means a camera pairs with whichever it noticed first.
// The defaults keep this file buildable on its own.
#ifndef REBOT_BLE_NAME
#define REBOT_BLE_NAME "rebot-copilot"
#endif
#ifndef REBOT_PAIR_SCAN_SECONDS
#define REBOT_PAIR_SCAN_SECONDS 30
#endif
#ifndef REBOT_SERIAL_BAUD
#define REBOT_SERIAL_BAUD 115200
#endif

// Shows up in the camera's pairing list.
static CanonBLERemote camera(REBOT_BLE_NAME);

// Smartphone-mode protocol — used when the camera is in "connect to
// smartphone" mode instead of "remote" mode.  R-series bodies refuse to
// fire in remote mode; this is the working alternative.
static SmartCamera smartCamera;

// ── Backend management ──────────────────────────────────────────────────────
//
// The board can be paired with a camera via either the BR-E1 remote protocol or
// the smartphone-mode protocol.  Which one is active is persisted in NVS so
// reboots don't need a re-pair.
static const char *NVS_NS = "rebot";
static const char *KEY_BACKEND = "backend";

enum Backend { BACKEND_BLE, BACKEND_SMART, BACKEND_NONE };

static Backend getBackend() {
  Preferences prefs;
  prefs.begin(NVS_NS, true);
  String s = prefs.getString(KEY_BACKEND, "smart");
  prefs.end();
  if (s == "ble") return BACKEND_BLE;
  return BACKEND_SMART;  // default to smart mode
}

static void setBackend(Backend b) {
  Preferences prefs;
  prefs.begin(NVS_NS, false);
  prefs.putString(KEY_BACKEND, b == BACKEND_BLE ? "ble" : "smart");
  prefs.end();
}

static const unsigned long PAIR_SCAN_SECONDS = REBOT_PAIR_SCAN_SECONDS;
static const size_t MAX_LINE = 96;

// How long `SCAN` listens. Short: it is a diagnostic someone is watching.
static const unsigned long SCAN_SECONDS = 5;

// What `getPairedAddressString()` reads back when nothing was ever stored.
//
// CanonBLERemote initialises its address to `BLEAddress("")`, and that
// constructor returns early on any string that is not 17 characters -- without
// writing the address bytes. They read as zeros only because `camera` below has
// static storage duration and is therefore zero-initialised before any
// constructor runs. Keep it a file-scope object: as a local it would inherit
// whatever was on the stack, and this check would pass on an unpaired board.
static const char *UNPAIRED = "00:00:00:00:00:00";

static char line[MAX_LINE];
static size_t lineLength = 0;

static void reply(long id, const char *status, const char *detail = nullptr) {
  if (detail == nullptr) {
    Serial.printf("#%ld %s\n", id, status);
  } else {
    Serial.printf("#%ld %s %s\n", id, status, detail);
  }
  Serial.flush();
}

// Parses "#<id> <COMMAND>". Returns false on anything malformed, so a stray
// byte on the wire is ignored rather than acted on.
static bool parse(const char *input, long *id, const char **command) {
  if (input[0] != '#') {
    return false;
  }

  char *end = nullptr;
  *id = strtol(input + 1, &end, 10);
  if (end == input + 1 || *end != ' ') {
    return false;
  }

  while (*end == ' ') {
    end++;
  }
  if (*end == '\0') {
    return false;
  }

  *command = end;
  return true;
}

// Has a camera ever been paired? This is the question `isConnected()` looks
// like it answers and does not.
//
// `CanonBLERemote::init()` only reads the stored address out of NVS -- it does
// not connect, and nothing else does either until the first `trigger()` or
// `focus()`, which connect lazily. So `isConnected()` is false after every boot
// even with a camera paired, sitting awake, in range. Refusing to shoot on that
// basis would mean the board never connects at all: the one call that would
// have connected is the one being refused. On a paired board the first frame
// after a reset is simply slower than the rest.
static bool isPaired() {
  return camera.getPairedAddressString() != UNPAIRED;
}

static void handle(long id, const char *command) {
  if (strcmp(command, "PING") == 0) {
    // Deliberately does not check the camera. PING answers "is the host-to-
    // board link alive", which is a different question from "is the camera
    // reachable" and needs to stay answerable when the camera is asleep.
    reply(id, "OK");
    return;
  }

  if (strcmp(command, "STATUS") == 0) {
    // Check the active backend, then the other one.  Report the first
    // connected state found; if none, report the best available state.
    Backend backend = getBackend();
    bool smartPaired = smartCamera.isPaired();
    bool blePaired = isPaired();

    // Try the active backend first.
    if (backend == BACKEND_SMART) {
      if (smartCamera.isConnected()) {
        reply(id, "OK", "connected");
        return;
      }
      if (smartPaired) {
        reply(id, "OK", "disconnected");
        return;
      }
    } else {
      if (camera.isConnected()) {
        reply(id, "OK", "connected");
        return;
      }
      if (blePaired) {
        reply(id, "OK", "disconnected");
        return;
      }
    }

    // Check the other backend.
    if (backend == BACKEND_SMART) {
      if (blePaired) {
        reply(id, "OK", "disconnected");
        return;
      }
    } else {
      if (smartPaired) {
        reply(id, "OK", "disconnected");
        return;
      }
    }

    reply(id, "OK", "unpaired");
    return;
  }

  if (strcmp(command, "SCAN") == 0) {
    // Diagnostics, and the reason it earns its place: `PAIR` failing says only
    // "no camera found in pairing mode", which is unfalsifiable from the host
    // side -- camera asleep, camera in the wrong Bluetooth mode, camera not
    // advertising the remote service, board's radio dead, all one sentence.
    // This lists what the board can actually hear.
    //
    // The `# scan` lines are deliberately not `#<id>` replies: the host's
    // decoder drops unrecognised lines (backend/shutter/protocol.py), so a
    // diagnostic that runs mid-session cannot be mistaken for a command result.
    BLEScan *scan = BLEDevice::getScan();
    scan->setActiveScan(true);
    BLEScanResults found = scan->start(SCAN_SECONDS, false);

    for (int i = 0; i < found.getCount(); i++) {
      BLEAdvertisedDevice device = found.getDevice(i);

      // *Every* advertised service, not just the first. CanonBLERemote's scan
      // callback compares against `getServiceUUID()`, which returns only the
      // first one -- so a body that advertises its smartphone service ahead of
      // its remote service is a body this library can never pair with, and the
      // symptom is an ordinary "camera not found". A diagnostic that repeated
      // the same first-only assumption could not tell those two apart.
      String uuids;
      for (int u = 0; u < device.getServiceUUIDCount(); u++) {
        uuids += (u ? "," : "");
        uuids += device.getServiceUUID(u).toString().c_str();
      }

      Serial.printf("# scan %s rssi=%d name=%s uuid=%s\n", device.getAddress().toString().c_str(),
                    device.getRSSI(), device.haveName() ? device.getName().c_str() : "-",
                    uuids.length() ? uuids.c_str() : "-");
    }
    Serial.flush();
    scan->clearResults();

    char detail[32];
    snprintf(detail, sizeof(detail), "%d devices", found.getCount());
    reply(id, "OK", detail);
    return;
  }

  if (strcmp(command, "CHARS") == 0) {
    // Diagnostic: list all characteristics of the shutter control service.
    // Prints UUID, handle, and properties (R=read, W=write-with-response,
    // N=write-without-response, T=notify, I=indicate) prefixed with "# chars"
    // so the host decoder ignores them as unrecognised lines.
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
    camera.printCharacteristics();
    reply(id, "OK", "chars listed");
    return;
  }

  if (strcmp(command, "PAIR") == 0) {
    // Into a local first: C++ leaves the order of function arguments
    // unspecified, so writing the `pair()` call and a `isConnected()` detail as
    // two arguments of one `reply()` lets the compiler read the connection
    // state *before* pairing runs. It compiles, it looks symmetrical with the
    // rest of this function, and the reason reported is whatever was true a
    // scan earlier.
    bool paired = camera.pair(PAIR_SCAN_SECONDS);
    if (paired) {
      setBackend(BACKEND_BLE);
      reply(id, "OK", camera.getPairedAddressString().c_str());
    } else {
      reply(id, "ERR", "no camera found in pairing mode");
    }
    return;
  }

  if (strcmp(command, "PAIRSMART") == 0) {
    // Smartphone-mode pairing.  The camera must be in "connect to smartphone"
    // mode (not "remote" mode).  The user must confirm on the camera's screen
    // within 60 s.
    String error;
    if (smartCamera.pair(PAIR_SCAN_SECONDS, &error)) {
      setBackend(BACKEND_SMART);
      reply(id, "OK", "smart paired");
    } else {
      reply(id, "ERR", error.c_str());
    }
    return;
  }

  if (strcmp(command, "FOCUS") == 0) {
    if (!isPaired() && !smartCamera.isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }

    Backend backend = getBackend();
    // Fall back to the other backend if the active one is not paired.
    if (backend == BACKEND_SMART && !smartCamera.isPaired()) {
      backend = BACKEND_BLE;
    } else if (backend == BACKEND_BLE && !isPaired()) {
      backend = BACKEND_SMART;
    }

    if (backend == BACKEND_SMART) {
      // Smart mode has no half-press.
      String error;
      if (smartCamera.focus(&error)) {
        reply(id, "OK");
      } else {
        reply(id, "ERR", error.c_str());
      }
      return;
    }

    // BLE remote mode.
    // `focus()` connects first if it has to, so this can block for as long as
    // the BLE stack's connect timeout (30 s in the Arduino core). The host's
    // own timeout is shorter and its request ids exist precisely so the late
    // reply that follows is counted, not mistaken for the next command's.
    if (camera.focus()) {
      reply(id, "OK");
    } else {
      reply(id, "ERR", "camera unreachable");
    }
    return;
  }

  if (strcmp(command, "SHOOT") == 0) {
    if (!isPaired() && !smartCamera.isPaired()) {
      // Reported rather than ignored: the arm is mid-routine and the host has
      // to decide whether to abort. Silently dropping this is how a full run
      // ends with nothing on the card.
      reply(id, "ERR", "no camera paired");
      return;
    }

    Backend backend = getBackend();
    // Fall back to the other backend if the active one is not paired.
    if (backend == BACKEND_SMART && !smartCamera.isPaired()) {
      backend = BACKEND_BLE;
    } else if (backend == BACKEND_BLE && !isPaired()) {
      backend = BACKEND_SMART;
    }

    if (backend == BACKEND_SMART) {
      String error;
      if (smartCamera.shoot(&error)) {
        reply(id, "OK");
      } else {
        reply(id, "ERR", error.c_str());
      }
      return;
    }

    // BLE remote mode.
    // False here means the connect attempt failed -- asleep, switched off, out
    // of range. It never means the camera declined: once the link is up, the
    // library writes the characteristic and returns true without waiting for
    // the body to say anything back. So a frame the camera silently dropped
    // still reports OK, and only the card can settle it.
    if (camera.trigger()) {
      reply(id, "OK");
    } else {
      reply(id, "ERR", "camera unreachable");
    }
    return;
  }

  if (strcmp(command, "ARM") == 0) {
    // Diagnostic: re-send the [0x03, ' ', name, ' '] arming write on the id
    // characteristic. Pairing does this once; some bodies may want it on every
    // connection.
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
    if (camera.armWithName()) {
      reply(id, "OK");
    } else {
      reply(id, "ERR", "arm write failed");
    }
    return;
  }

  if (strcmp(command, "SUBS") == 0) {
    // Diagnostic: subscribe to the status characteristic's indications. The
    // body answers button writes on it -- `0100` for a fired shot, `0200` for
    // "received but declining" -- which is the only feedback this protocol
    // offers and the difference between iterating blind and iterating on
    // evidence. Lines print as "# ind <hex>".
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
    if (!camera.subscribeStatus()) {
      reply(id, "ERR", "no status characteristic");
      return;
    }
    reply(id, "OK", camera.readStatusHex().c_str());
    return;
  }

  if (strncmp(command, "RAW ", 4) == 0) {
    // Diagnostic: write arbitrary bytes to the control characteristic without
    // a reflash. Tokens are space-separated; a hex token is one write of one or
    // two bytes (`8C`, `8C00`), a `d<ms>` token is a pause. After every write
    // the status characteristic is read and printed as `# status <hex>`.
    // Example: `RAW 8C00 d200 0C00`.
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }

    char work[MAX_LINE];
    strncpy(work, command + 4, sizeof(work) - 1);
    work[sizeof(work) - 1] = '\0';

    char *save = nullptr;
    for (char *tok = strtok_r(work, " ", &save); tok != nullptr; tok = strtok_r(nullptr, " ", &save)) {
      if (tok[0] == 'd') {
        delay((unsigned long)atoi(tok + 1));
        continue;
      }

      size_t n = strlen(tok);
      if (n == 0 || n % 2 != 0 || n > 4) {
        reply(id, "ERR", "bad hex token");
        return;
      }
      uint8_t bytes[2];
      for (size_t b = 0; b < n / 2; b++) {
        char byteStr[3] = {tok[b * 2], tok[b * 2 + 1], '\0'};
        bytes[b] = (uint8_t)strtol(byteStr, nullptr, 16);
      }
      if (!camera.writeControlBytes(bytes, n / 2)) {
        reply(id, "ERR", "camera unreachable");
        return;
      }
      delay(250);
      Serial.printf("# status %s\n", camera.readStatusHex().c_str());
      Serial.flush();
    }
    reply(id, "OK");
    return;
  }

  if (strncmp(command, "RAWCHR ", 7) == 0) {
    // Diagnostic: write to a specific characteristic by UUID.
    // Format: RAWCHR <uuid> <hexbytes>
    // Example: RAWCHR 00050005 01
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }

    // Parse UUID and hex bytes from the remaining command.
    const char *rest = command + 7;
    while (*rest == ' ') rest++;
    const char *uuidStart = rest;
    while (*rest && *rest != ' ') rest++;
    if (*rest == '\0') {
      reply(id, "ERR", "missing uuid");
      return;
    }
    // Null-terminate the UUID by temporarily replacing the space.
    // We operate on a copy instead.
    char lineCopy[MAX_LINE];
    strncpy(lineCopy, command + 7, sizeof(lineCopy) - 1);
    lineCopy[sizeof(lineCopy) - 1] = '\0';
    char *uuidStr = strtok(lineCopy, " ");
    char *hexStr = strtok(nullptr, " ");
    if (uuidStr == nullptr || hexStr == nullptr) {
      reply(id, "ERR", "need uuid and hex");
      return;
    }

    size_t n = strlen(hexStr);
    if (n == 0 || n % 2 != 0 || n > 32) {
      reply(id, "ERR", "bad hex");
      return;
    }
    uint8_t bytes[16];
    size_t byteCount = n / 2;
    for (size_t b = 0; b < byteCount; b++) {
      char byteStr[3] = {hexStr[b * 2], hexStr[b * 2 + 1], '\0'};
      bytes[b] = (uint8_t)strtol(byteStr, nullptr, 16);
    }
    if (!camera.writeRawChar(uuidStr, bytes, byteCount)) {
      reply(id, "ERR", "write failed");
      return;
    }
    delay(250);
    Serial.printf("# status %s\n", camera.readStatusHex().c_str());
    Serial.flush();
    reply(id, "OK");
    return;
  }

  if (strncmp(command, "READCHR ", 8) == 0) {
    // Diagnostic: read a specific characteristic by UUID.
    // Format: READCHR <uuid>
    // Example: READCHR 00050004
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
    const char *uuidStr = command + 8;
    while (*uuidStr == ' ') uuidStr++;
    if (*uuidStr == '\0') {
      reply(id, "ERR", "missing uuid");
      return;
    }
    String result = camera.readRawChar(uuidStr);
    if (result == "nochar") {
      reply(id, "ERR", "characteristic not found");
      return;
    }
    if (result == "disconnected") {
      reply(id, "ERR", "camera unreachable");
      return;
    }
    reply(id, "OK", result.c_str());
    return;
  }

  if (strcmp(command, "SECNEG") == 0) {
    // Diagnostic: initiate BLE encryption on the existing connection.
    // Some characteristics (00050005/000a/000c) require encryption and cannot
    // be written to without it.  This command starts the encryption handshake.
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
    if (camera.negotiateSecurity()) {
      reply(id, "OK", "security negotiation initiated");
    } else {
      reply(id, "ERR", "security negotiation failed");
    }
    return;
  }

  reply(id, "ERR", "unknown command");
}

void setup() {
  Serial.begin(REBOT_SERIAL_BAUD);

  // Wait briefly for the host to open the port, but do not block forever --
  // the board must still work when it is powered from a plain USB charger.
  unsigned long deadline = millis() + 3000;
  while (!Serial && millis() < deadline) {
    delay(10);
  }

  camera.init();
  smartCamera.begin();

  Serial.printf("READY %s\n", FW_VERSION);
  Serial.flush();
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      if (lineLength == 0) {
        continue;
      }
      line[lineLength] = '\0';

      long id = 0;
      const char *command = nullptr;
      if (parse(line, &id, &command)) {
        handle(id, command);
      }
      lineLength = 0;
      continue;
    }

    if (lineLength < MAX_LINE - 1) {
      line[lineLength++] = c;
    } else {
      // Overlong line: drop it whole rather than acting on a truncated
      // command that might parse as something else.
      lineLength = 0;
    }
  }
}
