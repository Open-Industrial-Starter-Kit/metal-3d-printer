#include    "GrblUtil.h"
#include <string.h>
#include "../Constants.h"

//example to parse the status string
//<Idle|MPos:0.000,0.000,0.000,0.000|Bf:100,1023|FS:0,0|Pn:YZAHSP|WCO:0.000,0.000,0.000,0.000>

bool GrblUtil::checkGRBLSpecialChar(char _char) {

  //if (_char == GRBL_STATUS_QUERY)
    //return true;

  //if (_char == GRBL_TILDE)
    //return true;

  //if (_char == GRBL_STOP)
    //return true;

  return false;
}

bool GrblUtil::checkGBRLSpecialByte(byte _byte) {

   //if (_byte == GRBL_STOP)
    //return true;

  return false;
}

MachineState GrblUtil::getMachineState(char* _statusReport) {

  if (strstr(_statusReport, GRBL_HOME_STATUS) != NULL)
    return HOME;

  if (strstr(_statusReport, GRBL_IDLE_STATUS) != NULL)
    return IDLE;

  if (strstr(_statusReport, GRBL_HOLD_STATUS) != NULL)
    return HOLD;

  if (strstr(_statusReport, GRBL_ALARM_STATUS) != NULL)
    return ALARM;

  if (strstr(_statusReport, GRBL_RUN_STATUS) != NULL)
    return RUN;

  if (strstr(_statusReport, GRBL_JOG_STATUS) != NULL)
    return JOG;

  if (strstr(_statusReport, GRBL_DOOR_STATUS) != NULL)
    return DOOR;

  return UNKNOWN;

}

bool GrblUtil::isProbeTriggered(char* _statusReport) {

  char* pinState = strstr(_statusReport, "|Pn:");

  if (pinState == NULL)
    return false;

  pinState += 4;

  while (pinState[0] != NULL_CHAR && pinState[0] != '|' && pinState[0] != '>') {
    if (pinState[0] == 'P')
      return true;

    ++pinState;
  }

  return false;

}

// Parses a GRBL [PRB:x,y,z:s] response.
// Returns true and sets *z if the probe was triggered (s == 1).
// Format example: [PRB:-10.325,-5.000,-45.678:1]
bool GrblUtil::parsePRBZ(char* line, float* z) {

  char* prb = strstr(line, "[PRB:");
  if (prb == nullptr)
    return false;

  // Find the last colon — separates coordinates from triggered flag
  char* lastColon = strrchr(prb, ':');
  if (lastColon == nullptr || lastColon == prb + 4)
    return false;

  // Check triggered flag
  if (lastColon[1] != '1')
    return false;

  // Skip "[PRB:" then advance past two commas to reach Z value
  char* p = prb + 5;
  int commas = 0;
  while (*p != '\0' && commas < 2) {
    if (*p == ',') commas++;
    p++;
  }

  if (commas < 2 || *p == '\0')
    return false;

  char* endPtr = nullptr;
  float val = strtof(p, &endPtr);
  if (endPtr == p)
    return false;

  *z = val;
  return true;

}

// Nth axis field of the MPos list (0 = X). The per-axis getters above/below walk the
// commas by hand, one extra copy of the same loop per axis; this is that walk written
// once, and is what getMachineB uses rather than a fifth copy.
float GrblUtil::getAxisField(char* _statusReport, const char* field, int axisIndex) {

  char* subString = strstr(_statusReport, field);

  if (subString == NULL)
    return GRBL_NULL_POSITION;

  subString += strlen(field) + 1;   // past the label and its ':'

  for (int skipped = 0; skipped < axisIndex; ++skipped) {

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;   // fewer axes reported than asked for

    ++subString;
  }

  char* endptr;
  float value = strtof(subString, &endptr);

  if (endptr == subString)
    return GRBL_NULL_POSITION;

  return value;

}

float GrblUtil::getMachineB(char* _statusReport) {

  return getAxisField(_statusReport, GRBL_MPOS, 4);

}

