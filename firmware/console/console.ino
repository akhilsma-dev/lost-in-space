// Lost in Space -- mission console firmware (Grove Beginner Kit / Arduino Uno)
//
//   D6  button  -> SOLAR FLARE      A0  rotary knob -> TUMBLE rate
//   D5  buzzer  (lock chirp / lost alarm)   D4  LED = attitude lock
//   OLED on I2C (A4/A5) via U8g2 software I2C   D9/D10 pan/tilt servos
//
// Newline-delimited serial protocol at 115200 baud; see ../PROTOCOL.md.
// Libraries: U8g2 (Library Manager), Servo (bundled).

#include <Arduino.h>
#include <Servo.h>
#include <U8g2lib.h>

// ---------------------------------------------------------------- pins
const uint8_t PIN_BTN = 6;
const uint8_t PIN_KNOB = A0;
const uint8_t PIN_BUZZ = 5;
const uint8_t PIN_LED = 4;
const uint8_t PIN_PAN = 9;
const uint8_t PIN_TILT = 10;
const bool BTN_ACTIVE_HIGH = true;   // Grove button module reads HIGH when pressed

// ---------------------------------------------------------------- tuning
const uint16_t DEBOUNCE_MS = 30;
const uint8_t KNOB_DELTA = 6;        // send KNOB when it moves at least this many counts
const uint16_t KNOB_MIN_MS = 50;     // ...but at most 20 times a second
const float KNOB_MAX_RATE = 20.0;    // deg/s shown on the OLED at full knob (bridge uses the same)
const float SERVO_MAX_DPS = 60.0;    // on-board slew-rate limit
const uint16_t SERVO_TICK_MS = 20;
const float PAN_MIN = 0, PAN_MAX = 180;
const float TILT_MIN = 20, TILT_MAX = 160;
const uint32_t LINK_TIMEOUT_MS = 3000;   // no laptop line for this long -> "NO LINK"
const uint16_t OLED_MIN_MS = 200;        // redraw at most 5x/s

// ---------------------------------------------------------------- OLED
// The kit's SSD1315 works with the SSD1306 driver; hardware I2C pins used as SW I2C.
U8G2_SSD1306_128X64_NONAME_F_SW_I2C u8g2(U8G2_R0, /* clock=*/SCL, /* data=*/SDA, U8X8_PIN_NONE);

// ---------------------------------------------------------------- state
enum Status : uint8_t { ST_NOLINK, ST_SEARCH, ST_LOCK, ST_LOST };
const char *const STATUS_NAMES[] = {"NO LINK", "SEARCH", "LOCK", "LOST"};

Status status = ST_NOLINK;
float attRa = 0, attDec = 0, attRoll = 0;
bool haveAtt = false;
uint32_t lastRxMs = 0;
uint32_t flareShownMs = 0;
bool displayDirty = true;

// ---------------------------------------------------------------- buzzer sequencer (non-blocking)
struct Note { uint16_t hz; uint16_t ms; };   // hz 0 = rest
const Note CHIRP[] = {{1800, 60}, {0, 30}, {2400, 90}};
const Note ALARM[] = {{900, 150}, {0, 80}, {700, 150}, {0, 80}, {900, 150}, {0, 80}, {700, 300}};
const Note CLICK[] = {{3000, 15}};
const Note *seq = nullptr;
uint8_t seqLen = 0, seqIdx = 0;
uint32_t seqNoteStart = 0;

void play(const Note *s, uint8_t n) {
  seq = s; seqLen = n; seqIdx = 0; seqNoteStart = millis();
  if (s[0].hz) tone(PIN_BUZZ, s[0].hz); else noTone(PIN_BUZZ);
}

void updateBuzzer(uint32_t now) {
  if (!seq) return;
  if (now - seqNoteStart < seq[seqIdx].ms) return;
  if (++seqIdx >= seqLen) { noTone(PIN_BUZZ); seq = nullptr; return; }
  seqNoteStart = now;
  if (seq[seqIdx].hz) tone(PIN_BUZZ, seq[seqIdx].hz); else noTone(PIN_BUZZ);
}

