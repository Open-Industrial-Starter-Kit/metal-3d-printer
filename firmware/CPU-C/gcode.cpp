#include "gcode.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

// The XY2-100 frame rate, fixed by the PIO program: 20 bits at 16 cycles each from a
// 32 MHz state machine clock is 10 us per frame. Speeds become point spacing through this.
#define GALVO_FRAME_HZ 100000.0f

gcode_config_t gcode_config = {
    /* mark_speed_mm_s        */ 1000.0f,
    /* jump_speed_mm_s        */ 3000.0f,
    /* jump_delay_frames      */ 30,     // 300 us
    /* mark_delay_frames      */ 20,     // 200 us
    /* laser_on_delay_frames  */ 5,      //  50 us
};

// --- buffer and position -----------------------------------------------------------------
static const char *s_buf;
static size_t      s_len;
static size_t      s_pos;
static bool        s_running;

// --- modal state -------------------------------------------------------------------------
static bool  s_absolute;
static float s_unit;          // 1.0 for mm, 25.4 for inches
static float s_x, s_y;
static bool  s_laser_on;      // M3/M4 in force
static float s_feed_mm_s;     // from F, 0 = use the configured mark speed
static float s_power;         // 0..1

// --- the move being emitted --------------------------------------------------------------
static bool     s_in_move;
static float    s_x0, s_y0, s_x1, s_y1;
static uint32_t s_steps, s_step;
static bool     s_move_marks; // this move fires the laser (G1 with M3 in force)

// --- holds: settle time expressed in frames, emitted as repeats of the same position ------
static uint16_t s_hold;

// --- layer reporting ---------------------------------------------------------------------
static uint32_t s_layer;
static float    s_layer_z;

// -----------------------------------------------------------------------------------------

static void reset_modal(void)
{
    s_absolute = true;
    s_unit     = 1.0f;
    s_x = s_y  = 0.0f;
    s_laser_on = false;
    s_power    = 0.0f;
    s_feed_mm_s = 0.0f;
    s_in_move  = false;
    s_hold     = 0;
    s_steps = s_step = 0;
    s_move_marks = false;
    s_layer   = 0;
    s_layer_z = 0.0f;
}

void gcode_load(const char *text, size_t len)
{
    s_buf = text; s_len = len; s_pos = 0; s_running = false;
    reset_modal();
}

void gcode_start(void)  { s_pos = 0; reset_modal(); s_running = (s_buf && s_len); }
void gcode_resume(void) { if (s_buf && s_pos < s_len) s_running = true; }
void gcode_abort(void)  { s_running = false; s_in_move = false; s_hold = 0; }
bool gcode_is_running(void) { return s_running; }

size_t   gcode_byte_offset(void)      { return s_pos; }
uint32_t gcode_layer_index(void)      { return s_layer; }
float    gcode_layer_thickness_mm(void) { return s_layer_z; }

// Returns the next line as [start,end). Blank lines are returned too; the caller skips them
// cheaply because no word matches.
static bool next_line(const char **start, const char **end)
{
    while (s_pos < s_len && (s_buf[s_pos] == '\n' || s_buf[s_pos] == '\r')) s_pos++;
    if (s_pos >= s_len) return false;

    *start = s_buf + s_pos;
    size_t e = s_pos;
    while (e < s_len && s_buf[e] != '\n' && s_buf[e] != '\r') e++;
    *end = s_buf + e;
    s_pos = e;
    return true;
}

// Finds `letter` outside comments and parses the number after it. G-code is case insensitive
// and both ; and ( ) comment styles appear in the wild.
static bool word(const char *p, const char *end, char letter, float *out)
{
    bool in_paren = false;
    for (const char *q = p; q < end; q++) {
        char c = *q;
        if (c == '(') { in_paren = true; continue; }
        if (c == ')') { in_paren = false; continue; }
        if (in_paren)  continue;
        if (c == ';')  break;
        if (c >= 'a' && c <= 'z') c = (char)(c - 32);
        if (c == letter) {
            char *e = NULL;
            float v = strtof(q + 1, &e);
            if (e == q + 1) return false;      // letter not followed by a number
            *out = v;
            return true;
        }
    }
    return false;
}

