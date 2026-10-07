// Servo output drivers. Exactly one driver_*.cpp is compiled in (DRIVER_PCA9685 or DRIVER_FEETECH).
// The SCS packet builders are pure C++ (no Arduino) so the native tests can check them.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace signova {

class ServoDriver {
 public:
  virtual ~ServoDriver() {}
  // Initialise the hardware. Returns false if it did not answer (see warning()).
  virtual bool begin() = 0;
  // Command every joint at once: channel (PCA9685) or servo ID (Feetech), value in us or counts.
  virtual void writeAll(const uint8_t* channels, const int* values, int n) = 0;
  // false = no pulses / torque off (relax): no holding torque, no heat.
  virtual void enable(bool on, const uint8_t* channels, int n) = 0;
  virtual const char* name() const = 0;
  virtual const char* warning() const { return ""; }
};

// Defined by the driver_*.cpp selected in config.h / platformio.ini.
ServoDriver* createDriver();

// ---------------------------------------------------------------------------- Feetech SCS protocol
// Packet: 0xFF 0xFF ID LEN INSTR PARAMS... CHECKSUM, LEN = number of params + 2,
// CHECKSUM = ~(ID + LEN + INSTR + sum(PARAMS)) & 0xFF. SCS-series registers are big-endian.
const uint8_t SCS_BROADCAST_ID = 0xFE;
const uint8_t SCS_INST_WRITE = 0x03;
const uint8_t SCS_INST_SYNC_WRITE = 0x83;
const uint8_t SCS_ADDR_TORQUE_ENABLE = 40;
const uint8_t SCS_ADDR_GOAL_POSITION = 42;  // then goal time (44) and goal speed (46), 2 bytes each

inline uint8_t scsChecksum(const uint8_t* pkt, size_t len) {
  // pkt points at the full packet; sum from ID (index 2) to the byte before the checksum
  unsigned sum = 0;
  for (size_t i = 2; i < len - 1; ++i) sum += pkt[i];
  return (uint8_t)(~sum & 0xFF);
}

// WRITE `n` bytes starting at register `addr` of servo `id`. Returns the packet length (n + 7).
inline size_t buildScsWrite(uint8_t id, uint8_t addr, const uint8_t* data, uint8_t n, uint8_t* out) {
  size_t k = 0;
  out[k++] = 0xFF;
  out[k++] = 0xFF;
  out[k++] = id;
  out[k++] = (uint8_t)(n + 3);  // addr + n data bytes + 2
  out[k++] = SCS_INST_WRITE;
  out[k++] = addr;
  for (uint8_t i = 0; i < n; ++i) out[k++] = data[i];
  k++;
  out[k - 1] = scsChecksum(out, k);
  return k;
}

inline void scsPut16(uint8_t* p, uint16_t v) {  // big-endian (SCS series)
  p[0] = (uint8_t)(v >> 8);
  p[1] = (uint8_t)(v & 0xFF);
}

// Goal position + time + speed for one servo.
inline size_t buildScsGoal(uint8_t id, uint16_t pos, uint16_t timeMs, uint16_t speed, uint8_t* out) {
  uint8_t d[6];
  scsPut16(d, pos);
  scsPut16(d + 2, timeMs);
  scsPut16(d + 4, speed);
  return buildScsWrite(id, SCS_ADDR_GOAL_POSITION, d, 6, out);
}

// SYNC WRITE goal positions to n servos in one broadcast packet (no replies, no bus collisions).
// Packet: FF FF FE LEN 83 ADDR L (ID D1..DL)*n CHK with L = 6 (pos, time, speed), LEN = (L + 1) * n + 4.
inline size_t buildScsSyncGoal(const uint8_t* ids, const uint16_t* pos, int n, uint8_t* out) {
  const uint8_t L = 6;
  size_t k = 0;
  out[k++] = 0xFF;
  out[k++] = 0xFF;
  out[k++] = SCS_BROADCAST_ID;
  out[k++] = (uint8_t)((L + 1) * n + 4);
  out[k++] = SCS_INST_SYNC_WRITE;
  out[k++] = SCS_ADDR_GOAL_POSITION;
  out[k++] = L;
  for (int i = 0; i < n; ++i) {
    out[k++] = ids[i];
    scsPut16(out + k, pos[i]);
    scsPut16(out + k + 2, 0);  // time 0: the ESP32 already plans smooth motion at 100 Hz
    scsPut16(out + k + 4, 0);  // speed 0: servo maximum
    k += 6;
  }
  k++;
  out[k - 1] = scsChecksum(out, k);
  return k;
}

}  // namespace signova
