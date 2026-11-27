// G-code interpreter for CPU-C.
//
// Turns a buffer of G-code text into a stream of galvo positions at the XY2-100 frame rate.
// The buffer normally lives in PSRAM; this module only reads it, so it does not care.
//
// The design is a PULL generator: gcode_next_point() produces one position per call, and the
// caller decides when to ask. That keeps parsing decoupled from the frame clock -- a long
// move yields thousands of points from one parsed line, and a run of very short segments
// simply parses more often. The caller can buffer as deeply as it likes without this module
// knowing.
//
// Supported, deliberately a small set:
//   G0            rapid / jump   (laser gated off for the move)
//   G1            linear move    (laser gated per the current M3/M5 state)
//   F             feed in mm/min; overrides mark_speed for the moves that follow
//   G90 / G91     absolute / relative
//   G20 / G21     inches / millimetres
//   M3 / M4       laser on, power from S (0..1 scaled by GCODE_S_MAX)
//   M5            laser off
//   M600          layer boundary -- see below
//   ; and ( )     comments
//
// Anything else is skipped rather than rejected: a slicer emits plenty of lines that mean
// nothing to a galvo, and stopping on them would be useless.
//
// M600 is the layer boundary. The generator stops at it and reports GCODE_LAYER_END, so the
// caller can park the mirrors, gate the laser off and let CPU-B run the recoat before
// calling gcode_resume() for the next layer. Optional parameters:
//   M600 L<n>     layer index, reported back for progress and resume
//   M600 Z<mm>    layer thickness, passed up for the Z move

#ifndef GCODE_H
#define GCODE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Which M-code ends a layer. Single point of change if the post-processor uses another.
#define GCODE_LAYER_END_MCODE 600

// S word that means full power. Matches grblHAL's $30 convention.
#define GCODE_S_MAX 1000.0f

typedef enum {
    GCODE_POINT = 0,   // a position was produced
    GCODE_LAYER_END,   // hit the layer boundary; caller should recoat, then gcode_resume()
    GCODE_DONE,        // end of buffer
    GCODE_ERROR        // malformed beyond recovery
} gcode_status_t;

typedef struct {
    float x_mm, y_mm;     // where the mirrors should be
    bool  laser_on;       // gate: false on jumps and while M5 is in force
    float power;          // 0..1, from S
} gcode_point_t;

typedef struct {
    // Set these before gcode_load(). Speeds are mm/s and become point spacing at the frame
    // rate; delays are expressed in frames because that is the only clock that exists here.
    float    mark_speed_mm_s;
    float    jump_speed_mm_s;
    uint16_t jump_delay_frames;    // settle after a jump, before the laser may fire
    uint16_t mark_delay_frames;    // settle at the end of a mark, before the next jump
    uint16_t laser_on_delay_frames;// gate the laser this long after motion starts
} gcode_config_t;

extern gcode_config_t gcode_config;

// Point the interpreter at a buffer. Does not copy; the buffer must outlive the run.
void gcode_load(const char *text, size_t len);

// Restart from the beginning of the buffer with modal state reset.
void gcode_start(void);

// Continue past a GCODE_LAYER_END.
void gcode_resume(void);

void gcode_abort(void);
bool gcode_is_running(void);

// Progress reporting.
size_t   gcode_byte_offset(void);
uint32_t gcode_layer_index(void);
float    gcode_layer_thickness_mm(void);

// Produce the next position. Returns GCODE_POINT and fills *out, or one of the other
// statuses, in which case *out is untouched.
gcode_status_t gcode_next_point(gcode_point_t *out);

#endif // GCODE_H
