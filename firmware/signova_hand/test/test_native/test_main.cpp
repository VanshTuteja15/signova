// Native unit tests for the firmware's pure C++ parts: motion planner, protocol parsing /
// replies, line framing and the Feetech SCS packet builders.  Run with:  pio test -e native
#include <string.h>
#include <unity.h>

#include "drivers.h"
#include "motion.h"
#include "protocol.h"

using namespace signova;

static const char* const NAMES[7] = {"thumb", "thumb_rot", "index", "middle", "ring", "pinky", "wrist"};
static const ProtocolConfig CFG = {NAMES, 7, 500, 2500};

void setUp() {}
void tearDown() {}

static bool parse(const char* s, Command& c) { return parseCommand(s, strlen(s), CFG, c, 300); }

// ------------------------------------------------------------------ motion
void test_min_jerk_curve() {
  TEST_ASSERT_EQUAL_FLOAT(0.0f, minJerk(0.0f));
  TEST_ASSERT_EQUAL_FLOAT(1.0f, minJerk(1.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.5f, minJerk(0.5f));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, minJerk(-2.0f));
  TEST_ASSERT_EQUAL_FLOAT(1.0f, minJerk(3.0f));
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 1.0f - minJerk(0.75f), minJerk(0.25f));
  float prev = 0;
  for (int i = 1; i <= 20; ++i) {
    float v = minJerk(i / 20.0f);
    TEST_ASSERT_TRUE(v >= prev);
    prev = v;
  }
}

void test_motion_reaches_target_and_reports_once() {
  Motion m;
  float start[2] = {0.0f, 0.0f};
  float goal[2] = {0.5f, 0.2f};
  m.begin(2, 250, start);
  m.setTarget(goal, 300, 1000);
  TEST_ASSERT_TRUE(m.active());
  TEST_ASSERT_FALSE(m.update(1150));
  TEST_ASSERT_FLOAT_WITHIN(1e-4f, 0.25f, m.position(0));  // halfway in time = halfway in space
  TEST_ASSERT_TRUE(m.update(1300));
  TEST_ASSERT_EQUAL_FLOAT(0.5f, m.position(0));
  TEST_ASSERT_EQUAL_FLOAT(0.2f, m.position(1));
  TEST_ASSERT_FALSE(m.update(1400));
  TEST_ASSERT_FALSE(m.active());
}

void test_motion_speed_limit() {
  Motion m;
  float start[2] = {0.0f, 0.0f};
  float goal[2] = {1.0f, 0.1f};
  m.begin(2, 250, start);
  m.setTarget(goal, 50, 0);
  TEST_ASSERT_EQUAL_UINT32(250, m.duration(0));  // full range can't be faster than 250 ms
  TEST_ASSERT_EQUAL_UINT32(50, m.duration(1));
  TEST_ASSERT_FALSE(m.update(100));
  TEST_ASSERT_EQUAL_FLOAT(0.1f, m.position(1));
  TEST_ASSERT_TRUE(m.update(250));
}

void test_motion_clamps_and_holds() {
  Motion m;
  float start[1] = {0.5f};
  float goal[1] = {7.0f};
  m.begin(1, 250, start);
  m.setTarget(goal, 400, 0);
  m.update(200);
  float mid = m.position(0);
  TEST_ASSERT_TRUE(mid > 0.5f && mid < 1.0f);
  m.hold();
  TEST_ASSERT_FALSE(m.active());
  TEST_ASSERT_FALSE(m.update(1000));
  TEST_ASSERT_EQUAL_FLOAT(mid, m.position(0));
  float low[1] = {-3.0f};
  m.setTarget(low, 0, 1000);
  TEST_ASSERT_FALSE(m.update(1000 + 50));  // speed limit still applies with ms = 0
  m.update(2000);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, m.position(0));
}

void test_motion_new_target_starts_from_current_position() {
  Motion m;
  float start[1] = {0.0f};
  float a[1] = {1.0f};
  float b[1] = {0.0f};
  m.begin(1, 250, start);
  m.setTarget(a, 400, 0);
  m.update(200);
  float here = m.position(0);
  m.setTarget(b, 400, 200);
  m.update(200);
  TEST_ASSERT_EQUAL_FLOAT(here, m.position(0));  // no jump when superseded
}

void test_motion_millis_wraparound() {
  Motion m;
  float start[1] = {0.0f};
  float goal[1] = {1.0f};
  m.begin(1, 250, start);
  m.setTarget(goal, 300, 0xFFFFFF00u);
  TEST_ASSERT_FALSE(m.update(0xFFFFFF00u + 100));
  TEST_ASSERT_TRUE(m.update(0x00000100u));  // wrapped: 0x200 ms elapsed
}

