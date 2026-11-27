// Minimal JSON control API for CPU-C.
//
// Mirrors the envelope CPU-B already speaks to the host, so one client can talk to both
// processors the same way. Commands reach CPU-C either on its own USB CDC port or over the
// UART link from CPU-B, which forwards anything carrying "target":"cpuc".
//
// The parser is deliberately small: it handles a FLAT object of string, bool and number
// values and nothing else. No nesting, no arrays, no escapes. That covers the control
// envelope; bulk scan data is expected to get its own binary framing later.

#ifndef JSON_API_H
#define JSON_API_H

#include <stdbool.h>
#include <stddef.h>

// Big enough for a layer-transfer chunk. The control envelope needs a fraction of this.
#define JSON_API_LINE_CHARS 2048
#define JSON_API_FIRMWARE_VERSION "0.1.0-bringup"

// Which transport a line arrived on; the reply goes back the same way.
typedef enum {
    API_SRC_USB  = 0,   // CPU-C's own USB CDC port
    API_SRC_CPUB = 1,   // UART link from CPU-B
    API_SRC_COUNT
} api_source_t;

typedef void (*api_write_fn)(const char *text);

// Everything the API can read or change. main() owns the hardware and applies these each
// pass, so the API module never touches a GPIO or a state machine itself.
typedef struct {
    bool debugging_base;        // chatty per-sweep logging on the console
    bool enable_json_response;  // when false only serialPing still answers
    bool galvo_streaming;       // push XY2-100 frames to the PIO

    // Relays. Every one of these is OFF at boot and only closes when asked.
    bool galvo_power;           // K6+K7 together: +/-15 V to the scan head
    bool laser_interlock;       // Interlock_RLY, the RFL-C200A interlock pairs
    bool laser_main_power;      // LaserPowerON  -> REMOTE_PWRON
    bool laser_remote_on;       // LaserRemoteON -> REMOTE_ON

    // Manual galvo positioning. setGalvoPosition parks the mirrors at one point instead of
    // running the sweep: main() packs the XY2-100 frames and pushes one of each, and the PIO
    // repeats the last word whenever its FIFO is empty, so the head holds there.
    bool   galvo_position_pending;  // set by the API, cleared by main() once applied
    double galvo_position_x_mm;     // last commanded position
    double galvo_position_y_mm;
    double galvo_max_x_mm;          // travel limits, filled in by main() at startup so the
    double galvo_max_y_mm;          // API can range check without duplicating the constants

    int  num_points;            // points in the current shape (read only)
    int  shape;                 // 0 = circle, 1 = rectangle (read only)
} api_state_t;

extern api_state_t api_state;

// write_usb / write_cpub send a finished response line on each transport.
void json_api_init(api_write_fn write_usb, api_write_fn write_cpub);

// Feed received bytes. Lines are assembled per source and dispatched on '\n' or '\r'.
void json_api_feed(api_source_t src, char c);

// Unsolicited event, wrapped as {"from":"cpuc",<body>} and sent to whichever transport
// started the current layer. Used for layerComplete and programComplete, which arrive
// without anyone having asked.
void json_api_emit(const char *body);

#endif // JSON_API_H