// ---------------------------------------------------------------- servos with slew limiting
struct Axis {
  Servo servo;
  float pos, target, lo, hi;
  void begin(uint8_t pin, float start, float l, float h) {
    lo = l; hi = h; pos = target = constrain(start, l, h);
    servo.attach(pin);
    write();
  }
  void write() { servo.writeMicroseconds(544 + (int)(pos * (2400 - 544) / 180.0 + 0.5)); }
  void setTarget(float t) { target = constrain(t, lo, hi); }
  void step(float maxStep) {
    float d = target - pos;
    if (d == 0) return;
    pos += constrain(d, -maxStep, maxStep);
    write();
  }
};
Axis pan, tilt;
uint32_t lastServoMs = 0;

void updateServos(uint32_t now) {
  if (now - lastServoMs < SERVO_TICK_MS) return;
  float dt = (now - lastServoMs) / 1000.0;
  lastServoMs = now;
  if (dt > 0.1) dt = 0.1;
  float maxStep = SERVO_MAX_DPS * dt;
  pan.step(maxStep);
  tilt.step(maxStep);
}

// ---------------------------------------------------------------- button (debounced, event on press)
bool btnStable = false, btnLastRaw = false;
uint32_t btnChangeMs = 0;

void updateButton(uint32_t now) {
  bool raw = digitalRead(PIN_BTN) == (BTN_ACTIVE_HIGH ? HIGH : LOW);
  if (raw != btnLastRaw) { btnLastRaw = raw; btnChangeMs = now; }
  if (raw != btnStable && now - btnChangeMs >= DEBOUNCE_MS) {
    btnStable = raw;
    if (btnStable) {
      Serial.println(F("BTN FLARE"));
      flareShownMs = now;
      play(CLICK, 1);
      displayDirty = true;
    }
  }
}

// ---------------------------------------------------------------- knob (smoothed, event on change)
float knobFilt = -1;
int knobSent = -1000;
uint32_t knobSentMs = 0;

void sendKnob(uint32_t now) {
  knobSent = (int)(knobFilt + 0.5);
  knobSentMs = now;
  Serial.print(F("KNOB "));
  Serial.println(knobSent);
  displayDirty = true;
}

void updateKnob(uint32_t now) {
  int raw = analogRead(PIN_KNOB);
  knobFilt = knobFilt < 0 ? raw : knobFilt + 0.2 * (raw - knobFilt);
  int v = (int)(knobFilt + 0.5);
  bool atEnd = (v <= 2 && knobSent > 2) || (v >= 1021 && knobSent < 1021);  // always reach the ends
  if ((abs(v - knobSent) >= KNOB_DELTA || atEnd) && now - knobSentMs >= KNOB_MIN_MS) sendKnob(now);
}

// ---------------------------------------------------------------- status changes
void setStatus(Status s) {
  if (s == status) return;
  Status prev = status;
  status = s;
  digitalWrite(PIN_LED, s == ST_LOCK ? HIGH : LOW);
  if (s == ST_LOCK) play(CHIRP, sizeof(CHIRP) / sizeof(Note));
  else if (s == ST_LOST && prev != ST_NOLINK) play(ALARM, sizeof(ALARM) / sizeof(Note));
  displayDirty = true;
}

// ---------------------------------------------------------------- serial input
char line[64];
uint8_t lineLen = 0;
bool lineOverflow = false;