// ------------------------------------------------------------------ protocol parsing
void test_parse_simple_commands() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"hello\"}", c));
  TEST_ASSERT_EQUAL(CMD_HELLO, c.type);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"stop\"}", c));
  TEST_ASSERT_EQUAL(CMD_STOP, c.type);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"relax\"}", c));
  TEST_ASSERT_EQUAL(CMD_RELAX, c.type);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"ping\"}", c));
  TEST_ASSERT_EQUAL(CMD_PING, c.type);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"cal_get\"}", c));
  TEST_ASSERT_EQUAL(CMD_CAL_GET, c.type);
}

void test_parse_pose() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"pose\",\"id\":\"ILY.3\",\"j\":[0,0,0,1,1,0,0.5],\"ms\":250}", c));
  TEST_ASSERT_EQUAL(CMD_POSE, c.type);
  TEST_ASSERT_EQUAL_STRING("ILY.3", c.id);
  TEST_ASSERT_EQUAL_INT(7, c.jCount);
  TEST_ASSERT_EQUAL_FLOAT(1.0f, c.j[3]);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, c.j[6]);
  TEST_ASSERT_EQUAL_UINT32(250, c.ms);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":[0,0,0,0,0,0,0]}", c));
  TEST_ASSERT_EQUAL_UINT32(300, c.ms);  // default
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":[0,0,0,0,0,0,0],\"ms\":99999}", c));
  TEST_ASSERT_EQUAL_UINT32(10000, c.ms);  // clamped
}

static void expect_error(const char* line, const char* code) {
  Command c;
  TEST_ASSERT_FALSE_MESSAGE(parse(line, c), line);
  TEST_ASSERT_EQUAL(CMD_ERROR, c.type);
  TEST_ASSERT_EQUAL_STRING_MESSAGE(code, c.err, line);
  TEST_ASSERT_TRUE(strlen(c.detail) > 0);
}

void test_parse_errors_never_crash() {
  expect_error("this is not json", "bad_json");
  expect_error("[1,2,3]", "bad_json");
  expect_error("{\"nocmd\":1}", "bad_json");
  expect_error("{\"cmd\":5}", "bad_json");
  expect_error("{\"cmd\":\"dance\"}", "unknown_cmd");
  expect_error("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":[0,0]}", "bad_length");
  expect_error("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":\"x\"}", "bad_length");
  expect_error("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":[0,0,0,0,0,0,\"x\"]}", "bad_length");
  expect_error("{\"cmd\":\"pose\",\"id\":\"A\",\"j\":[0,0,0,0,0,0,0],\"ms\":\"slow\"}", "bad_json");
  expect_error("{\"cmd\":\"cal_set\",\"joint\":\"elbow\"}", "bad_joint");
  expect_error("{\"cmd\":\"cal_set\",\"joint\":\"index\",\"min\":10}", "bad_json");
  expect_error("{\"cmd\":\"cal_set\",\"joint\":\"index\",\"inv\":\"yes\"}", "bad_json");
  expect_error("{\"cmd\":\"cal_set\",\"joint\":\"index\",\"rest\":3}", "bad_json");
  expect_error("{\"cmd\":\"raw\",\"joint\":\"elbow\",\"us\":1500}", "bad_joint");
  expect_error("{\"cmd\":\"raw\",\"joint\":\"index\"}", "bad_json");
  expect_error("{\"cmd\":\"pose\",", "bad_json");
}

void test_pose_error_carries_cmd_and_id() {
  Command c;
  parse("{\"cmd\":\"pose\",\"id\":\"Q.9\",\"j\":[0]}", c);
  char buf[256];
  size_t n = writeError(buf, sizeof(buf), c);
  TEST_ASSERT_TRUE(n > 0);
  TEST_ASSERT_NOT_NULL(strstr(buf, "\"err\":\"bad_length\""));
  TEST_ASSERT_NOT_NULL(strstr(buf, "\"cmd\":\"pose\""));
  TEST_ASSERT_NOT_NULL(strstr(buf, "\"id\":\"Q.9\""));
}

void test_cal_set_and_raw() {
  Command c;
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"cal_set\",\"joint\":\"index\",\"min\":550,\"max\":2350,\"inv\":true,\"rest\":0.1}", c));
  TEST_ASSERT_EQUAL(CMD_CAL_SET, c.type);
  TEST_ASSERT_EQUAL_INT(2, c.joint);
  CalEntry e = {2, 600, 2300, false, 0.15f};
  TEST_ASSERT_TRUE(applyCalSet(c, e));
  TEST_ASSERT_EQUAL_INT(550, e.minV);
  TEST_ASSERT_EQUAL_INT(2350, e.maxV);
  TEST_ASSERT_TRUE(e.inv);
  TEST_ASSERT_EQUAL_FLOAT(0.1f, e.rest);

  TEST_ASSERT_TRUE(parse("{\"cmd\":\"cal_set\",\"joint\":\"index\",\"min\":2400}", c));
  CalEntry before = e;
  TEST_ASSERT_FALSE(applyCalSet(c, e));  // merged min >= max
  TEST_ASSERT_EQUAL_STRING("bad_json", c.err);
  TEST_ASSERT_EQUAL_INT(before.minV, e.minV);

  TEST_ASSERT_TRUE(parse("{\"cmd\":\"raw\",\"joint\":\"wrist\",\"us\":1500}", c));
  TEST_ASSERT_EQUAL(CMD_RAW, c.type);
  TEST_ASSERT_EQUAL_INT(6, c.joint);
  TEST_ASSERT_EQUAL_INT(1500, c.rawValue);
  TEST_ASSERT_TRUE(parse("{\"cmd\":\"raw\",\"joint\":\"wrist\",\"us\":99999}", c));
  TEST_ASSERT_EQUAL_INT(2500, c.rawValue);  // clamped to the hard limit
}

