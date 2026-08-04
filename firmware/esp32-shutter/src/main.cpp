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

static const unsigned long PAIR_SCAN_SECONDS = REBOT_PAIR_SCAN_SECONDS;
static const size_t MAX_LINE = 96;

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
    // Three states, not two. `unpaired` needs a human with the camera's menu;
    // `disconnected` is the ordinary state of a paired board between shoots and
    // resolves itself on the next frame. Collapsing them sends the operator to
    // the wrong place.
    if (!isPaired()) {
      reply(id, "OK", "unpaired");
    } else {
      reply(id, "OK", camera.isConnected() ? "connected" : "disconnected");
    }
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
      reply(id, "OK", camera.getPairedAddressString().c_str());
    } else {
      reply(id, "ERR", "no camera found in pairing mode");
    }
    return;
  }

  if (strcmp(command, "FOCUS") == 0) {
    if (!isPaired()) {
      reply(id, "ERR", "no camera paired");
      return;
    }
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
    if (!isPaired()) {
      // Reported rather than ignored: the arm is mid-routine and the host has
      // to decide whether to abort. Silently dropping this is how a full run
      // ends with nothing on the card.
      reply(id, "ERR", "no camera paired");
      return;
    }
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
