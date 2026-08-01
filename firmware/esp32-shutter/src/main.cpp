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

// Shows up in the camera's pairing list.
static CanonBLERemote camera("rebot-copilot");

static const unsigned long PAIR_SCAN_SECONDS = 30;
static const size_t MAX_LINE = 96;

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

static void handle(long id, const char *command) {
  if (strcmp(command, "PING") == 0) {
    // Deliberately does not check the camera. PING answers "is the host-to-
    // board link alive", which is a different question from "is the camera
    // reachable" and needs to stay answerable when the camera is asleep.
    reply(id, "OK");
    return;
  }

  if (strcmp(command, "STATUS") == 0) {
    reply(id, "OK", camera.isConnected() ? "connected" : "disconnected");
    return;
  }

  if (strcmp(command, "PAIR") == 0) {
    reply(id, camera.pair(PAIR_SCAN_SECONDS) ? "OK" : "ERR",
          camera.isConnected() ? nullptr : "no camera found in pairing mode");
    return;
  }

  if (strcmp(command, "FOCUS") == 0) {
    if (!camera.isConnected()) {
      reply(id, "ERR", "camera not connected");
      return;
    }
    reply(id, camera.focus() ? "OK" : "ERR", "focus rejected by camera");
    return;
  }

  if (strcmp(command, "SHOOT") == 0) {
    if (!camera.isConnected()) {
      // Reported rather than ignored: the arm is mid-routine and the host has
      // to decide whether to abort. Silently dropping this is how a full run
      // ends with nothing on the card.
      reply(id, "ERR", "camera not connected");
      return;
    }
    reply(id, camera.trigger() ? "OK" : "ERR", "shutter rejected by camera");
    return;
  }

  reply(id, "ERR", "unknown command");
}

void setup() {
  Serial.begin(115200);

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
