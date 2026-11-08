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
#include <U8x8lib.h>

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
// The kit's SSD1315 works with the SSD1306 driver, on the hardware I2C bus (A4/A5).
// U8x8 (text-only, 16x8 character cells) writes straight to the display with no frame
// buffer. The U8g2 full-buffer mode needed 1 KB of the Uno's 2 KB RAM, which left ~45
// bytes of stack and corrupted the display.
// Hardware I2C at 400 kHz, not software I2C: SW I2C took tens of ms per text row, long
// enough for the 64-byte serial RX buffer to overflow and garble the bridge's commands.
U8X8_SSD1306_128X64_NONAME_HW_I2C u8x8(/* reset=*/U8X8_PIN_NONE);

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
// Software I2C is slow (a whole screen blocks for tens of ms, long enough to overflow the
// 64-byte serial RX buffer). So render() composes the text into small RAM strings, then
// updateOled() pushes one row per loop() pass, reading serial in between.
// Layout (8 rows of 16 chars): rows 0-1 big 2x2 title, rows 2-7 normal text.
const uint8_t COLS = 16, TEXT_ROW0 = 2, TEXT_ROWS = 6;
char oledTitle[COLS / 2 + 1];                // 8 big chars, space padded (always full width)
bool oledTitleInverse = false;
char oledText[TEXT_ROWS][COLS + 1];          // rows 2..7, space padded
char shownTitle[COLS / 2 + 1];               // what is on the glass now: only changed rows are sent
bool shownTitleInverse = false;
char shownText[TEXT_ROWS][COLS + 1];
int8_t oledRow = -1;   // next row to check/send, -1 = idle
uint32_t lastDrawMs = 0;

// Copy s into a space-padded fixed-width row (clears leftovers from the previous frame).
void setRow(char *dst, uint8_t width, const char *s, uint8_t col = 0) {
  memset(dst, ' ', width);
  dst[width] = 0;
  for (uint8_t i = col; i < width && *s; i++) dst[i] = *s++;
}

void render(uint32_t now) {
  char buf[COLS + 1];
  const char *title = (flareShownMs && now - flareShownMs < 1000) ? "FLARE!" : STATUS_NAMES[status];
  setRow(oledTitle, COLS / 2, title, (COLS / 2 - strlen(title)) / 2);
  oledTitleInverse = (status == ST_LOCK);    // solid bar = locked

  for (uint8_t i = 0; i < TEXT_ROWS; i++) setRow(oledText[i], COLS, "");
  if (haveAtt) {
    char v[10];
    dtostrf(attRa, 8, 2, v);   snprintf(buf, sizeof(buf), "RA   %s", v); setRow(oledText[1], COLS, buf);
    dtostrf(attDec, 8, 2, v);  snprintf(buf, sizeof(buf), "DEC  %s", v); setRow(oledText[2], COLS, buf);
    dtostrf(attRoll, 8, 2, v); snprintf(buf, sizeof(buf), "ROLL %s", v); setRow(oledText[3], COLS, buf);
  } else {
    setRow(oledText[2], COLS, "no attitude yet");
  }
  float rate = knobSent < 0 ? 0 : knobSent * KNOB_MAX_RATE / 1023.0;
  char r[8];
  dtostrf(rate, 4, 1, r);
  snprintf(buf, sizeof(buf), "TUMBLE %s d/s", r);
  setRow(oledText[TEXT_ROWS - 1], COLS, buf);
}

void updateOled(uint32_t now) {
  // At most one changed row goes out per loop() pass; unchanged rows are skipped.
  while (oledRow >= 0) {
    if (oledRow < TEXT_ROW0) {
      oledRow = TEXT_ROW0;
      if (oledTitleInverse != shownTitleInverse || strcmp(oledTitle, shownTitle)) {
        u8x8.setInverseFont(oledTitleInverse);
        u8x8.draw2x2String(0, 0, oledTitle);
        u8x8.setInverseFont(0);
        strcpy(shownTitle, oledTitle);
        shownTitleInverse = oledTitleInverse;
        return;
      }
    } else {
      uint8_t i = oledRow - TEXT_ROW0;
      if (++oledRow >= TEXT_ROW0 + TEXT_ROWS) oledRow = -1;
      if (strcmp(oledText[i], shownText[i])) {
        u8x8.drawString(0, TEXT_ROW0 + i, oledText[i]);
        strcpy(shownText[i], oledText[i]);
        return;
      }
    }
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

  u8x8.setBusClock(400000);
  u8x8.begin();
  u8x8.setFont(u8x8_font_chroma48medium8_r);
  u8x8.clearDisplay();
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
