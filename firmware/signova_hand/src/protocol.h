// SIGNOVA serial protocol: newline-terminated JSON, one object per line (docs/PROTOCOL.md).
// Parsing / validation / reply formatting only - no I/O - so it runs in native unit tests.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "motion.h"

namespace signova {

const size_t LINE_MAX_BYTES = 512;
const int ID_MAX = 16;

enum CmdType { CMD_NONE = 0, CMD_HELLO, CMD_POSE, CMD_STOP, CMD_RELAX, CMD_PING, CMD_CAL_GET, CMD_CAL_SET, CMD_RAW, CMD_ERROR };

struct ProtocolConfig {
  const char* const* jointNames;
  int jointCount;
  int servoMin;  // hard limits for raw / calibration values (us or position counts)
  int servoMax;
};

struct CalEntry {
  int ch;
  int minV;
  int maxV;
  bool inv;
  float rest;
};

struct Command {
  CmdType type;
  char id[ID_MAX + 1];
  float j[MAX_JOINTS];
  int jCount;
  uint32_t ms;
  int joint;  // joint index for cal_set / raw, -1 if none
  bool hasMin, hasMax, hasInv, hasRest;
  int minV, maxV;
  bool inv;
  float rest;
  int rawValue;
  // error details when type == CMD_ERROR
  char err[16];
  char detail[80];
  char errCmd[16];
  bool errHasId;
};

void clearCommand(Command& c);

// Parse one line. Returns true for a valid command; on failure `out.type == CMD_ERROR`
// with err = bad_json | unknown_cmd | bad_joint | bad_length. Never crashes on bad input.
bool parseCommand(const char* line, size_t len, const ProtocolConfig& cfg, Command& out, uint32_t defaultMoveMs = 300);

// Apply a validated cal_set to an entry (checks min < max against the merged result).
// Returns false and fills err/detail in `cmd` if the merged calibration is invalid.
bool applyCalSet(Command& cmd, CalEntry& entry);

// Normalised 0..1 -> servo command (us or counts), with invert and clamping.
int mapToServo(float v, const CalEntry& c);

// Reply builders. Each writes a JSON object (no newline) and returns its length (0 if it didn't fit).
size_t writeHello(char* buf, size_t cap, const char* fw, const char* driver, const ProtocolConfig& cfg, const char* warn);
size_t writeOk(char* buf, size_t cap, const char* what);
size_t writeDone(char* buf, size_t cap, const char* id, uint32_t t);
size_t writePong(char* buf, size_t cap, uint32_t t);
size_t writeCal(char* buf, size_t cap, const ProtocolConfig& cfg, const CalEntry* cal);
size_t writeError(char* buf, size_t cap, const Command& c);
size_t writeEvent(char* buf, size_t cap, const char* event, const char* detail, const char* fw);

// Collects bytes into lines. feed() returns LINE_READY when a full line is available in line(),
// LINE_OVERFLOW when a line exceeded LINE_MAX_BYTES (the rest of it is discarded).
class LineReader {
 public:
  enum Result { NONE = 0, LINE_READY, LINE_OVERFLOW };
  LineReader();
  Result feed(char c);
  const char* line() const { return buf_; }
  size_t length() const { return len_; }

 private:
  char buf_[LINE_MAX_BYTES + 1];
  size_t len_;
  bool overflow_;
  bool ready_;
};

}  // namespace signova