float GrblUtil::getJobB(char* _statusReport) {

  return getAxisField(_statusReport, GRBL_WPOS, 4);

}

float GrblUtil::getMachineX(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_MPOS);

  if (subString != NULL) {

    char* endptr;
    float bufferPositionX = strtof(subString + 5, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString + 5) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionX;
  
  }
  else
    return GRBL_NULL_POSITION;

}

float GrblUtil::getMachineY(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_MPOS);

  if (subString != NULL) {

    while (subString[0] != ',')//go on until you encounter a comma
      ++subString;

    ++subString;

    char* endptr;
    float bufferPositionY = strtof(subString, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionY;
  
  }
  else
    return GRBL_NULL_POSITION;
}

float GrblUtil::getMachineZ(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_MPOS);

  if (subString != NULL) {

    while (subString[0] != ',')//go on until you encounter a comma
      ++subString;

    ++subString;

    while (subString[0] != ',')//go on until you encounter a second comma
      ++subString;

    ++subString;

    char* endptr;
    float bufferPositionZ = strtof(subString, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionZ;
  
  }
  else
    return GRBL_NULL_POSITION;

}

float GrblUtil::getMachineA(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_MPOS);

  if (subString != NULL) {

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    char* endptr;
    float bufferPositionA = strtof(subString, &endptr);

    if (endptr == subString)
      return GRBL_NULL_POSITION;
    else
      return bufferPositionA;

  }
  else
    return GRBL_NULL_POSITION;

}

long GrblUtil::getFeedRate(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_FS);

  if (subString != NULL) {

    char* endptr;
    long feedRate = strtol(subString + 3, &endptr, 10);

    if (endptr == subString + 3)
      return -1L;

    return feedRate;

  }

  return -1L;
}

long GrblUtil::getSpindleRPM(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_FS);

  if (subString != NULL) {

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return -1L;

    ++subString;

    char* endptr;
    long spindleRPM = strtol(subString, &endptr, 10);

    if (endptr == subString)
      return -1L;

    return spindleRPM;

  }

  return -1L;
}

// Parses the spindle override percentage from the GRBL-HAL status report
// "Ov:feed,rapid,spindle" field (third value). GRBL-HAL only emits the Ov:
// field intermittently (on change and periodically), so returns -1 when the
// field is absent or unparseable; callers must keep their cached value in that
// case.
int GrblUtil::getSpindleOverride(char* _statusReport) {

  char* subString = strstr(_statusReport, GRBL_OV);

  if (subString == NULL)
    return -1;

  subString += 3;  // skip "Ov:"

  char* endptr;

  // feed override
  strtol(subString, &endptr, 10);
  if (endptr == subString || endptr[0] != ',')
    return -1;
  subString = endptr + 1;

  // rapid override
  strtol(subString, &endptr, 10);
  if (endptr == subString || endptr[0] != ',')
    return -1;
  subString = endptr + 1;

  // spindle override
  long spindleOverride = strtol(subString, &endptr, 10);
  if (endptr == subString)
    return -1;

  return (int)spindleOverride;

}

float GrblUtil::getJobX(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_WPOS);

  if (subString != NULL) {

    char* endptr;
    float bufferPositionX = strtof(subString + 4, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString + 4) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionX;
  
  }
  else
    return GRBL_NULL_POSITION;

}

float GrblUtil::getJobY(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_WPOS);

  if (subString != NULL) {

    while (subString[0] != ',')//go on until you encounter a comma
      ++subString;

    ++subString;

    char* endptr;
    float bufferPositionY = strtof(subString, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionY;
  
  }
  else
    return GRBL_NULL_POSITION;
}

float GrblUtil::getJobZ(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_WPOS);

  if (subString != NULL) {

    while (subString[0] != ',')//go on until you encounter a comma
      ++subString;

    ++subString;

    while (subString[0] != ',')//go on until you encounter a second comma
      ++subString;

    ++subString;

    char* endptr;
    float bufferPositionZ = strtof(subString, &endptr);
    
    // Check if parsing failed - endptr should point past the number
    if (endptr == subString) 
      return GRBL_NULL_POSITION;  // No valid number found
    else
      return bufferPositionZ;
  
  }
  else
    return GRBL_NULL_POSITION;
}

