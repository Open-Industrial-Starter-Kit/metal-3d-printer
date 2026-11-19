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

bool TimerUtil::networkCheckTimer() {

  if (startTimeNetworkCheck == 0) {

    startTimeNetworkCheck = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeNetworkCheck) >= NETWORK_CHECK_DELAY) {
    startTimeNetworkCheck = currentTime;
    return true;

  }

  return false;

}

void TimerUtil::resetNetworkCheckTimer() {

  startTimeNetworkCheck = millis();

}

bool TimerUtil::webSocketPingTimer() {

  if (startTimeWebSocketPing == 0) {

    startTimeWebSocketPing = millis();
    return false;

  }

  unsigned long currentTime = millis();

  if ((currentTime - startTimeWebSocketPing) >= NETWORK_CHECK_DELAY) {
    
    startTimeWebSocketPing = currentTime;
    return true;

  }

  return false;


}


void TimerUtil::resetWebSocketPingTimer() {

  startTimeWebSocketPing = millis();

}

