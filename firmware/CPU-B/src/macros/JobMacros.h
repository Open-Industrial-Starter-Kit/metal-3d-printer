#ifndef JOB_MACROS_H
#define JOB_MACROS_H

#include "Constants.h"

const char RETURN_JOB_XY0[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  MACRO_SPEED_MEDIUM,
  //add here a G53 for safe z axis bofore going to zero!!                   
  "G1 X0 Y0",
  "\0",

};

// NOTE: lines [3] and [4] (the G10 touch-point offset and the retract height) are
// PLACEHOLDERS — they are rebuilt at runtime from the EEPROM-backed probe height
// (probeHeightSettings, default DEFAULT_PROBE_HEIGHT_MM, retract = offset +
// PROBE_HEIGHT_RETRACT_MARGIN_MM) when the macro is launched. Do not tune them here.
const char PROBE_SET_JOB_Z0[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  "G91",
  "G38.2 Z-10 F100",
  "G90",
  "G10 L20 P1 Z14",
  "G1 Z17 F250",
  "\0",

};

const char PROBE_TOOL_LENGTH[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  "G91",
  "G38.2 Z-60 F50",
  "G1 Z5 F250",
  "G90",
  "\0",

};


#endif // JOB_MACROS_H