void test_map_to_servo() {
  CalEntry c = {0, 600, 2300, false, 0.15f};
  TEST_ASSERT_EQUAL_INT(600, mapToServo(0.0f, c));
  TEST_ASSERT_EQUAL_INT(2300, mapToServo(1.0f, c));
  TEST_ASSERT_EQUAL_INT(1450, mapToServo(0.5f, c));
  TEST_ASSERT_EQUAL_INT(2300, mapToServo(9.0f, c));
  TEST_ASSERT_EQUAL_INT(600, mapToServo(-1.0f, c));
  c.inv = true;
  TEST_ASSERT_EQUAL_INT(2300, mapToServo(0.0f, c));
  TEST_ASSERT_EQUAL_INT(600, mapToServo(1.0f, c));
}

// ------------------------------------------------------------------ replies
void test_reply_formats() {
  char buf[512];
  writeOk(buf, sizeof(buf), "stop");
  TEST_ASSERT_EQUAL_STRING("{\"ok\":\"stop\"}", buf);
  writeDone(buf, sizeof(buf), "A.1", 1234);
  TEST_ASSERT_EQUAL_STRING("{\"done\":\"A.1\",\"t\":1234}", buf);
  writePong(buf, sizeof(buf), 42);
  TEST_ASSERT_EQUAL_STRING("{\"pong\":42}", buf);
  writeHello(buf, sizeof(buf), "0.1.0", "pca9685", CFG, "");
  TEST_ASSERT_EQUAL_STRING(
      "{\"ok\":\"hello\",\"fw\":\"0.1.0\",\"driver\":\"pca9685\",\"joints\":[\"thumb\",\"thumb_rot\",\"index\","
      "\"middle\",\"ring\",\"pinky\",\"wrist\"]}",
      buf);
  writeEvent(buf, sizeof(buf), "watchdog", "no message", "");
  TEST_ASSERT_EQUAL_STRING("{\"event\":\"watchdog\",\"detail\":\"no message\"}", buf);
  TEST_ASSERT_EQUAL(0, writeOk(buf, 5, "stop"));  // doesn't fit: nothing written
}

void test_cal_reply() {
  CalEntry cal[7];
  for (int i = 0; i < 7; ++i) {
    CalEntry e = {i, 600, 2300, false, 0.15f};
    cal[i] = e;
  }
  cal[2].inv = true;
  char buf[1024];
  size_t n = writeCal(buf, sizeof(buf), CFG, cal);
  TEST_ASSERT_TRUE(n > 0);
  TEST_ASSERT_NOT_NULL(strstr(buf, "{\"joint\":\"index\",\"ch\":2,\"min\":600,\"max\":2300,\"inv\":true,\"rest\":0.15}"));
}

// ------------------------------------------------------------------ framing
void test_line_reader() {
  LineReader r;
  const char* input = "{\"cmd\":\"ping\"}\r\n\n{\"cmd\":\"hello\"}\n";
  int lines = 0;
  for (const char* p = input; *p; ++p) {
    if (r.feed(*p) == LineReader::LINE_READY) {
      ++lines;
      TEST_ASSERT_EQUAL_STRING(lines == 1 ? "{\"cmd\":\"ping\"}" : "{\"cmd\":\"hello\"}", r.line());
    }
  }
  TEST_ASSERT_EQUAL_INT(2, lines);
}

