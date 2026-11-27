// One-layer G-code buffer in PSRAM, with a state machine that says what may happen to it.
//
// Deliberately a SINGLE layer. The LPBF cycle gives a natural idle window -- while CPU-A
// runs the Z move, reservoir and recoater, the galvo has nothing to do -- and a layer of
// G-code crosses the 2 Mbaud link in well under a second against 5-15 s of recoat. So there
// is no need to buffer ahead, and one buffer keeps the ownership rules trivial.
//
// The states exist to answer the question "has this been streamed yet, and may I overwrite
// it?" without the caller having to track it:
//
//   EMPTY    nothing loaded
//   LOADING  a transfer is in progress
//   READY    complete and not yet scanned
//   SCANNING being streamed to the PIO right now -- refuses to be overwritten
//   PAUSED   stopped at an M600 layer boundary, waiting for the recoat and a resume.
//            Still refuses to be overwritten: the rest of the buffer has yet to be read.
//   DONE     fully streamed; safe to replace
//
// Capacity is 4 MB of the 8 MB fitted, which is ~25x a typical layer. The remaining half is
// left free so a second buffer can be added later without moving anything.

#ifndef LAYER_H
#define LAYER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LAYER_CAPACITY_BYTES (4u * 1024u * 1024u)

typedef enum {
    LAYER_EMPTY = 0,
    LAYER_LOADING,
    LAYER_READY,
    LAYER_SCANNING,
    LAYER_PAUSED,
    LAYER_DONE
} layer_state_t;

void          layer_init(void);
layer_state_t layer_get_state(void);
const char   *layer_state_name(void);

// Start a transfer. Refuses while SCANNING; on refusal *err names the reason and the
// existing contents are untouched. expected_bytes is what the sender will store, counting
// one newline per line.
bool layer_begin(uint32_t expected_bytes, uint32_t index, const char **err);

// Append one chunk. The payload is lines separated by '|', matching the PC-to-CPU-B
// protocol; each becomes a line in the buffer. Empty tokens are skipped.
bool layer_append_chunk(const char *payload, size_t len, const char **err);

void layer_clear(void);

// Hand-off to the interpreter.
const char *layer_data(void);
bool        layer_mark_scanning(const char **err);
void        layer_mark_paused(void);   // reached an M600 boundary
void        layer_mark_done(void);     // reached the end of the buffer

uint32_t layer_bytes(void);       // stored so far
uint32_t layer_expected(void);    // declared by the sender
uint32_t layer_index(void);
uint32_t layer_capacity(void);

#endif // LAYER_H
