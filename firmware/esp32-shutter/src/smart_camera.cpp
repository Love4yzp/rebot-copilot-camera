// Smartphone-mode protocol implementation.
//
// Follows gkoh/furble's CanonEOSSmart step for step (confirmed on RP and R6 II).
// See smart_camera.h for the protocol overview and the rationale for choosing
// this path over the BR-E1 remote protocol.

#include "smart_camera.h"

#include <Preferences.h>
#include <esp_mac.h>

#include <cstring>

// ---------------------------------------------------------------------------
// File-scope pointer for the static callback bridge.
// ---------------------------------------------------------------------------

static SmartCamera *g_smartCameraInstance = nullptr;

// ---------------------------------------------------------------------------
// Service / characteristic UUIDs  (Canon base 0000d8492fffa821)
// ---------------------------------------------------------------------------

static const char *SERVICE_SMART_STR    = "00010000-0000-1000-0000-d8492fffa821";
static const char *CHAR_NAME_WRITE_STR  = "00010006-0000-1000-0000-d8492fffa821";
static const char *CHAR_IDENTITY_STR    = "0001000a-0000-1000-0000-d8492fffa821";
static const char *SERVICE_MODE_STR     = "00030000-0000-1000-0000-d8492fffa821";
static const char *CHAR_MODE_STR        = "00030010-0000-1000-0000-d8492fffa821";
static const char *CHAR_SHUTTER_STR     = "00030030-0000-1000-0000-d8492fffa821";

static const BLEUUID SERVICE_SMART(SERVICE_SMART_STR);
static const BLEUUID CHAR_NAME_WRITE(CHAR_NAME_WRITE_STR);
static const BLEUUID CHAR_IDENTITY(CHAR_IDENTITY_STR);
static const BLEUUID SERVICE_MODE_UUID(SERVICE_MODE_STR);
static const BLEUUID CHAR_MODE_UUID(CHAR_MODE_STR);
static const BLEUUID CHAR_SHUTTER_UUID(CHAR_SHUTTER_STR);

// ---------------------------------------------------------------------------
// Deterministic chip-derived identity UUID  (furble-compatible xorshift32)
// ---------------------------------------------------------------------------

static constexpr size_t UUID128_LEN = 16;
static constexpr size_t UUID128_AS_32_LEN = (UUID128_LEN / sizeof(uint32_t));

typedef struct {
  uint32_t uint32[UUID128_AS_32_LEN];
  uint8_t uint8[UUID128_LEN];
} uuid128_t;

static uint32_t xorshift32(uint32_t x) {
  x ^= x << 13;
  x ^= x << 17;
  x ^= x << 5;
  return x;
}

// Derived once, cached for the lifetime of the board.
static const uuid128_t &getIdentityUuid() {
  static uuid128_t uuid = []() {
    uuid128_t u;
    uint64_t mac = 0;
    esp_efuse_mac_get_default(reinterpret_cast<uint8_t *>(&mac));
    uint32_t chip_id = static_cast<uint32_t>(mac);
    for (size_t i = 0; i < UUID128_AS_32_LEN; i++) {
      chip_id = xorshift32(chip_id);
      u.uint32[i] = chip_id;
    }
    return u;
  }();
  return uuid;
}

// ---------------------------------------------------------------------------
// NVS persistence  (namespace "rebot")
// ---------------------------------------------------------------------------

static const char *NVS_NS = "rebot";
static const char *KEY_ADDR = "smart_addr";

static void saveAddress(const BLEAddress &addr) {
  Preferences prefs;
  prefs.begin(NVS_NS, false);
  // toString() is non-const in Arduino 2.0.x, so copy to a local.
  BLEAddress tmp = addr;
  prefs.putString(KEY_ADDR, tmp.toString().c_str());
  prefs.end();
}

static void clearAddress() {
  Preferences prefs;
  prefs.begin(NVS_NS, false);
  prefs.remove(KEY_ADDR);
  prefs.end();
}

