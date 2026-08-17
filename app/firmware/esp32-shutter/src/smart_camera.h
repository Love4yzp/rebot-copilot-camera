// The smartphone-mode half: Canon's *other* BLE protocol.
//
// Two protocols exist, and which one a body speaks depends on which Bluetooth
// mode it is in:
//
//   "Remote" mode             -> BR-E1 protocol (CanonBLERemote, service 00050000)
//   "Connect to smartphone"   -> this one (services 00010000 / 00030000)
//
// The R-series bodies accept the BR-E1 handshake and then *decline to fire*:
// every control write is acknowledged (status char indicates 0200) and nothing
// happens, on this R6 with write-with-response, two-byte writes and the drive
// mode all set correctly. Independent reverse engineering reached the same
// verdict and points here instead: gkoh/furble drives EOS RP and R6 II through
// this protocol, confirmed firing. The handshake below follows furble's
// CanonEOSSmart step for step.
//
// Cost of this path: someone has to start the smartphone-connect mode on the
// body after every power cycle, and pairing needs a confirmation tap on the
// body's screen. It is not invisible like BR-E1 was supposed to be.

#ifndef REBOT_SMART_CAMERA_H_
#define REBOT_SMART_CAMERA_H_

#include <Arduino.h>
#include <BLEDevice.h>

class SmartCameraClientCallbacks;

class SmartCamera {
public:
  void begin();

  bool isPaired() const;
  bool isConnected() const;

  // Full scan + identification + wait for the operator to confirm on the
  // body's screen. Slow by nature; the caller's timeout has to cover it.
  bool pair(unsigned int scanSeconds, String *error);

  // Re-join a previously paired body and get it into shooting mode.
  bool connect(String *error);

  bool shoot(String *error);
  // This protocol has no half-press.
  bool focus(String *error);

  // Static callback bridge for the pair indication characteristic.
  static void pairIndicationCallback(BLERemoteCharacteristic *pChr,
                                      uint8_t *pData, size_t length,
                                      bool isNotify);

private:
  friend class SmartCameraClientCallbacks;

  bool identify(BLERemoteService *service, String *error);
  bool waitForPairResult(unsigned long timeoutMs);

  BLEClient *pclient = nullptr;
  SmartCameraClientCallbacks *callbacks = nullptr;

  BLEAddress cameraAddress = BLEAddress("");
  bool connectedFlag = false;

  // Derived from the chip id, the way furble does: stable across reflashes, so
  // the body keeps recognising this board as the same device.  Stored as a hex
  // string for the identity write and as raw bytes for the prefix write.
  String identityUuid;
  String deviceName;

  BLERemoteCharacteristic *pShutter = nullptr;
  BLERemoteCharacteristic *pMode = nullptr;
  BLERemoteCharacteristic *pPairIndication = nullptr;
  volatile int pairResult = 0;
};

#endif