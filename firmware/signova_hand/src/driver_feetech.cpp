// Feetech SCS serial-bus servo driver (e.g. SCS0009). UNTESTED ON HARDWARE - see docs/HANDOFF.md.
// Compiled only with DRIVER_FEETECH on the ESP32. Uses UART2 (half-duplex bus via a buffer board).
#if defined(ARDUINO) && defined(DRIVER_FEETECH)

#include <Arduino.h>

#include "config.h"
#include "drivers.h"

namespace signova {

class FeetechDriver : public ServoDriver {
 public:
  FeetechDriver() : enabled_(false) {}

  bool begin() override {
    Serial2.begin(FEETECH_BAUD, SERIAL_8N1, FEETECH_RX_PIN, FEETECH_TX_PIN);
    return true;  // the bus has no presence check without reading replies
  }

  void writeAll(const uint8_t* ids, const int* values, int n) override {
    if (!enabled_ || n <= 0 || n > 16) return;
    uint16_t pos[16];
    for (int i = 0; i < n; ++i) pos[i] = (uint16_t)(values[i] < 0 ? 0 : (values[i] > 1023 ? 1023 : values[i]));
    uint8_t pkt[8 + 7 * 16];
    size_t len = buildScsSyncGoal(ids, pos, n, pkt);
    Serial2.write(pkt, len);
  }

  void enable(bool on, const uint8_t* ids, int n) override {
    enabled_ = on;
    uint8_t v = on ? 1 : 0;
    uint8_t pkt[16];
    for (int i = 0; i < n; ++i) {
      size_t len = buildScsWrite(ids[i], SCS_ADDR_TORQUE_ENABLE, &v, 1, pkt);
      Serial2.write(pkt, len);
      delayMicroseconds(300);  // let each servo answer before the next command
    }
  }

  const char* name() const override { return "feetech"; }

 private:
  bool enabled_;
};

ServoDriver* createDriver() {
  static FeetechDriver driver;
  return &driver;
}

}  // namespace signova

#endif
