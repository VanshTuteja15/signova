// SIGNOVA hand firmware configuration.
//
// Edit this file for your hand. The joint list and order MUST match config/hand.yaml on the
// laptop: the laptop checks it on connect ({"cmd":"hello"}) and refuses to move a hand whose
// joints differ. Calibration defaults below are only used until you save calibration from the
// dashboard (it is then stored in NVS flash and survives reboots).
#pragma once

#include <stdint.h>

#define SIGNOVA_FW_VERSION "0.1.0"

// ----------------------------------------------------------------------------- driver
// Pick one (platformio.ini sets it per environment):
//   DRIVER_PCA9685  Adafruit-style 16-channel PWM board over I2C (default, hobby servos)
//   DRIVER_FEETECH  Feetech SCS serial-bus servos (e.g. SCS0009) - UNTESTED ON HARDWARE
#if !defined(DRIVER_PCA9685) && !defined(DRIVER_FEETECH)
#define DRIVER_PCA9685 1
#endif

// ----------------------------------------------------------------------------- joints
#define NUM_JOINTS 7
static const char* const JOINT_NAMES[NUM_JOINTS] = {
    "thumb", "thumb_rot", "index", "middle", "ring", "pinky", "wrist",
};
// PCA9685 channel (DRIVER_PCA9685) or Feetech servo ID (DRIVER_FEETECH) for each joint.
#if defined(DRIVER_FEETECH)
static const uint8_t JOINT_CHANNELS[NUM_JOINTS] = {1, 2, 3, 4, 5, 6, 7};
#else
static const uint8_t JOINT_CHANNELS[NUM_JOINTS] = {0, 1, 2, 3, 4, 5, 6};
#endif
// Rest pose (normalised 0..1); also where the hand goes on relax / watchdog.
static const float DEFAULT_REST[NUM_JOINTS] = {0.15f, 0.20f, 0.15f, 0.15f, 0.18f, 0.20f, 0.50f};

// ----------------------------------------------------------------------------- calibration defaults
#if defined(DRIVER_FEETECH)
#define SERVO_LIMIT_MIN 0      // SCS position counts
#define SERVO_LIMIT_MAX 1023
#define DEFAULT_CAL_MIN 200
#define DEFAULT_CAL_MAX 800
#define DRIVER_NAME "feetech"
#else
#define SERVO_LIMIT_MIN 500    // microseconds; never command outside this
#define SERVO_LIMIT_MAX 2500
#define DEFAULT_CAL_MIN 600
#define DEFAULT_CAL_MAX 2300
#define DRIVER_NAME "pca9685"
#endif

// ----------------------------------------------------------------------------- motion + safety
#define FULL_RANGE_MS 250      // speed limit: no joint goes 0 -> 1 faster than this
#define MOTION_PERIOD_MS 10    // 100 Hz motion update
#define WATCHDOG_MS 5000       // no message for this long -> move to rest, then outputs off
#define RELAX_MS 600           // time to glide to rest before switching outputs off
#define DEFAULT_MOVE_MS 300

// ----------------------------------------------------------------------------- pins
#define SERIAL_BAUD 115200
#define LED_PIN 2              // on-board LED on most ESP32 dev boards
#define I2C_SDA 21
#define I2C_SCL 22
#define PCA9685_ADDR 0x40
#define PCA9685_OE_PIN -1      // set to a GPIO wired to the PCA9685 OE pin to cut all outputs at once
#define PCA9685_OSC_HZ 27000000
#define SERVO_PWM_HZ 50
#define FEETECH_RX_PIN 16
#define FEETECH_TX_PIN 17
#define FEETECH_BAUD 1000000
