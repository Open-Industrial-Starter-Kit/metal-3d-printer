#ifndef GRBL_UTIL_H
#define GRBL_UTIL_H

#include <Arduino.h>
#include "../Constants.h"

class GrblUtil {
public:

bool checkGRBLSpecialChar(char _char);
bool checkGBRLSpecialByte(byte _byte);
bool checkForOk(char* _line);
int countJobStreamingOks(const char* _line, bool* _recovered);
bool isStatusReport(char* _line);
bool isResetReport(char* _line);
MachineState getMachineState(char* _statusReport);
char *getMachineStateString(MachineState machineState);
bool isProbeTriggered(char* _statusReport);
bool parsePRBZ(char* line, float* z);

float getMachineX(char* _statusReport);
float getMachineY(char* _statusReport);
float getMachineZ(char* _statusReport);
float getMachineA(char* _statusReport);
long getFeedRate(char* _statusReport);
long getSpindleRPM(char* _statusReport);
int getSpindleOverride(char* _statusReport);

float getJobX(char* _statusReport);
float getJobY(char* _statusReport);
float getJobZ(char* _statusReport);
float getJobA(char* _statusReport);

bool shouldSkipLineForDryRun(const char* line);
void sanitizeLineForDryRun(const char* input, char* output, int outputSize);

private:

bool parseGCodeWord(const char* line, char word, long* value);

};

#endif // GRBL_UTIL_H