static void begin_move(float nx, float ny, bool rapid)
{
    s_x0 = s_x; s_y0 = s_y;
    s_x1 = nx;  s_y1 = ny;

    const float dx = nx - s_x, dy = ny - s_y;
    const float dist = sqrtf(dx * dx + dy * dy);

    // An F word, if the program gave one, wins for marking moves. G-code expresses feed in
    // mm/min, so it is converted here; jumps always use the configured jump speed.
    float speed = rapid ? gcode_config.jump_speed_mm_s
                        : (s_feed_mm_s > 0.0f ? s_feed_mm_s : gcode_config.mark_speed_mm_s);
    if (speed <= 0.0f) speed = 1000.0f;

    // One frame of travel. The galvo gets a position every frame whether or not it changed,
    // so speed is only ever expressed as how far apart consecutive positions are.
    const float spacing = speed / GALVO_FRAME_HZ;

    uint32_t steps = (uint32_t)(dist / spacing + 0.5f);
    if (steps == 0) steps = 1;            // a zero length move still occupies one frame

    s_steps = steps;
    s_step  = 0;
    s_move_marks = (!rapid && s_laser_on);
    s_in_move = true;
}

gcode_status_t gcode_next_point(gcode_point_t *out)
{
    if (!s_running) return GCODE_DONE;

    for (;;) {
        // 1. settling: hold position, laser gated off
        if (s_hold > 0) {
            s_hold--;
            out->x_mm = s_x; out->y_mm = s_y;
            out->laser_on = false; out->power = s_power;
            return GCODE_POINT;
        }

        // 2. mid move: emit the next interpolated position
        if (s_in_move) {
            s_step++;
            const float t = (float)s_step / (float)s_steps;
            const float x = s_x0 + t * (s_x1 - s_x0);
            const float y = s_y0 + t * (s_y1 - s_y0);

            if (s_step >= s_steps) {
                s_in_move = false;
                s_x = s_x1; s_y = s_y1;
                // settle before whatever comes next
                s_hold = s_move_marks ? gcode_config.mark_delay_frames
                                      : gcode_config.jump_delay_frames;
            }

            out->x_mm = x; out->y_mm = y;
            // hold the beam off for the first few frames so the mirrors are actually moving
            // before energy goes down
            out->laser_on = s_move_marks && (s_step > gcode_config.laser_on_delay_frames);
            out->power    = s_power;
            return GCODE_POINT;
        }

        // 3. need another line
        const char *ls, *le;
        if (!next_line(&ls, &le)) { s_running = false; return GCODE_DONE; }

        float v;

        if (word(ls, le, 'M', &v)) {
            const int m = (int)(v + 0.5f);
            if (m == GCODE_LAYER_END_MCODE) {
                float t;
                if (word(ls, le, 'L', &t)) s_layer   = (uint32_t)(t + 0.5f);
                else                       s_layer++;
                if (word(ls, le, 'Z', &t)) s_layer_z = t * s_unit;
                s_laser_on = false;            // a layer never ends with the beam live
                s_running  = false;            // caller resumes after the recoat
                return GCODE_LAYER_END;
            }
            if (m == 3 || m == 4) {
                s_laser_on = true;
                if (word(ls, le, 'S', &v)) {
                    s_power = v / GCODE_S_MAX;
                    if (s_power < 0.0f) s_power = 0.0f;
                    if (s_power > 1.0f) s_power = 1.0f;
                }
                continue;
            }
            if (m == 5) { s_laser_on = false; continue; }
            continue;                           // every other M word is not ours
        }

        if (word(ls, le, 'G', &v)) {
            const int g = (int)(v + 0.5f);
            if (g == 90) { s_absolute = true;  continue; }
            if (g == 91) { s_absolute = false; continue; }
            if (g == 20) { s_unit = 25.4f;     continue; }
            if (g == 21) { s_unit = 1.0f;      continue; }

            if (g == 0 || g == 1) {
                float nx = s_x, ny = s_y, w;
                if (word(ls, le, 'X', &w)) nx = s_absolute ? w * s_unit : s_x + w * s_unit;
                if (word(ls, le, 'Y', &w)) ny = s_absolute ? w * s_unit : s_y + w * s_unit;
                if (word(ls, le, 'S', &w)) {
                    s_power = w / GCODE_S_MAX;
                    if (s_power < 0.0f) s_power = 0.0f;
                    if (s_power > 1.0f) s_power = 1.0f;
                }
                if (word(ls, le, 'F', &w) && w > 0.0f) s_feed_mm_s = w / 60.0f;
                begin_move(nx, ny, g == 0);
                continue;
            }
            continue;                           // arcs and the rest are not supported yet
        }

        // a bare F line sets the feed for the moves that follow
        if (word(ls, le, 'F', &v) && v > 0.0f) s_feed_mm_s = v / 60.0f;

        // a line with an S but no motion word still updates power
        if (word(ls, le, 'S', &v)) {
            s_power = v / GCODE_S_MAX;
            if (s_power < 0.0f) s_power = 0.0f;
            if (s_power > 1.0f) s_power = 1.0f;
        }
        // comments and anything unrecognised: move on
    }
}
