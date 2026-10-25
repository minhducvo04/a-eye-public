// Only ONE color is driven at a time: the kit has ONE shared 220-ohm resistor.
const bool SINGLE_LED = true;  // one plain LED on pin 9: states shown by pattern, not color
const bool COMMON_ANODE = false; // true: shared leg through resistor to +5V; false: to GND.
const byte PIR = 2, RED = 9, GREEN = 10, BLUE = 11;
char command[20];
byte used = 0;
bool overflow = false;
int previous = -1;
unsigned long heartbeat = 0, lastCommand = 0;
enum Mode { OFF, ARMED, EVENT, SESSION };
Mode mode = OFF;

void color(byte pin, byte brightness) {
  // Turn all channels off first, including while changing colors.
  analogWrite(RED, COMMON_ANODE ? 255 : 0);
  analogWrite(GREEN, COMMON_ANODE ? 255 : 0);
  analogWrite(BLUE, COMMON_ANODE ? 255 : 0);
  analogWrite(pin, COMMON_ANODE ? 255 - brightness : brightness);
}

void setup() {
  pinMode(PIR, INPUT);
  pinMode(RED, OUTPUT); pinMode(GREEN, OUTPUT); pinMode(BLUE, OUTPUT);
  color(RED, 0);
  Serial.begin(115200);
}

void loop() {
  unsigned long now = millis();
  int motion = digitalRead(PIR);
  if (motion != previous || now - heartbeat >= 1000) {
    Serial.println(motion ? "P1" : "P0");
    previous = motion; heartbeat = now;
  }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      command[used] = 0;
      if (!overflow) {
        if (!strcmp(command, "L OFF")) mode = OFF;
        else if (!strcmp(command, "L ARMED")) mode = ARMED;
        else if (!strcmp(command, "L EVENT")) mode = EVENT;
        else if (!strcmp(command, "L SESSION")) mode = SESSION;
        lastCommand = now;
      }
      used = 0; overflow = false;
    } else if (c != '\r') {
      if (used < sizeof(command) - 1) command[used++] = c;
      else overflow = true;
    }
  }
  if (now - lastCommand > 5000) mode = OFF; // Lost bridge: no stale active indication.
  if (mode == ARMED) {
    unsigned int phase = now % 4000;
    byte dim = 2 + (phase < 2000 ? phase : 4000 - phase) * 18UL / 2000;
    color(SINGLE_LED ? RED : BLUE, dim);
  } else if (mode == EVENT) color(RED, 100);
  else if (mode == SESSION) color(SINGLE_LED ? RED : GREEN, SINGLE_LED ? ((now / 500) % 2 ? 100 : 0) : 100);
  else color(RED, 0);
}