static bool loadAddress(BLEAddress &addr) {
  Preferences prefs;
  prefs.begin(NVS_NS, true);
  String s = prefs.getString(KEY_ADDR, "");
  prefs.end();
  if (s.length() == 0) {
    return false;
  }
  addr = BLEAddress(s.c_str());
  return true;
}

// ---------------------------------------------------------------------------
// Client callbacks
// ---------------------------------------------------------------------------

class SmartCameraClientCallbacks : public BLEClientCallbacks {
public:
  explicit SmartCameraClientCallbacks(SmartCamera *camera) : m_camera(camera) {}

  void onConnect(BLEClient *) override {
    if (m_camera != nullptr) {
      m_camera->connectedFlag = true;
    }
  }

  void onDisconnect(BLEClient *) override {
    if (m_camera != nullptr) {
      m_camera->connectedFlag = false;
      m_camera->pShutter = nullptr;
      m_camera->pMode = nullptr;
      m_camera->pPairIndication = nullptr;
    }
  }

private:
  SmartCamera *m_camera;
};

// ---------------------------------------------------------------------------
// Static callback bridge  (defined here so it can access the instance pointer)
// ---------------------------------------------------------------------------

void SmartCamera::pairIndicationCallback(BLERemoteCharacteristic *pChr,
                                          uint8_t *pData, size_t length,
                                          bool isNotify) {
  (void)pChr;
  // The pair indication is sent as an indication (isNotify == false), not a
  // notification.  pData[0] == 0x02 means accepted, 0x03 means rejected.
  if (!isNotify && length > 0 && g_smartCameraInstance != nullptr) {
    g_smartCameraInstance->pairResult = pData[0];
  }
}

// ---------------------------------------------------------------------------
// SmartCamera implementation
// ---------------------------------------------------------------------------

void SmartCamera::begin() {
  // Register this instance as the callback target.
  g_smartCameraInstance = this;

  // Derive the identity UUID from the chip ID.
  const uuid128_t &uuid = getIdentityUuid();
  char hex[UUID128_LEN * 2 + 1] = {0};
  for (size_t i = 0; i < UUID128_LEN; i++) {
    sprintf(hex + i * 2, "%02x", uuid.uint8[i]);
  }
  identityUuid = String(hex);

  // Device name: REBOT_BLE_NAME with a short chip-ID suffix for uniqueness.
  uint64_t mac = 0;
  esp_efuse_mac_get_default(reinterpret_cast<uint8_t *>(&mac));
  uint32_t chip_suffix = static_cast<uint32_t>(mac) & 0xFFFFF;
  // REBOT_BLE_NAME is defined in platformio.ini.
  deviceName = String(REBOT_BLE_NAME) + "-" + String(chip_suffix, HEX);

  // Create the BLE client (BLEDevice must already be initialised by the
  // caller — typically via CanonBLERemote::init()).
  pclient = BLEDevice::createClient();
  callbacks = new SmartCameraClientCallbacks(this);
  pclient->setClientCallbacks(callbacks);
}

bool SmartCamera::isPaired() const {
  BLEAddress dummy("");
  return loadAddress(dummy);
}

bool SmartCamera::isConnected() const {
  return connectedFlag;
}

