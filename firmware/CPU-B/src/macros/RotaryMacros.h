#ifndef ROTARY_MACROS_H
#define ROTARY_MACROS_H

#include "Constants.h"

const char MOVE_TO_ROTARY_POSITION[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  "G1 F1000",
  "G53 Y-60.78", //24V
  "\0",

};

const char INSTALL_ROTARY[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  "$HZ",
  "G1 F1000",
  "G53 X72.5",
  "G53 Y-0.5",
  "\0",

};


#endif // ROTARY_MACROS_H