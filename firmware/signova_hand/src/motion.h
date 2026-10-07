// Motion planner: per-joint start / target / duration with minimum-jerk easing.
// Pure C++ (no Arduino headers) so it runs in the native unit tests.
#pragma once

#include <stdint.h>

namespace signova {

const int MAX_JOINTS = 16;

// s(u) = 10u^3 - 15u^4 + 6u^5: zero velocity and acceleration at both ends.
float minJerk(float u);

class Motion {
 public:
  Motion();

  // n joints, speed limit (ms for a full 0 -> 1 move), starting positions (normalised).
  void begin(int n, uint32_t fullRangeMs, const float* initial);

  // Move every joint to `targets` (clamped 0..1). Each joint takes
  // max(ms, |delta| * fullRangeMs) so it never exceeds the speed limit, even if ms is small.
  void setTarget(const float* targets, uint32_t ms, uint32_t nowMs);

  // Freeze where we are (stop command).
  void hold();

  // Advance to nowMs. Returns true exactly once, when every joint has arrived.
  bool update(uint32_t nowMs);

  float position(int i) const { return pos_[i]; }
  float target(int i) const { return target_[i]; }
  uint32_t duration(int i) const { return dur_[i]; }
  bool active() const { return active_; }
  int count() const { return n_; }

 private:
  int n_;
  uint32_t fullRangeMs_;
  uint32_t t0_;
  bool active_;
  float pos_[MAX_JOINTS];
  float start_[MAX_JOINTS];
  float target_[MAX_JOINTS];
  uint32_t dur_[MAX_JOINTS];
};

}  // namespace signova