bool SmartCamera::pair(unsigned int scanSeconds, String *error) {
  // 1. Scan for a device advertising 00010000.
  BLEScan *scan = BLEDevice::getScan();
  scan->setActiveScan(true);
  scan->clearResults();

  BLEAddress foundAddr("");
  String foundName;

  BLEScanResults found = scan->start(scanSeconds, false);
  for (int i = 0; i < found.getCount(); i++) {
    BLEAdvertisedDevice device = found.getDevice(i);
    if (device.haveServiceUUID() &&
        device.isAdvertisingService(SERVICE_SMART)) {
      foundAddr = device.getAddress();
      foundName =
          device.haveName() ? String(device.getName().c_str()) : "";
      break;
    }
  }
  scan->stop();
  scan->clearResults();

  if (foundAddr.toString() == "00:00:00:00:00:00") {
    if (error != nullptr) {
      *error = "no camera with smart service found";
    }
    return false;
  }

  cameraAddress = foundAddr;

  // Diagnostic: report what we found.
  Serial.printf("# smart found %s name=%s\n",
                cameraAddress.toString().c_str(),
                foundName.length() ? foundName.c_str() : "-");
  Serial.flush();

  // 2. Connect.
  if (!pclient->connect(cameraAddress)) {
    if (error != nullptr) {
      *error = "connect failed";
    }
    return false;
  }

  // 3. Identify and pair.
  BLERemoteService *pSvc = pclient->getService(SERVICE_SMART);
  if (pSvc == nullptr) {
    if (error != nullptr) {
      *error = "smart service not found";
    }
    pclient->disconnect();
    return false;
  }

  if (!identify(pSvc, error)) {
    pclient->disconnect();
    return false;
  }

  // 4. Wait for the user to confirm on the camera body (up to 60 s).
  pairResult = 0;
  if (!waitForPairResult(60000)) {
    if (error != nullptr) {
      if (pairResult == 0x03) {
        *error = "pairing rejected by user";
      } else {
        *error = "pairing confirmation timeout";
      }
    }
    pclient->disconnect();
    return false;
  }

  // 5. Finalise pairing: write [0x01] to identity characteristic.
  {
    uint8_t finalise = 0x01;
    pSvc->getCharacteristic(CHAR_IDENTITY)->writeValue(&finalise, 1, true);
  }

  // 6. Switch to shooting mode.
  BLERemoteService *pModeSvc = pclient->getService(SERVICE_MODE_UUID);
  if (pModeSvc == nullptr) {
    if (error != nullptr) {
      *error = "mode service not found";
    }
    pclient->disconnect();
    return false;
  }

  {
    uint8_t modeShoot = 0x02;
    pMode = pModeSvc->getCharacteristic(CHAR_MODE_UUID);
    if (pMode == nullptr) {
      if (error != nullptr) {
        *error = "mode characteristic not found";
      }
      pclient->disconnect();
      return false;
    }
    pMode->writeValue(&modeShoot, 1, true);
  }

  // 7. Get shutter characteristic.
  pShutter = pModeSvc->getCharacteristic(CHAR_SHUTTER_UUID);
  if (pShutter == nullptr) {
    if (error != nullptr) {
      *error = "shutter characteristic not found";
    }
    pclient->disconnect();
    return false;
  }

  // 8. Persist.
  saveAddress(cameraAddress);

  return true;
}

bool SmartCamera::connect(String *error) {
  // 1. Load the saved address.
  if (!loadAddress(cameraAddress)) {
    if (error != nullptr) {
      *error = "no camera address saved";
    }
    return false;
  }

  // 2. Connect.
  if (!pclient->connect(cameraAddress)) {
    if (error != nullptr) {
      *error = "connect failed";
    }
    return false;
  }

  // 3. Identify (always, even if bonded — the camera expects it).
  BLERemoteService *pSvc = pclient->getService(SERVICE_SMART);
  if (pSvc == nullptr) {
    if (error != nullptr) {
      *error = "smart service not found";
    }
    pclient->disconnect();
    return false;
  }

  if (!identify(pSvc, error)) {
    pclient->disconnect();
    return false;
  }

  // 4. Wait briefly for pair indication. If already bonded, no indication
  //    comes — treat 8 s of silence as "accepted".
  pairResult = 0;
  if (!waitForPairResult(8000)) {
    // timeout = already bonded, proceed.
  }

  if (pairResult == 0x03) {
    if (error != nullptr) {
      *error = "pairing rejected by camera";
    }
    pclient->disconnect();
    return false;
  }

  // 5. Finalise pairing.
  {
    uint8_t finalise = 0x01;
    pSvc->getCharacteristic(CHAR_IDENTITY)->writeValue(&finalise, 1, true);
  }

  // 6. Switch to shooting mode.
  BLERemoteService *pModeSvc = pclient->getService(SERVICE_MODE_UUID);
  if (pModeSvc == nullptr) {
    if (error != nullptr) {
      *error = "mode service not found";
    }
    pclient->disconnect();
    return false;
  }

  {
    uint8_t modeShoot = 0x02;
    pMode = pModeSvc->getCharacteristic(CHAR_MODE_UUID);
    if (pMode == nullptr) {
      if (error != nullptr) {
        *error = "mode characteristic not found";
      }
      pclient->disconnect();
      return false;
    }
    pMode->writeValue(&modeShoot, 1, true);
  }

  // 7. Get shutter characteristic.
  pShutter = pModeSvc->getCharacteristic(CHAR_SHUTTER_UUID);
  if (pShutter == nullptr) {
    if (error != nullptr) {
      *error = "shutter characteristic not found";
    }
    pclient->disconnect();
    return false;
  }

  return true;
}

