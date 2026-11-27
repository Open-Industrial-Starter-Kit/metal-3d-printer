#include "layer.h"

#include <string.h>
#include "pico/platform/sections.h"

// The buffer itself. Uninitialised so nothing is copied into PSRAM at boot.
static __uninitialized_psram("layer") char s_buf[LAYER_CAPACITY_BYTES];

static layer_state_t s_state = LAYER_EMPTY;
static uint32_t s_bytes;      // stored so far
static uint32_t s_expected;   // declared by the sender
static uint32_t s_index;      // layer number, for reporting

void layer_init(void)
{
    s_state = LAYER_EMPTY;
    s_bytes = s_expected = s_index = 0;
}

layer_state_t layer_get_state(void) { return s_state; }

const char *layer_state_name(void)
{
    switch (s_state) {
        case LAYER_EMPTY:    return "empty";
        case LAYER_LOADING:  return "loading";
        case LAYER_READY:    return "ready";
        case LAYER_SCANNING: return "scanning";
        case LAYER_DONE:     return "done";
    }
    return "unknown";
}

uint32_t layer_bytes(void)    { return s_bytes; }
uint32_t layer_expected(void) { return s_expected; }
uint32_t layer_index(void)    { return s_index; }
uint32_t layer_capacity(void) { return LAYER_CAPACITY_BYTES; }
const char *layer_data(void)  { return s_buf; }

void layer_clear(void)
{
    s_state = LAYER_EMPTY;
    s_bytes = s_expected = 0;
}

bool layer_begin(uint32_t expected_bytes, uint32_t index, const char **err)
{
    // The one rule worth enforcing: never overwrite a layer that is being scanned. Doing so
    // would pull the buffer out from under the interpreter mid-move.
    if (s_state == LAYER_SCANNING) { *err = "layerBusy"; return false; }

    if (expected_bytes == 0 || expected_bytes > LAYER_CAPACITY_BYTES) {
        *err = "layerTooLarge";
        return false;
    }

    s_state    = LAYER_LOADING;
    s_bytes    = 0;
    s_expected = expected_bytes;
    s_index    = index;
    return true;
}

bool layer_append_chunk(const char *payload, size_t len, const char **err)
{
    if (s_state != LAYER_LOADING) { *err = "layerTransferNotStarted"; return false; }

    const char *end = payload + len;
    for (const char *p = payload; p < end; ) {
        const char *sep = (const char *)memchr(p, '|', (size_t)(end - p));
        const size_t n = sep ? (size_t)(sep - p) : (size_t)(end - p);

        if (n > 0) {
            if (s_bytes + n + 1 > LAYER_CAPACITY_BYTES) { *err = "layerOverflow"; return false; }
            memcpy(s_buf + s_bytes, p, n);
            s_bytes += n;
            s_buf[s_bytes++] = '\n';
        }

        if (!sep) break;
        p = sep + 1;
    }

    if (s_bytes >= s_expected) s_state = LAYER_READY;
    return true;
}

bool layer_mark_scanning(const char **err)
{
    if (s_state != LAYER_READY && s_state != LAYER_DONE) { *err = "layerNotReady"; return false; }
    s_state = LAYER_SCANNING;
    return true;
}

void layer_mark_done(void)
{
    if (s_state == LAYER_SCANNING) s_state = LAYER_DONE;
}
