#ifndef TIMER_UTIL_H
#define TIMER_UTIL_H

class TimerUtil {
public:

bool blinkLedTimer();
void resetBlinkLedTimer();

bool spindleTimer();
void resetSpindleTimer();

bool statusTimer();
void resetStatusTimer();

bool ESP32C3CheckTimer();
void resetESP32C3CheckTimer();

bool webSocketPingTimer();
void resetWebSocketPingTimer();

private:

unsigned long startTimeBlinkLed = 0.0;
unsigned long startTimeSpindle = 0.0;
unsigned long startTimeStatus = 0.0;
unsigned long startTimeESP32C3Check = 0.0;
unsigned long startTimeWebSocket = 0.0;
unsigned long startTimeWebSocketPing = 0.0;

};

#endif // TIMER_UTIL_H