float GrblUtil::getJobA(char* _statusReport) {

  char* subString = NULL;
  subString = strstr(_statusReport, GRBL_WPOS);

  if (subString != NULL) {

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    while (subString[0] != ',' && subString[0] != NULL_CHAR)
      ++subString;

    if (subString[0] != ',')
      return GRBL_NULL_POSITION;

    ++subString;

    char* endptr;
    float bufferPositionA = strtof(subString, &endptr);

    if (endptr == subString)
      return GRBL_NULL_POSITION;
    else
      return bufferPositionA;

  }
  else
    return GRBL_NULL_POSITION;
}


bool GrblUtil::checkForOk(char* _line) {

  char* subString = NULL;
  subString = strstr(_line, GRBL_OK);

  if (subString != NULL)
    return true;

  return false;
}

// Counts the GRBL "ok" responses in a line read from CPUA while a job is streaming, tolerating the
// damage that noise on the CPUA<->CPUB link has been seen to cause ("k", "0k", "Pk" instead of "ok").
// A missed ok leaves waitForOk stuck and silently hangs the job, so during streaming it is safer to
// accept a damaged one: a false match only sends the next line early, which GRBL simply queues.
//  - every "ok" at the start of the line, right after another counted "ok", or right after a '>' or
//    ']' (the end of a status report / message whose line ending was lost) is counted, so two
//    responses glued into one line ("okok", "ok<Run|...>", "<Run|...>ok") are not undercounted;
//  - if none is found, a line of 1..4 characters that starts with 'o'/'O' or ends with 'k'/'K' is
//    taken as one damaged "ok". No real GRBL response is that short.
// *_recovered is set when the ok had to be recovered (damaged, glued or not at the line start), so
// the caller can log it.
int GrblUtil::countJobStreamingOks(const char* _line, bool* _recovered) {

  *_recovered = false;

  int length = 0;
  while (_line[length] != '\0' && _line[length] != NEWLINE && _line[length] != RETURN)
    ++length;

  int count = 0;
  int lastOkEnd = -1;

  for (int i = 0; i + 1 < length; ++i) {

    if (_line[i] != 'o' || _line[i + 1] != 'k')
      continue;

    bool atBoundary = (i == 0) || (i == lastOkEnd) || _line[i - 1] == '>' || _line[i - 1] == ']';

    if (!atBoundary)
      continue;

    if (i != 0)
      *_recovered = true;

    ++count;
    lastOkEnd = i + 2;
    ++i;
  }

  if (count > 1 || (count == 1 && length > 2))
    *_recovered = true;

  if (count > 0)
    return count;

  if (length >= 1 && length <= 4) {

    char first = _line[0];
    char last = _line[length - 1];

    if (first == 'o' || first == 'O' || last == 'k' || last == 'K') {
      *_recovered = true;
      return 1;
    }
  }

  return 0;
}

bool GrblUtil::isStatusReport(char* _line) {

  if (strstr(_line, GRBL_MPOS) != NULL)
    return true;

  return false;
}

bool GrblUtil::isResetReport(char* _line) {

  if (strstr(_line, GRBL_RESET_STRING) != NULL)
    return true;

  return false;
}

bool GrblUtil::parseGCodeWord(const char* line, char word, long* value) {

  if (line == nullptr || value == nullptr) return false;

  const char upperWord = static_cast<char>(toupper(static_cast<unsigned char>(word)));

  for (int i = 0; line[i] != NULL_CHAR; ++i) {

    if (line[i] == ';') break;

    const char cu = static_cast<char>(toupper(static_cast<unsigned char>(line[i])));

    if (cu != upperWord) continue;

    const char* start = line + i + 1;
    if (*start == NULL_CHAR) break;

    char* end;
    *value = strtol(start, &end, 10);
    if (end != start) return true;

  }

  return false;

}