bool SmartCamera::shoot(String *error) {
  if (!connectedFlag) {
    if (error != nullptr) {
      *error = "not connected";
    }
    return false;
  }

  if (pShutter == nullptr) {
    if (error != nullptr) {
      *error = "shutter characteristic not available";
    }
    return false;
  }

  // Press: [0x00, 0x01]
  uint8_t press[2] = {0x00, 0x01};
  pShutter->writeValue(press, 2, true);

  delay(200);

  // Release: [0x00, 0x02]
  uint8_t release[2] = {0x00, 0x02};
  pShutter->writeValue(release, 2, true);

  return true;
}

bool SmartCamera::focus(String *error) {
  // This protocol has no half-press.  furble's focusPress() is deliberately
  // empty.  Report the truth rather than silently doing nothing.
  if (error != nullptr) {
    *error = "focus not supported in smart mode";
  }
  return false;
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

bool SmartCamera::identify(BLERemoteService *service, String *error) {
  // 1. Subscribe to the pair indication characteristic.
  pPairIndication = service->getCharacteristic(CHAR_NAME_WRITE);
  if (pPairIndication == nullptr) {
    if (error != nullptr) {
      *error = "pair indication characteristic not found";
    }
    return false;
  }

  if (pPairIndication->canIndicate()) {
    // Enable indications (second param = false means indications, not
    // notifications).
    pPairIndication->registerForNotify(pairIndicationCallback, false, true);
  }

  // 2. Write [0x01, name] to the name characteristic.
  {
    String name = deviceName;
    size_t len = name.length();
    uint8_t *buf = new uint8_t[1 + len];
    buf[0] = 0x01;
    memcpy(buf + 1, name.c_str(), len);
    service->getCharacteristic(CHAR_NAME_WRITE)->writeValue(buf, 1 + len, true);
    delete[] buf;
  }

  // 3. Write [0x03, uuid_16bytes] to the identity characteristic.
  {
    const uuid128_t &uuid = getIdentityUuid();
    uint8_t buf[1 + UUID128_LEN];
    buf[0] = 0x03;
    memcpy(buf + 1, uuid.uint8, UUID128_LEN);
    service->getCharacteristic(CHAR_IDENTITY)->writeValue(buf, sizeof(buf), true);
  }

  // 4. Write [0x04, name] to the identity characteristic.
  {
    String name = deviceName;
    size_t len = name.length();
    uint8_t *buf = new uint8_t[1 + len];
    buf[0] = 0x04;
    memcpy(buf + 1, name.c_str(), len);
    service->getCharacteristic(CHAR_IDENTITY)->writeValue(buf, 1 + len, true);
    delete[] buf;
  }

  // 5. Write [0x05, 0x02] to the identity characteristic.
  {
    uint8_t buf[2] = {0x05, 0x02};
    service->getCharacteristic(CHAR_IDENTITY)->writeValue(buf, sizeof(buf), true);
  }

  return true;
}

bool SmartCamera::waitForPairResult(unsigned long timeoutMs) {
  unsigned long deadline = millis() + timeoutMs;
  while (millis() < deadline) {
    if (pairResult != 0) {
      return pairResult == 0x02;  // 0x02 = accept, 0x03 = reject
    }
    delay(100);
  }
  return false;  // timed out
}