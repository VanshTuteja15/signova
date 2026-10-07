#include "protocol.h"

#include <ArduinoJson.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace signova {

static void copyStr(char* dst, size_t cap, const char* src) {
  if (cap == 0) return;
  size_t i = 0;
  if (src) {
    for (; src[i] && i < cap - 1; ++i) dst[i] = src[i];
  }
  dst[i] = '\0';
}

void clearCommand(Command& c) {
  memset(&c, 0, sizeof(c));
  c.type = CMD_NONE;
  c.joint = -1;
}

static bool fail(Command& c, const char* code, const char* detail, const char* cmd) {
  c.type = CMD_ERROR;
  copyStr(c.err, sizeof(c.err), code);
  copyStr(c.detail, sizeof(c.detail), detail);
  copyStr(c.errCmd, sizeof(c.errCmd), cmd ? cmd : "");
  return false;
}

static bool isNumber(JsonVariantConst v) {
  if (!v.is<float>()) return false;
  float f = v.as<float>();
  return !isnan(f) && !isinf(f);
}

static int findJoint(const ProtocolConfig& cfg, const char* name) {
  if (!name) return -1;
  for (int i = 0; i < cfg.jointCount; ++i) {
    if (strcmp(cfg.jointNames[i], name) == 0) return i;
  }
  return -1;
}

bool parseCommand(const char* line, size_t len, const ProtocolConfig& cfg, Command& out, uint32_t defaultMoveMs) {
  clearCommand(out);
  if (len > LINE_MAX_BYTES) return fail(out, "bad_json", "line longer than 512 bytes", 0);

  JsonDocument doc;
  DeserializationError e = deserializeJson(doc, line, len);
  if (e) return fail(out, "bad_json", "could not parse JSON", 0);
  JsonObjectConst obj = doc.as<JsonObjectConst>();
  if (obj.isNull() || !obj["cmd"].is<const char*>()) return fail(out, "bad_json", "expected an object with a \"cmd\" string", 0);
  const char* cmd = obj["cmd"].as<const char*>();

  if (strcmp(cmd, "hello") == 0) { out.type = CMD_HELLO; return true; }
  if (strcmp(cmd, "stop") == 0) { out.type = CMD_STOP; return true; }
  if (strcmp(cmd, "relax") == 0) { out.type = CMD_RELAX; return true; }
  if (strcmp(cmd, "ping") == 0) { out.type = CMD_PING; return true; }
  if (strcmp(cmd, "cal_get") == 0) { out.type = CMD_CAL_GET; return true; }

  if (strcmp(cmd, "pose") == 0) {
    const char* id = obj["id"].is<const char*>() ? obj["id"].as<const char*>() : "";
    copyStr(out.id, sizeof(out.id), id);
    out.errHasId = true;
    JsonArrayConst j = obj["j"].as<JsonArrayConst>();
    if (j.isNull() || (int)j.size() != cfg.jointCount) {
      char d[80];
      snprintf(d, sizeof(d), "j must be an array of %d numbers", cfg.jointCount);
      return fail(out, "bad_length", d, "pose");
    }
    int i = 0;
    for (JsonVariantConst v : j) {
      if (!isNumber(v)) {
        char d[80];
        snprintf(d, sizeof(d), "j must be an array of %d numbers", cfg.jointCount);
        return fail(out, "bad_length", d, "pose");
      }
      out.j[i++] = v.as<float>();
    }
    out.jCount = i;
    out.ms = defaultMoveMs;
    if (!obj["ms"].isNull()) {
      if (!isNumber(obj["ms"])) return fail(out, "bad_json", "ms must be a number", "pose");
      float ms = obj["ms"].as<float>();
      out.ms = (uint32_t)(ms < 0 ? 0 : (ms > 10000 ? 10000 : ms));
    }
    out.type = CMD_POSE;
    return true;
  }

  if (strcmp(cmd, "cal_set") == 0 || strcmp(cmd, "raw") == 0) {
    bool isCal = cmd[0] == 'c';
    const char* name = obj["joint"].is<const char*>() ? obj["joint"].as<const char*>() : 0;
    out.joint = findJoint(cfg, name);
    if (out.joint < 0) return fail(out, "bad_joint", "unknown joint", cmd);
    if (!isCal) {
      if (!isNumber(obj["us"])) return fail(out, "bad_json", "us must be a number", "raw");
      float v = obj["us"].as<float>();
      out.rawValue = (int)(v < cfg.servoMin ? cfg.servoMin : (v > cfg.servoMax ? cfg.servoMax : v));
      out.type = CMD_RAW;
      return true;
    }
    char d[80];
    snprintf(d, sizeof(d), "min and max must be numbers %d..%d", cfg.servoMin, cfg.servoMax);
    if (!obj["min"].isNull()) {
      if (!isNumber(obj["min"]) || obj["min"].as<float>() < cfg.servoMin || obj["min"].as<float>() > cfg.servoMax)
        return fail(out, "bad_json", d, "cal_set");
      out.hasMin = true;
      out.minV = (int)obj["min"].as<float>();
    }
    if (!obj["max"].isNull()) {
      if (!isNumber(obj["max"]) || obj["max"].as<float>() < cfg.servoMin || obj["max"].as<float>() > cfg.servoMax)
        return fail(out, "bad_json", d, "cal_set");
      out.hasMax = true;
      out.maxV = (int)obj["max"].as<float>();
    }
    if (!obj["inv"].isNull()) {
      if (!obj["inv"].is<bool>()) return fail(out, "bad_json", "inv must be true or false", "cal_set");
      out.hasInv = true;
      out.inv = obj["inv"].as<bool>();
    }
    if (!obj["rest"].isNull()) {
      if (!isNumber(obj["rest"]) || obj["rest"].as<float>() < 0 || obj["rest"].as<float>() > 1)
        return fail(out, "bad_json", "rest must be a number 0..1", "cal_set");
      out.hasRest = true;
      out.rest = obj["rest"].as<float>();
    }
    out.type = CMD_CAL_SET;
    return true;
  }

  char short_cmd[16];
  copyStr(short_cmd, sizeof(short_cmd), cmd);
  char d[80];
  snprintf(d, sizeof(d), "unknown command '%s'", short_cmd);
  return fail(out, "unknown_cmd", d, short_cmd);
}

