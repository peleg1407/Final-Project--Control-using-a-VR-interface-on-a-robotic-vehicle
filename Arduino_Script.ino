#include <Arduino.h>
#include <Servo.h>
#include "Wire.h"
#include "SonarEZ0pw.h"
#include "I2Cdev.h"
#include "MPU6050.h"

// === MOTOR CONTROL PINS ===
int enA = 5;
int in1 = 8;
int in2 = 7;
int enB = 3;
int in3 = 4;
int in4 = 2;

// === SERVO SETUP ===
Servo steeringServo;
int servoPin = 6;

// === MPU6050 SENSOR SETUP ===
MPU6050 mpu;
float ax_ms2, ay_ms2, az_ms2;
float gx_dps, gy_dps, gz_dps;
float tempC;

// === ULTRASONIC SENSOR SETUP ===
SonarEZ0pw Sonar(12);
float distance_cm = 0.0;

// === COMMUNICATION & TIMING ===
String cmdBuffer = "";
unsigned long lastReceive = 0;
unsigned long lastSensorSend = 0;
const unsigned long TIMEOUT = 10;
const unsigned long SENSOR_INTERVAL = 50;

// === SMOOTHING GLOBALS ===
int currentSpeed = 0;
int targetSpeed  = 0;
String targetDir = "";
const int RAMP_STEP = 5;

int currentAngle = 85;
int targetAngle  = 85;
const int ANGLE_STEP = 2;

void setup() {
  // Motor pins
  pinMode(enA, OUTPUT);
  pinMode(in1, OUTPUT);
  pinMode(in2, OUTPUT);
  pinMode(enB, OUTPUT);
  pinMode(in3, OUTPUT);
  pinMode(in4, OUTPUT);

  // Servo setup
  steeringServo.attach(servoPin);
  steeringServo.write(currentAngle);

  // Serial & I2C
  Serial.begin(115200);
  Wire.begin();
  mpu.initialize();
  if (mpu.testConnection()) {
    Serial.println("MPU6050 connection successful");
  } else {
    Serial.println("MPU6050 connection failed");
  }
  delay(100);
}

void stopMotors() {
  digitalWrite(in1, LOW);
  digitalWrite(in2, LOW);
  analogWrite(enA, 0);
  digitalWrite(in3, LOW);
  digitalWrite(in4, LOW);
  analogWrite(enB, 0);
}

void moveMotors(const String& dir, int speed) {
  if (dir == "forward") {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
    analogWrite(enA, speed);
    digitalWrite(in3, HIGH);
    digitalWrite(in4, LOW);
    analogWrite(enB, speed);
  } else if (dir == "backward") {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
    analogWrite(enA, speed);
    digitalWrite(in3, LOW);
    digitalWrite(in4, HIGH);
    analogWrite(enB, speed);
  }
}

void processCommand(String command) {
  command.trim();

  if (command.startsWith("forward:") || command.startsWith("backward:")) {
    // Extract target direction and speed
    targetDir = command.startsWith("forward:") ? "forward" : "backward";
    targetSpeed = command.substring(command.indexOf(':') + 1).toInt();
    Serial.println("ack");

  } else if (command == "stop") {
    targetSpeed = 0;
    targetDir = "";
    Serial.println("ack");

  } else if (command.startsWith("servo:")) {
    targetAngle = command.substring(6).toInt();
    Serial.println("ack");
  }
}

void updateMotors() {
  static String lastDir = "";
  // Ramp speed toward target
  if (currentSpeed < targetSpeed) {
    currentSpeed = min(currentSpeed + RAMP_STEP, targetSpeed);
  } else if (currentSpeed > targetSpeed) {
    currentSpeed = max(currentSpeed - RAMP_STEP, targetSpeed);
  }
  // If direction changed, ensure a brief stop
  if (targetDir != lastDir) {
    stopMotors();
    delay(50);
    lastDir = targetDir;
  }
  // Apply movement
  if (targetDir == "forward" || targetDir == "backward") {
    moveMotors(targetDir, currentSpeed);
  } else {
    stopMotors();
  }
}

void updateServo() {
  // Ramp angle toward target
  if (currentAngle < targetAngle) {
    currentAngle = min(currentAngle + ANGLE_STEP, targetAngle);
  } else if (currentAngle > targetAngle) {
    currentAngle = max(currentAngle - ANGLE_STEP, targetAngle);
  }
  steeringServo.write(currentAngle);
}

void readSensorData() {
  int16_t ax_raw, ay_raw, az_raw;
  int16_t gx_raw, gy_raw, gz_raw;
  int16_t tempRaw;
  mpu.getAcceleration(&ax_raw, &ay_raw, &az_raw);
  mpu.getRotation(&gx_raw, &gy_raw, &gz_raw);
  tempRaw = mpu.getTemperature();
  ax_ms2 = ax_raw / 16384.0 * 9.81;
  ay_ms2 = ay_raw / 16384.0 * 9.81;
  az_ms2 = az_raw / 16384.0 * 9.81;
  gx_dps = gx_raw / 131.0;
  gy_dps = gy_raw / 131.0;
  gz_dps = gz_raw / 131.0;
  tempC  = tempRaw / 340.0 + 36.53;
  distance_cm = Sonar.Distance(cm);
}

void sendSensorData() {
  readSensorData();
  Serial.print("{\"ax\":"); Serial.print(ax_ms2, 2);
  Serial.print(",\"ay\":"); Serial.print(ay_ms2, 2);
  Serial.print(",\"az\":"); Serial.print(az_ms2, 2);
  Serial.print(",\"gx\":"); Serial.print(gx_dps, 2);
  Serial.print(",\"gy\":"); Serial.print(gy_dps, 2);
  Serial.print(",\"gz\":"); Serial.print(gz_dps, 2);
  Serial.print(",\"temp\":"); Serial.print(tempC, 2);
  Serial.print(",\"distance\":"); Serial.print(distance_cm, 1);
  Serial.println("}");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n') {
      processCommand(cmdBuffer);
      cmdBuffer = "";
    } else {
      cmdBuffer += c;
    }
    lastReceive = millis();
  }
  if (cmdBuffer.length() > 0 && millis() - lastReceive > TIMEOUT) cmdBuffer = "";
  if (Serial.available() > 128) { while (Serial.available()) Serial.read(); cmdBuffer = ""; }
  if (millis() - lastSensorSend > SENSOR_INTERVAL) {
    sendSensorData();
    lastSensorSend = millis();
  }
  updateMotors();
  updateServo();
}
