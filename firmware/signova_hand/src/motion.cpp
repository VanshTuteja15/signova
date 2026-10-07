#include "motion.h"

namespace signova {

static float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

float minJerk(float u) {
  u = clamp01(u);
  return u * u * u * (10.0f - 15.0f * u + 6.0f * u * u);
}

Motion::Motion() : n_(0), fullRangeMs_(250), t0_(0), active_(false) {
  for (int i = 0; i < MAX_JOINTS; ++i) {
    pos_[i] = start_[i] = target_[i] = 0.0f;
    dur_[i] = 0;
  }
}

void Motion::begin(int n, uint32_t fullRangeMs, const float* initial) {
  n_ = n < 0 ? 0 : (n > MAX_JOINTS ? MAX_JOINTS : n);
  fullRangeMs_ = fullRangeMs;
  for (int i = 0; i < n_; ++i) {
    pos_[i] = start_[i] = target_[i] = clamp01(initial ? initial[i] : 0.0f);
    dur_[i] = 0;
  }
  active_ = false;
}

void Motion::setTarget(const float* targets, uint32_t ms, uint32_t nowMs) {
  for (int i = 0; i < n_; ++i) {
    float t = clamp01(targets[i]);
    float delta = t > pos_[i] ? t - pos_[i] : pos_[i] - t;
    uint32_t limited = (uint32_t)(delta * (float)fullRangeMs_ + 0.5f);
    start_[i] = pos_[i];
    target_[i] = t;
    dur_[i] = ms > limited ? ms : limited;
  }
  t0_ = nowMs;
  active_ = true;
}

void Motion::hold() {
  for (int i = 0; i < n_; ++i) {
    start_[i] = target_[i] = pos_[i];
    dur_[i] = 0;
  }
  active_ = false;
}

bool Motion::update(uint32_t nowMs) {
  if (!active_) return false;
  uint32_t elapsed = nowMs - t0_;  // unsigned: survives millis() wrap-around
  bool arrived = true;
  for (int i = 0; i < n_; ++i) {
    if (dur_[i] == 0 || elapsed >= dur_[i]) {
      pos_[i] = target_[i];
    } else {
      float u = (float)elapsed / (float)dur_[i];
      pos_[i] = start_[i] + (target_[i] - start_[i]) * minJerk(u);
      arrived = false;
    }
  }
  if (arrived) active_ = false;
  return arrived;
}

}  // namespace signova
