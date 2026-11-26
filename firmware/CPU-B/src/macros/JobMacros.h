#ifndef JOB_MACROS_H
#define JOB_MACROS_H

#include "Constants.h"

const char RETURN_JOB_XY0[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS] = {

  MACRO_SPEED_MEDIUM,
  //add here a G53 for safe z axis bofore going to zero!!                   
  "G1 X0 Y0",
  "\0",

};




#endif // JOB_MACROS_H