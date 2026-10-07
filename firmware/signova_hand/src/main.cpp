// SIGNOVA hand firmware for the ESP32 (Arduino framework).
//
// Receives normalised poses from the laptop over USB serial (115200 baud, one JSON object per
// line, docs/PROTOCOL.md), plans smooth motion at 100 Hz (minimum-jerk, per-joint speed limit),
// applies calibration and safety limits, and drives the servos through a PCA9685 or a Feetech
// SCS bus. If the laptop goes quiet for 5 s, the hand glides to rest and switches outputs off.
//
// LED (GPIO 2):  slow blink = waiting for the laptop   solid = laptop connected
//                fast flicker = moving                 double blink = watchdog relaxed the hand
//                rapid bursts = servo driver not found (check wiring / power)
#ifdef ARDUINO

#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

#include "config.h"
#include "drivers.h"
#include "motion.h"
#include "protocol.h"

using namespace signova;

namespace {

const uint16_t CAL_MAGIC = 0x5349;  // "SI"
const uint8_t CAL_VERSION = 1;

struct StoredCal {
  uint16_t magic;
  uint8_t version;
  uint8_t inv;
  int16_t minV;
  int16_t maxV;
  float rest;
};

const ProtocolConfig kCfg = {JOINT_NAMES, NUM_JOINTS, SERVO_LIMIT_MIN, SERVO_LIMIT_MAX};

Motion motion;
ServoDriver* driver = nullptr;
CalEntry cal[NUM_JOINTS];
uint8_t channels[NUM_JOINTS];
int rawOverride[NUM_JOINTS];  // -1 = follow motion; otherwise a raw value from calibration
Preferences prefs;
LineReader reader;
char out[1024];
char pendingId[ID_MAX + 1];
bool havePending = false;
bool relaxing = false;
bool outputsOn = false;
bool watchdogFired = false;
bool hostSeen = false;
bool driverProblem = false;
uint32_t lastRx = 0;
uint32_t lastTick = 0;

void sendLine(size_t n) {
  if (n == 0) return;
  Serial.write(reinterpret_cast<const uint8_t*>(out), n);
  Serial.write('\n');
}

void loadCalibration() {
  prefs.begin("signova", true);
  for (int i = 0; i < NUM_JOINTS; ++i) {
    channels[i] = JOINT_CHANNELS[i];
    cal[i].ch = JOINT_CHANNELS[i];
    cal[i].minV = DEFAULT_CAL_MIN;
    cal[i].maxV = DEFAULT_CAL_MAX;
    cal[i].inv = false;
    cal[i].rest = DEFAULT_REST[i];
    char key[8];
    snprintf(key, sizeof(key), "cal%d", i);
    StoredCal s;
    if (prefs.getBytes(key, &s, sizeof(s)) == sizeof(s) && s.magic == CAL_MAGIC && s.version == CAL_VERSION &&
        s.minV >= SERVO_LIMIT_MIN && s.maxV <= SERVO_LIMIT_MAX && s.minV < s.maxV && s.rest >= 0.0f &&
        s.rest <= 1.0f) {
      cal[i].minV = s.minV;
      cal[i].maxV = s.maxV;
      cal[i].inv = s.inv != 0;
      cal[i].rest = s.rest;
    }
  }
  prefs.end();
}

void saveCalibration(int i) {
  StoredCal s;
  s.magic = CAL_MAGIC;
  s.version = CAL_VERSION;
  s.inv = cal[i].inv ? 1 : 0;
  s.minV = (int16_t)cal[i].minV;
  s.maxV = (int16_t)cal[i].maxV;
  s.rest = cal[i].rest;
  char key[8];
  snprintf(key, sizeof(key), "cal%d", i);
  prefs.begin("signova", false);
  prefs.putBytes(key, &s, sizeof(s));
  prefs.end();
}

void setOutputs(bool on) {
  if (on == outputsOn) return;
  driver->enable(on, channels, NUM_JOINTS);
  outputsOn = on;
}

void writeOutputs() {
  if (!outputsOn) return;
  int values[NUM_JOINTS];
  for (int i = 0; i < NUM_JOINTS; ++i) {
    values[i] = rawOverride[i] >= 0 ? rawOverride[i] : mapToServo(motion.position(i), cal[i]);
  }
  driver->writeAll(channels, values, NUM_JOINTS);
}

void startRelax(uint32_t now) {
  havePending = false;
  for (int i = 0; i < NUM_JOINTS; ++i) rawOverride[i] = -1;
  float rest[NUM_JOINTS];
  for (int i = 0; i < NUM_JOINTS; ++i) rest[i] = cal[i].rest;
  relaxing = true;
  motion.setTarget(rest, RELAX_MS, now);
}

void handleLine(const char* line, size_t len, uint32_t now) {
  lastRx = now;
  watchdogFired = false;
  hostSeen = true;
  Command c;
  if (!parseCommand(line, len, kCfg, c, DEFAULT_MOVE_MS)) {
    sendLine(writeError(out, sizeof(out), c));
    return;
  }
  switch (c.type) {
    case CMD_HELLO:
      sendLine(writeHello(out, sizeof(out), SIGNOVA_FW_VERSION, DRIVER_NAME, kCfg, driver->warning()));
      break;
    case CMD_PING:
      sendLine(writePong(out, sizeof(out), now));
      break;
    case CMD_POSE:
      for (int i = 0; i < NUM_JOINTS; ++i) rawOverride[i] = -1;
      relaxing = false;
      setOutputs(true);
      motion.setTarget(c.j, c.ms, now);  // targets are clamped to 0..1, then mapped inside calibration
      strncpy(pendingId, c.id, ID_MAX);
      pendingId[ID_MAX] = '\0';
      havePending = true;  // an older pending pose is superseded and never reported
      break;
    case CMD_STOP:
      motion.hold();
      havePending = false;
      relaxing = false;
      sendLine(writeOk(out, sizeof(out), "stop"));
      break;
    case CMD_RELAX:
      startRelax(now);
      sendLine(writeOk(out, sizeof(out), "relax"));
      break;
    case CMD_CAL_GET:
      sendLine(writeCal(out, sizeof(out), kCfg, cal));
      break;
    case CMD_CAL_SET:
      if (!applyCalSet(c, cal[c.joint])) {
        sendLine(writeError(out, sizeof(out), c));
        break;
      }
      saveCalibration(c.joint);
      sendLine(writeOk(out, sizeof(out), "cal_set"));
      break;
    case CMD_RAW:
      rawOverride[c.joint] = c.rawValue;
      relaxing = false;
      setOutputs(true);
      writeOutputs();
      sendLine(writeOk(out, sizeof(out), "raw"));
      break;
    default:
      break;
  }
}

void updateLed(uint32_t now) {
  bool on;
  uint32_t phase = now % 1000;
  if (driverProblem) {
    on = (now / 100) % 2 == 0 && (now / 1000) % 2 == 0;
  } else if (motion.active()) {
    on = (now / 50) % 2 == 0;
  } else if (watchdogFired) {
    on = phase < 100 || (phase >= 200 && phase < 300);
  } else if (hostSeen && now - lastRx < 2000) {
    on = true;
  } else {
    on = (now / 500) % 2 == 0;
  }
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

}  // namespace

void setup() {
  Serial.setRxBufferSize(1024);
  Serial.begin(SERIAL_BAUD);
  pinMode(LED_PIN, OUTPUT);
  for (int i = 0; i < NUM_JOINTS; ++i) rawOverride[i] = -1;
  loadCalibration();
  driver = createDriver();
  driverProblem = !driver->begin();
  driver->enable(false, channels, NUM_JOINTS);  // no torque until the laptop sends a pose
  outputsOn = false;
  float rest[NUM_JOINTS];
  for (int i = 0; i < NUM_JOINTS; ++i) rest[i] = cal[i].rest;
  motion.begin(NUM_JOINTS, FULL_RANGE_MS, rest);
  sendLine(writeEvent(out, sizeof(out), "boot", driver->warning(), SIGNOVA_FW_VERSION));
  lastTick = millis();
  lastRx = lastTick;
}

void loop() {
  uint32_t now = millis();
  while (Serial.available() > 0) {
    LineReader::Result r = reader.feed((char)Serial.read());
    if (r == LineReader::LINE_READY) {
      handleLine(reader.line(), reader.length(), now);
    } else if (r == LineReader::LINE_OVERFLOW) {
      lastRx = now;
      Command c;
      clearCommand(c);
      c.type = CMD_ERROR;
      strcpy(c.err, "bad_json");
      strcpy(c.detail, "line longer than 512 bytes");
      sendLine(writeError(out, sizeof(out), c));
    }
  }

  if (now - lastTick >= MOTION_PERIOD_MS) {
    lastTick = now;
    if (motion.update(now)) {
      if (relaxing) {
        relaxing = false;
        setOutputs(false);  // at rest: stop holding (no heat, no buzzing)
      } else if (havePending) {
        havePending = false;
        sendLine(writeDone(out, sizeof(out), pendingId, now));
      }
    }
    writeOutputs();
    if (!watchdogFired && (outputsOn || motion.active()) && now - lastRx > WATCHDOG_MS) {
      watchdogFired = true;
      startRelax(now);
      sendLine(writeEvent(out, sizeof(out), "watchdog", "no message for 5000 ms, relaxing", ""));
    }
    updateLed(now);
  }
}

#endif  // ARDUINO