bool GrblUtil::shouldSkipLineForDryRun(const char* line) {

  if (line == nullptr) return true;

  bool hasX = false, hasY = false;
  for (int i = 0; line[i] != NULL_CHAR && line[i] != NEWLINE && line[i] != RETURN; ++i) {
    if (line[i] == ';') break;
    const char c = toupper(static_cast<unsigned char>(line[i]));
    if      (c == 'X') hasX = true;
    else if (c == 'Y') hasY = true;
  }

  long gCode = -1;
  bool hasGCode = parseGCodeWord(line, 'G', &gCode);

  if (hasGCode) {

    // Always skip homing / return-to-predefined-position
    if (gCode == 28 || gCode == 30) return true;

    // Motion codes: allow if there is at least one X or Y word
    if (gCode == 0 || gCode == 1 || gCode == 2 || gCode == 3 || gCode == 53)
      return (!hasX && !hasY);

    // Safe modal G-codes: always allow
    if (gCode == 17 || gCode == 18 || gCode == 19 ||
        gCode == 20 || gCode == 21 ||
        gCode == 90 || gCode == 91 ||
        gCode == 92 ||
        gCode == 93 || gCode == 94 ||
        (gCode >= 54 && gCode <= 59))
      return false;

    // Any other G code not in whitelist: skip
    return true;

  }

  // No G code on this line — modal motion continuation
  if (hasX || hasY) return false;

  return true;

}

void GrblUtil::sanitizeLineForDryRun(const char* input, char* output, int outputSize) {

  if (input == nullptr || output == nullptr || outputSize <= 0) return;

  int outIdx = 0;
  int inIdx  = 0;

  while (input[inIdx] != NULL_CHAR && input[inIdx] != NEWLINE && input[inIdx] != RETURN
         && outIdx < outputSize - 1) {

    if (input[inIdx] == ';') break;

    const char cu = toupper(static_cast<unsigned char>(input[inIdx]));

    if (cu == 'Z' || cu == 'M' || cu == 'S' || cu == 'F') {
      ++inIdx;
      if (input[inIdx] == '+' || input[inIdx] == '-') ++inIdx;
      while (input[inIdx] == '.' || (input[inIdx] >= '0' && input[inIdx] <= '9')) ++inIdx;
      while (input[inIdx] == ' ' || input[inIdx] == '\t') ++inIdx;
      continue;
    }

    output[outIdx++] = input[inIdx++];
  }

  while (outIdx > 0 && (output[outIdx - 1] == ' ' || output[outIdx - 1] == '\t')) --outIdx;

  output[outIdx] = NULL_CHAR;

  // Convert G0 (rapid) to G1 so all dry-run moves respect the feed rate cap.
  // Scan for the first G word and, if its value is 0, replace it with 1.
  for (int i = 0; output[i] != NULL_CHAR; ++i) {
    if (toupper(static_cast<unsigned char>(output[i])) == 'G') {
      int j = i + 1;
      while (output[j] == ' ' || output[j] == '\t') ++j;
      int numStart = j;
      long gNum = 0;
      while (output[j] >= '0' && output[j] <= '9') {
        gNum = gNum * 10 + (output[j] - '0');
        ++j;
      }
      if (gNum == 0 && j > numStart) {
        output[numStart] = '1';
        for (int k = numStart + 1; k < j; ++k) output[k] = ' ';
      }
      break;
    }
  }

}

char *GrblUtil::getMachineStateString(MachineState machineState) {

  if (machineState == IDLE)
    return GRBL_IDLE_STATUS;

  if (machineState == HOME)
    return GRBL_HOME_STATUS;

  if (machineState == HOLD)
    return GRBL_HOLD_STATUS;

  if (machineState == ALARM)
    return GRBL_ALARM_STATUS;

  if (machineState == RUN)
    return GRBL_RUN_STATUS;

  if (machineState == JOG)
    return GRBL_JOG_STATUS;

  if (machineState == DOOR)
    return GRBL_DOOR_STATUS;

  return GRBL_UNKNOWN_STATUS;

}