// PCA9685 16-channel PWM driver (hobby servos). Compiled only with DRIVER_PCA9685 on the ESP32.
#if defined(ARDUINO) && defined(DRIVER_PCA9685)

#include <Adafruit_PWMServoDriver.h>
#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "drivers.h"

namespace signova {

class Pca9685Driver : public ServoDriver {
 public:
  Pca9685Driver() : pwm_(PCA9685_ADDR, Wire), ok_(false), enabled_(false) {
    for (int i = 0; i < 16; ++i) last_[i] = -1;
  }

  bool begin() override {
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.beginTransmission(PCA9685_ADDR);
    ok_ = Wire.endTransmission() == 0;
    pwm_.begin();
    pwm_.setOscillatorFrequency(PCA9685_OSC_HZ);
    pwm_.setPWMFreq(SERVO_PWM_HZ);
#if PCA9685_OE_PIN >= 0
    pinMode(PCA9685_OE_PIN, OUTPUT);
    digitalWrite(PCA9685_OE_PIN, HIGH);  // outputs off until the first pose
#endif
    return ok_;
  }

  void writeAll(const uint8_t* channels, const int* values, int n) override {
    if (!enabled_) return;
    for (int i = 0; i < n; ++i) {
      uint8_t ch = channels[i] & 0x0F;
      if (values[i] == last_[ch]) continue;  // only touch the I2C bus when a value changes
      pwm_.writeMicroseconds(ch, (uint16_t)values[i]);
      last_[ch] = values[i];
    }
  }

  void enable(bool on, const uint8_t* channels, int n) override {
    enabled_ = on;
#if PCA9685_OE_PIN >= 0
    digitalWrite(PCA9685_OE_PIN, on ? LOW : HIGH);
#endif
    if (!on) {
      for (int i = 0; i < n; ++i) {
        uint8_t ch = channels[i] & 0x0F;
        pwm_.setPWM(ch, 0, 4096);  // "fully off": no pulses, the servo stops holding
        last_[ch] = -1;
      }
    }
  }

  const char* name() const override { return "pca9685"; }
  const char* warning() const override {
    return ok_ ? "" : "PCA9685 not found on I2C: check SDA 21 / SCL 22 wiring and board power";
  }

 private:
  Adafruit_PWMServoDriver pwm_;
  bool ok_;
  bool enabled_;
  int last_[16];
};

ServoDriver* createDriver() {
  static Pca9685Driver driver;
  return &driver;
}

}  // namespace signova

#endif