void handleLine(char *s) {
  lastRxMs = millis();
  char *cmd = strtok(s, " \t");
  if (!cmd) return;
  if (!strcmp(cmd, "STATUS")) {
    char *a = strtok(nullptr, " \t");
    if (!a) return;
    if (!strcmp(a, "LOCK")) setStatus(ST_LOCK);
    else if (!strcmp(a, "LOST")) setStatus(ST_LOST);
    else if (!strcmp(a, "SEARCH")) setStatus(ST_SEARCH);
    else { Serial.println(F("ERR bad status")); return; }
  } else if (!strcmp(cmd, "ATT")) {
    char *a = strtok(nullptr, " \t"), *b = strtok(nullptr, " \t"), *c = strtok(nullptr, " \t");
    if (!a || !b || !c) { Serial.println(F("ERR ATT needs 3 numbers")); return; }
    attRa = atof(a); attDec = atof(b); attRoll = atof(c);
    haveAtt = true;
    displayDirty = true;
  } else if (!strcmp(cmd, "SERVO")) {
    char *a = strtok(nullptr, " \t"), *b = strtok(nullptr, " \t");
    if (!a || !b) { Serial.println(F("ERR SERVO needs 2 numbers")); return; }
    pan.setTarget(atof(a));
    tilt.setTarget(atof(b));
  } else if (!strcmp(cmd, "PING")) {
    Serial.println(F("PONG"));
    sendKnob(millis());
  } else {
    Serial.println(F("ERR unknown"));
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = 0;
      if (!lineOverflow && lineLen) handleLine(line);
      lineLen = 0;
      lineOverflow = false;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    } else {
      lineOverflow = true;   // drop the whole over-long line
    }
  }
}

// ---------------------------------------------------------------- OLED
// Software I2C is slow (a full 1 KB frame blocks for tens of ms, long enough to overflow
// the 64-byte serial RX buffer). So we render into the RAM buffer, then push it out one
// 8-pixel tile row per loop() pass, reading serial in between.
int8_t oledRow = -1;   // next tile row to send, -1 = idle
uint32_t lastDrawMs = 0;

void render(uint32_t now) {
  char buf[22];
  u8g2.clearBuffer();

  u8g2.setFont(u8g2_font_ncenB14_tr);
  const char *title = (flareShownMs && now - flareShownMs < 1000) ? "FLARE!" : STATUS_NAMES[status];
  u8g2.drawStr((128 - u8g2.getStrWidth(title)) / 2, 16, title);
  if (status == ST_LOCK) u8g2.drawFrame(0, 0, 128, 20);

  u8g2.setFont(u8g2_font_6x10_tr);
  if (haveAtt) {
    u8g2.drawStr(0, 31, "RA");   dtostrf(attRa, 8, 2, buf);   u8g2.drawStr(36, 31, buf);
    u8g2.drawStr(0, 41, "DEC");  dtostrf(attDec, 8, 2, buf);  u8g2.drawStr(36, 41, buf);
    u8g2.drawStr(0, 51, "ROLL"); dtostrf(attRoll, 8, 2, buf); u8g2.drawStr(36, 51, buf);
  } else {
    u8g2.drawStr(0, 41, "no attitude yet");
  }
  float rate = knobSent < 0 ? 0 : knobSent * KNOB_MAX_RATE / 1023.0;
  char r[8];
  dtostrf(rate, 4, 1, r);
  snprintf(buf, sizeof(buf), "TUMBLE %s deg/s", r);
  u8g2.drawStr(0, 63, buf);
}

void updateOled(uint32_t now) {
  if (oledRow >= 0) {
    u8g2.updateDisplayArea(0, oledRow, 16, 1);
    if (++oledRow >= 8) oledRow = -1;
    return;
  }
  bool flareExpired = flareShownMs && now - flareShownMs >= 1000;
  if (flareExpired) { flareShownMs = 0; displayDirty = true; }
  if (displayDirty && now - lastDrawMs >= OLED_MIN_MS) {
    displayDirty = false;
    lastDrawMs = now;
    render(now);
    oledRow = 0;
  }
}

// ---------------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BTN, INPUT);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUZZ, OUTPUT);
  digitalWrite(PIN_LED, LOW);

  u8g2.begin();
  pan.begin(PIN_PAN, 90, PAN_MIN, PAN_MAX);
  tilt.begin(PIN_TILT, 90, TILT_MIN, TILT_MAX);
  lastServoMs = millis();

  Serial.println(F("HELLO console 1"));
  updateKnob(millis());
  sendKnob(millis());
}

void loop() {
  uint32_t now = millis();
  readSerial();
  updateButton(now);
  updateKnob(now);
  updateServos(now);
  updateBuzzer(now);
  if (status != ST_NOLINK && now - lastRxMs > LINK_TIMEOUT_MS) {
    status = ST_NOLINK;
    digitalWrite(PIN_LED, LOW);
    displayDirty = true;
  }
  updateOled(now);
}