void test_line_reader_overflow_recovers() {
  LineReader r;
  int overflow = 0;
  for (int i = 0; i < 600; ++i) TEST_ASSERT_EQUAL(LineReader::NONE, r.feed('x'));
  if (r.feed('\n') == LineReader::LINE_OVERFLOW) ++overflow;
  TEST_ASSERT_EQUAL_INT(1, overflow);
  const char* ok = "{\"cmd\":\"ping\"}\n";
  LineReader::Result last = LineReader::NONE;
  for (const char* p = ok; *p; ++p) last = r.feed(*p);
  TEST_ASSERT_EQUAL(LineReader::LINE_READY, last);
  TEST_ASSERT_EQUAL_STRING("{\"cmd\":\"ping\"}", r.line());
}

// ------------------------------------------------------------------ Feetech SCS packets
void test_scs_goal_packet() {
  uint8_t pkt[32];
  size_t n = buildScsGoal(1, 512, 0, 1000, pkt);
  const uint8_t expected[] = {0xFF, 0xFF, 0x01, 0x09, 0x03, 0x2A, 0x02, 0x00, 0x00, 0x00, 0x03, 0xE8, 0xDB};
  TEST_ASSERT_EQUAL_UINT(sizeof(expected), n);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, pkt, sizeof(expected));
}

void test_scs_torque_packet() {
  uint8_t pkt[16];
  uint8_t on = 1;
  size_t n = buildScsWrite(3, SCS_ADDR_TORQUE_ENABLE, &on, 1, pkt);
  // FF FF 03 04 03 28 01 CHK, CHK = ~(03+04+03+28+01) = ~0x33 = 0xCC
  const uint8_t expected[] = {0xFF, 0xFF, 0x03, 0x04, 0x03, 0x28, 0x01, 0xCC};
  TEST_ASSERT_EQUAL_UINT(sizeof(expected), n);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, pkt, sizeof(expected));
}

void test_scs_sync_packet() {
  uint8_t ids[2] = {1, 2};
  uint16_t pos[2] = {0x0123, 0x0300};
  uint8_t pkt[64];
  size_t n = buildScsSyncGoal(ids, pos, 2, pkt);
  TEST_ASSERT_EQUAL_UINT(8 + 7 * 2, n);
  TEST_ASSERT_EQUAL_HEX8(0xFE, pkt[2]);
  TEST_ASSERT_EQUAL_HEX8((6 + 1) * 2 + 4, pkt[3]);
  TEST_ASSERT_EQUAL_HEX8(0x83, pkt[4]);
  TEST_ASSERT_EQUAL_HEX8(0x2A, pkt[5]);
  TEST_ASSERT_EQUAL_HEX8(6, pkt[6]);
  TEST_ASSERT_EQUAL_HEX8(0x01, pkt[7]);
  TEST_ASSERT_EQUAL_HEX8(0x01, pkt[8]);  // big-endian position
  TEST_ASSERT_EQUAL_HEX8(0x23, pkt[9]);
  unsigned sum = 0;
  for (size_t i = 2; i < n - 1; ++i) sum += pkt[i];
  TEST_ASSERT_EQUAL_HEX8((uint8_t)(~sum & 0xFF), pkt[n - 1]);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_min_jerk_curve);
  RUN_TEST(test_motion_reaches_target_and_reports_once);
  RUN_TEST(test_motion_speed_limit);
  RUN_TEST(test_motion_clamps_and_holds);
  RUN_TEST(test_motion_new_target_starts_from_current_position);
  RUN_TEST(test_motion_millis_wraparound);
  RUN_TEST(test_parse_simple_commands);
  RUN_TEST(test_parse_pose);
  RUN_TEST(test_parse_errors_never_crash);
  RUN_TEST(test_pose_error_carries_cmd_and_id);
  RUN_TEST(test_cal_set_and_raw);
  RUN_TEST(test_map_to_servo);
  RUN_TEST(test_reply_formats);
  RUN_TEST(test_cal_reply);
  RUN_TEST(test_line_reader);
  RUN_TEST(test_line_reader_overflow_recovers);
  RUN_TEST(test_scs_goal_packet);
  RUN_TEST(test_scs_torque_packet);
  RUN_TEST(test_scs_sync_packet);
  return UNITY_END();
}
