#include   "TimerUtil.h"
#include <Arduino.h>
#include "../Config.h"


bool TimerUtil::blinkLedTimer() {

if (startTimeBlinkLed == 0) {

    startTimeBlinkLed = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeBlinkLed) >= LED_BLINK_INTERVAL) {
    startTimeBlinkLed = currentTime;
    return true;
  }

  return false;
}

void TimerUtil::resetBlinkLedTimer() {

  startTimeBlinkLed = millis();

}

void TimerUtil::resetSpindleTimer() {

    startTimeSpindle = millis();

}

bool TimerUtil::spindleTimer() {

  if (startTimeSpindle == 0) {

    startTimeSpindle = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeSpindle) >= SPINDLE_INCREMENT_INTERVAL) {
    startTimeSpindle = currentTime;
    return true;
  }

  return false;
}

void TimerUtil::resetStatusTimer() {

    startTimeStatus = millis();

}

bool TimerUtil::statusTimer() {

  if (startTimeStatus == 0) {

    startTimeStatus = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeStatus) >= ASK_STATUS_INTERVAL) {
    startTimeStatus = currentTime;
    return true;

  }

  return false;

}

bool TimerUtil::ESP32C3CheckTimer() {

  if (startTimeESP32C3Check == 0) {

    startTimeESP32C3Check = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeESP32C3Check) >= ESP32C3_CHECK_DELAY) {
    startTimeESP32C3Check = currentTime;
    return true;

  }

  return false;

}

void TimerUtil::resetESP32C3CheckTimer() {

  startTimeESP32C3Check = millis();

}

bool TimerUtil::webSocketPingTimer() {

  if (startTimeWebSocketPing == 0) {

    startTimeWebSocketPing = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeWebSocketPing) >= ESP32C3_CHECK_DELAY) {
    
    startTimeWebSocketPing = currentTime;
    return true;

  }

  return false;


}


void TimerUtil::resetWebSocketPingTimer() {

  startTimeWebSocketPing = millis();

}