bool applyCalSet(Command& cmd, CalEntry& entry) {
  CalEntry merged = entry;
  if (cmd.hasMin) merged.minV = cmd.minV;
  if (cmd.hasMax) merged.maxV = cmd.maxV;
  if (cmd.hasInv) merged.inv = cmd.inv;
  if (cmd.hasRest) merged.rest = cmd.rest;
  if (merged.minV >= merged.maxV) return fail(cmd, "bad_json", "min must be below max", "cal_set");
  entry = merged;
  return true;
}

int mapToServo(float v, const CalEntry& c) {
  if (v < 0) v = 0;
  if (v > 1) v = 1;
  if (c.inv) v = 1.0f - v;
  float out = (float)c.minV + (float)(c.maxV - c.minV) * v;
  return (int)(out + 0.5f);
}

static size_t finish(JsonDocument& doc, char* buf, size_t cap) {
  size_t need = measureJson(doc);
  if (need + 1 > cap) return 0;
  return serializeJson(doc, buf, cap);
}

size_t writeHello(char* buf, size_t cap, const char* fw, const char* driver, const ProtocolConfig& cfg, const char* warn) {
  JsonDocument doc;
  doc["ok"] = "hello";
  doc["fw"] = fw;
  doc["driver"] = driver;
  JsonArray arr = doc["joints"].to<JsonArray>();
  for (int i = 0; i < cfg.jointCount; ++i) arr.add(cfg.jointNames[i]);
  if (warn && warn[0]) doc["warn"] = warn;
  return finish(doc, buf, cap);
}

size_t writeOk(char* buf, size_t cap, const char* what) {
  JsonDocument doc;
  doc["ok"] = what;
  return finish(doc, buf, cap);
}

size_t writeDone(char* buf, size_t cap, const char* id, uint32_t t) {
  JsonDocument doc;
  doc["done"] = id;
  doc["t"] = t;
  return finish(doc, buf, cap);
}

size_t writePong(char* buf, size_t cap, uint32_t t) {
  JsonDocument doc;
  doc["pong"] = t;
  return finish(doc, buf, cap);
}

size_t writeCal(char* buf, size_t cap, const ProtocolConfig& cfg, const CalEntry* cal) {
  JsonDocument doc;
  JsonArray arr = doc["cal"].to<JsonArray>();
  for (int i = 0; i < cfg.jointCount; ++i) {
    JsonObject o = arr.add<JsonObject>();
    o["joint"] = cfg.jointNames[i];
    o["ch"] = cal[i].ch;
    o["min"] = cal[i].minV;
    o["max"] = cal[i].maxV;
    o["inv"] = cal[i].inv;
    o["rest"] = roundf(cal[i].rest * 1000.0f) / 1000.0f;
  }
  return finish(doc, buf, cap);
}

size_t writeError(char* buf, size_t cap, const Command& c) {
  JsonDocument doc;
  doc["err"] = c.err;
  doc["detail"] = c.detail;
  if (c.errCmd[0]) doc["cmd"] = c.errCmd;
  if (c.errHasId) doc["id"] = c.id;
  return finish(doc, buf, cap);
}

size_t writeEvent(char* buf, size_t cap, const char* event, const char* detail, const char* fw) {
  JsonDocument doc;
  doc["event"] = event;
  if (detail && detail[0]) doc["detail"] = detail;
  if (fw && fw[0]) doc["fw"] = fw;
  return finish(doc, buf, cap);
}

LineReader::LineReader() : len_(0), overflow_(false), ready_(false) { buf_[0] = '\0'; }

LineReader::Result LineReader::feed(char c) {
  if (ready_) {  // previous line was consumed: start a new one
    len_ = 0;
    ready_ = false;
    buf_[0] = '\0';
  }
  if (c == '\r') return NONE;
  if (c == '\n') {
    if (overflow_) {
      overflow_ = false;
      len_ = 0;
      buf_[0] = '\0';
      return LINE_OVERFLOW;
    }
    if (len_ == 0) return NONE;  // ignore blank lines
    buf_[len_] = '\0';
    ready_ = true;
    return LINE_READY;
  }
  if (overflow_) return NONE;
  if (len_ >= LINE_MAX_BYTES) {
    overflow_ = true;
    return NONE;
  }
  buf_[len_++] = c;
  return NONE;
}

}  // namespace signova
