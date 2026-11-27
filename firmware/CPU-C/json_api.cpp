#include "json_api.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "pico/stdlib.h"
#include "hardware/psram.h"
#include "layer.h"
#include "gcode.h"

api_state_t api_state = {
    /* debugging_base       */ false,
    /* enable_json_response */ true,
    /* galvo_streaming      */ false,   // nothing sweeps at boot; the built-in test
                                        // rectangle is opt-in via setGalvoStreaming
    /* galvo_power          */ false,   // every relay is open at boot and
    /* laser_interlock      */ false,   // only closes when something asks,
    /* laser_main_power     */ false,   // so nothing is energised by merely
    /* laser_remote_on      */ false,   // powering the board
    /* galvo_position_pending */ false,
    /* galvo_position_x_mm  */ 0.0,
    /* galvo_position_y_mm  */ 0.0,
    /* galvo_max_x_mm       */ 0.0,     // main() fills these in at startup
    /* galvo_max_y_mm       */ 0.0,
    /* num_points           */ 0,
    /* shape                */ 1,
};

static api_write_fn s_write[API_SRC_COUNT] = { NULL, NULL };
static api_source_t s_event_src = API_SRC_USB;   // where layer events go

static char   s_line[API_SRC_COUNT][JSON_API_LINE_CHARS];
static size_t s_len[API_SRC_COUNT]  = { 0, 0 };
static bool   s_over[API_SRC_COUNT] = { false, false };

// ---------------------------------------------------------------------------------------
// Flat JSON scanning. Values may be strings, bools or numbers; nesting, arrays and escape
// sequences are not supported and are not needed by the control envelope.
// ---------------------------------------------------------------------------------------

static const char *skip_ws(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    return p;
}

// Returns the first character of the value for `key`, or NULL. Only matches a genuine key,
// i.e. a quoted name followed by a colon, so a string VALUE of the same text is ignored.
static const char *find_value(const char *json, const char *key)
{
    size_t keylen = strlen(key);
    const char *p = json;

    while ((p = strchr(p, '"')) != NULL) {
        const char *name = p + 1;
        const char *end  = strchr(name, '"');
        if (end == NULL) return NULL;

        if ((size_t)(end - name) == keylen && strncmp(name, key, keylen) == 0) {
            const char *q = skip_ws(end + 1);
            if (*q == ':') return skip_ws(q + 1);
        }
        p = end + 1;
    }
    return NULL;
}

static bool json_get_string(const char *json, const char *key, char *out, size_t outlen)
{
    const char *v = find_value(json, key);
    if (v == NULL || *v != '"') return false;

    v++;
    const char *end = strchr(v, '"');
    if (end == NULL) return false;

    size_t n = (size_t)(end - v);
    if (n >= outlen) n = outlen - 1;
    memcpy(out, v, n);
    out[n] = '\0';
    return true;
}

// Like json_get_string but hands back a pointer into the original line. Used for the layer
// chunk payload, which is far too big to copy onto the stack.
static bool json_get_string_ref(const char *json, const char *key,
                                const char **start, size_t *len)
{
    const char *v = find_value(json, key);
    if (v == NULL || *v != '"') return false;
    v++;
    const char *end = strchr(v, '"');
    if (end == NULL) return false;
    *start = v;
    *len   = (size_t)(end - v);
    return true;
}

static bool json_get_bool(const char *json, const char *key, bool *out)
{
    const char *v = find_value(json, key);
    if (v == NULL) return false;

    if (strncmp(v, "true", 4) == 0)  { *out = true;  return true; }
    if (strncmp(v, "false", 5) == 0) { *out = false; return true; }
    // tolerate 0/1, which several JSON clients send for booleans
    if (*v == '0') { *out = false; return true; }
    if (*v == '1') { *out = true;  return true; }
    return false;
}

static bool json_get_double(const char *json, const char *key, double *out)
{
    const char *v = find_value(json, key);
    if (v == NULL) return false;
    if (*v != '-' && *v != '+' && *v != '.' && (*v < '0' || *v > '9')) return false;

    char *end = NULL;
    double d = strtod(v, &end);
    if (end == v) return false;

    *out = d;
    return true;
}

// ---------------------------------------------------------------------------------------
// Responses. Every reply carries "from":"cpuc" so a host reading CPU-B's mirrored stream
// can tell CPU-C's answers from CPU-B's own.
// ---------------------------------------------------------------------------------------

static void api_send(api_source_t src, const char *body)
{
    if (src >= API_SRC_COUNT || s_write[src] == NULL) return;

    char out[JSON_API_LINE_CHARS];
    snprintf(out, sizeof(out), "{\"from\":\"cpuc\",%s}\r\n", body);
    s_write[src](out);
}

static void api_ok(api_source_t src)
{
    api_send(src, "\"result\":\"success\"");
}

// Succeeds, but tells the caller something about the result they probably want to know.
// Used when a command is accepted and recorded yet cannot physically take effect.
static void api_ok_warn(api_source_t src, const char *warning)
{
    if (warning == NULL) { api_ok(src); return; }

    char body[160];
    snprintf(body, sizeof(body), "\"result\":\"success\",\"warning\":\"%s\"", warning);
    api_send(src, body);
}

static void api_fail(api_source_t src, const char *error)
{
    char body[128];
    snprintf(body, sizeof(body), "\"result\":\"failure\",\"error\":\"%s\"", error);
    api_send(src, body);
}

// Diagnostic variant: reports back what was actually received, so a parse failure can be
// told apart from a transport problem (lost or corrupted bytes on the CPU-B link).
static void api_fail_echo(api_source_t src, const char *error, const char *line)
{
    char safe[110];
    size_t n = 0;
    for (const char *q = line; *q && n < sizeof(safe) - 1; q++)
        if (*q != '"' && *q != '\\' && (unsigned char)*q >= 0x20)
            safe[n++] = *q;
    safe[n] = '\0';

    char body[240];
    snprintf(body, sizeof(body),
             "\"result\":\"failure\",\"error\":\"%s\",\"gotLen\":%u,\"got\":\"%s\"",
             error, (unsigned)strlen(line), safe);
    api_send(src, body);
}

void json_api_emit(const char *body)
{
    api_send(s_event_src, body);
}

// ---------------------------------------------------------------------------------------
// Command handling
// ---------------------------------------------------------------------------------------

static void handle_get(api_source_t src, const char *json)
{
    char what[48];
    if (!json_get_string(json, "getType", what, sizeof(what))) {
        api_fail(src, "missingJsonKey");
        return;
    }

    char body[320];

    if (strcmp(what, "getFirmwareVersion") == 0) {
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"firmwareVersion\":\"%s\"",
                 JSON_API_FIRMWARE_VERSION);
        api_send(src, body);

    } else if (strcmp(what, "getStatus") == 0) {
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"firmwareVersion\":\"%s\","
                 "\"galvoStreaming\":%s,\"galvoPower\":%s,"
                 "\"laserInterlock\":%s,\"laserMainPower\":%s,\"laserRemoteOn\":%s,"
                 "\"galvoX\":%.3f,\"galvoY\":%.3f,"
                 "\"shape\":%d,\"numPoints\":%d,\"uptimeMs\":%lu",
                 JSON_API_FIRMWARE_VERSION,
                 api_state.galvo_streaming ? "true" : "false",
                 api_state.galvo_power ? "true" : "false",
                 api_state.laser_interlock ? "true" : "false",
                 api_state.laser_main_power ? "true" : "false",
                 api_state.laser_remote_on ? "true" : "false",
                 api_state.galvo_position_x_mm, api_state.galvo_position_y_mm,
                 api_state.shape, api_state.num_points,
                 (unsigned long)to_ms_since_boot(get_absolute_time()));
        api_send(src, body);

    } else if (strcmp(what, "getLayerInfo") == 0) {

        const uint32_t stored = layer_bytes(), expect = layer_expected();
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"state\":\"%s\",\"layer\":%u,"
                 "\"bytes\":%u,\"bytesExpected\":%u,\"bytesRemaining\":%u,"
                 "\"capacityBytes\":%u,\"streamed\":%s",
                 layer_state_name(), (unsigned)layer_index(),
                 (unsigned)stored, (unsigned)expect,
                 (unsigned)(expect > stored ? expect - stored : 0),
                 (unsigned)layer_capacity(),
                 layer_get_state() == LAYER_DONE ? "true" : "false");
        // append live interpreter progress
        {
            char prog[96];
            snprintf(prog, sizeof(prog),
                     ",\"running\":%s,\"scanPos\":%u",
                     gcode_is_running() ? "true" : "false",
                     (unsigned)gcode_byte_offset());
            strncat(body, prog, sizeof(body) - strlen(body) - 1);
        }
        api_send(src, body);

    } else if (strcmp(what, "getPsramInfo") == 0) {

        // Proves the size rather than trusting the part number. Distinct patterns are
        // written to the first, middle and last words BEFORE any are read back: if the
        // device is smaller than claimed, the high addresses alias onto the low ones and
        // the earlier patterns are destroyed, which the readback then catches.
        const size_t size = psram_get_size();
        bool readback_ok = false;

        if (size >= 16) {
            volatile uint32_t *psram = (volatile uint32_t *)0x11000000u;
            const size_t words = size / 4;
            const size_t idx[3] = { 0, words / 2, words - 1 };
            const uint32_t pat[3] = { 0xA5A5F00Du, 0x5A5A0FF0u, 0xDEADBEEFu };

            for (int i = 0; i < 3; i++) psram[idx[i]] = pat[i];

            readback_ok = true;
            for (int i = 0; i < 3; i++)
                if (psram[idx[i]] != pat[i]) readback_ok = false;
        }

        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"psramAvailable\":%s,\"psramSizeBytes\":%u,"
                 "\"psramSizeMB\":%u,\"readbackOk\":%s,\"csPin\":%d",
                 psram_is_available() ? "true" : "false",
                 (unsigned)size, (unsigned)(size / (1024u * 1024u)),
                 readback_ok ? "true" : "false", PICO_PSRAM_CS_PIN);
        api_send(src, body);

    } else if (strcmp(what, "getDebuggingSettings") == 0) {
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"debuggingBase\":%s,\"enableJsonResponse\":%s",
                 api_state.debugging_base ? "true" : "false",
                 api_state.enable_json_response ? "true" : "false");
        api_send(src, body);

    } else {
        api_fail(src, "unknownJsonGetType");
    }
}

static void handle_set(api_source_t src, const char *json)
{
    char what[48];
    if (!json_get_string(json, "setType", what, sizeof(what))) {
        api_fail(src, "missingJsonKey");
        return;
    }

    bool value;
    if (!json_get_bool(json, "value", &value)) {
        api_fail(src, "missingJsonKey");
        return;
    }

    if (strcmp(what, "setDebuggingBase") == 0) {
        api_state.debugging_base = value;
    } else if (strcmp(what, "setEnableJsonResponse") == 0) {
        api_state.enable_json_response = value;
    } else {
        api_fail(src, "unknownJsonSetType");
        return;
    }

    api_ok(src);
}

static void handle_command(api_source_t src, const char *json)
{
    char what[48];
    if (!json_get_string(json, "commandType", what, sizeof(what))) {
        api_fail(src, "missingJsonKey");
        return;
    }

    bool value;

    // Every relay command is "name a relay, give it a state". main() applies these to the
    // GPIOs; nothing here touches hardware directly.
    struct { const char *name; bool *flag; } relays[] = {
        { "setGalvoPower",     &api_state.galvo_power      },  // K6 + K7, +/-15 V to the head
        { "setLaserInterlock", &api_state.laser_interlock  },  // Interlock_RLY
        { "setLaserMainPower", &api_state.laser_main_power },  // REMOTE_PWRON
        { "setLaserRemoteOn",  &api_state.laser_remote_on  },  // REMOTE_ON
    };

    for (size_t i = 0; i < sizeof(relays) / sizeof(relays[0]); i++) {
        if (strcmp(what, relays[i].name) == 0) {
            if (!json_get_bool(json, "value", &value)) { api_fail(src, "missingJsonKey"); return; }
            *relays[i].flag = value;
            api_ok(src);
            return;
        }
    }

    if (strcmp(what, "startLayerTransfer") == 0) {

        double bytes = 0, index = 0;
        if (!json_get_double(json, "bytes", &bytes)) { api_fail(src, "missingJsonKey"); return; }
        json_get_double(json, "layer", &index);      // optional

        const char *err = "layerError";
        if (!layer_begin((uint32_t)bytes, (uint32_t)index, &err)) { api_fail(src, err); return; }

        char body[128];
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"bytesRemaining\":%u",
                 (unsigned)(layer_expected() - layer_bytes()));
        api_send(src, body);

    } else if (strcmp(what, "setGalvoSpeeds") == 0) {

        // Speeds in mm/s, delays in frames (10 us each). Every field optional.
        double v;
        if (json_get_double(json, "markSpeed", &v) && v > 0) gcode_config.mark_speed_mm_s = (float)v;
        if (json_get_double(json, "jumpSpeed", &v) && v > 0) gcode_config.jump_speed_mm_s = (float)v;
        if (json_get_double(json, "jumpDelay", &v) && v >= 0) gcode_config.jump_delay_frames = (uint16_t)v;
        if (json_get_double(json, "markDelay", &v) && v >= 0) gcode_config.mark_delay_frames = (uint16_t)v;
        if (json_get_double(json, "laserOnDelay", &v) && v >= 0) gcode_config.laser_on_delay_frames = (uint16_t)v;

        char body[200];
        snprintf(body, sizeof(body),
                 "\"result\":\"success\",\"markSpeed\":%.1f,\"jumpSpeed\":%.1f,"
                 "\"jumpDelay\":%u,\"markDelay\":%u,\"laserOnDelay\":%u",
                 (double)gcode_config.mark_speed_mm_s, (double)gcode_config.jump_speed_mm_s,
                 gcode_config.jump_delay_frames, gcode_config.mark_delay_frames,
                 gcode_config.laser_on_delay_frames);
        api_send(src, body);

    } else if (strcmp(what, "startLayer") == 0) {

        const char *err = "layerError";
        if (!layer_mark_scanning(&err)) { api_fail(src, err); return; }

        gcode_load(layer_data(), layer_bytes());
        gcode_start();
        s_event_src = src;                 // layerComplete goes back to whoever asked

        // The test rectangle must not fight the interpreter for the FIFO.
        api_state.galvo_streaming = false;

        api_ok_warn(src, api_state.galvo_power ? NULL : "galvoPowerOff");

    } else if (strcmp(what, "resumeLayer") == 0) {

        const char *rerr = "layerNotScanning";
        if (!layer_mark_scanning(&rerr)) { api_fail(src, rerr); return; }
        gcode_resume();
        api_ok(src);

    } else if (strcmp(what, "abortLayer") == 0) {

        gcode_abort();
        layer_mark_done();
        api_ok(src);

    } else if (strcmp(what, "clearLayer") == 0) {

        if (layer_get_state() == LAYER_SCANNING || layer_get_state() == LAYER_PAUSED) {
            api_fail(src, "layerBusy"); return;
        }
        layer_clear();
        api_ok(src);

    } else if (strcmp(what, "setGalvoPosition") == 0) {

        double x, y;
        if (!json_get_double(json, "x", &x) || !json_get_double(json, "y", &y)) {
            api_fail(src, "missingJsonKey");
            return;
        }

        // Refuse rather than clamp: an out of range value is a client bug, and silently
        // moving somewhere else is worse than saying no.
        if (x < 0.0 || x > api_state.galvo_max_x_mm ||
            y < 0.0 || y > api_state.galvo_max_y_mm) {
            api_fail(src, "valueOutOfRange");
            return;
        }

        // A parked position would be overwritten by the very next sweep frame, so asking for
        // one implies manual control. Streaming can be turned back on explicitly.
        api_state.galvo_streaming      = false;
        api_state.galvo_position_x_mm  = x;
        api_state.galvo_position_y_mm  = y;
        api_state.galvo_position_pending = true;

        // The frames go out either way and the PIO will hold them, but with K6/K7 open the
        // head has no +/-15 V and will not move. Say so rather than report a bare success
        // that looks like the mirrors went somewhere.
        api_ok_warn(src, api_state.galvo_power ? NULL : "galvoPowerOff");

    } else if (strcmp(what, "setGalvoStreaming") == 0) {
        if (!json_get_bool(json, "value", &value)) { api_fail(src, "missingJsonKey"); return; }
        api_state.galvo_streaming = value;   // main() stops pushing frames when false
        // Same caveat as setGalvoPosition: streaming into an unpowered head does nothing.
        api_ok_warn(src, (value && !api_state.galvo_power) ? "galvoPowerOff" : NULL);

    } else {
        api_fail(src, "unknownJsonCommandType");
    }
}

static void dispatch(api_source_t src, const char *json)
{
    // Layer chunks first, before any other parsing. They are the only high volume message
    // on this link and carry no "type" envelope, exactly like CPU-B's "fcl".
    {
        const char *payload; size_t plen;
        if (json_get_string_ref(json, "lcl", &payload, &plen)) {
            const char *err = "layerError";
            if (!layer_append_chunk(payload, plen, &err)) { api_fail(src, err); return; }

            const uint32_t stored = layer_bytes(), expect = layer_expected();
            char body[160];
            if (layer_get_state() == LAYER_READY)
                snprintf(body, sizeof(body),
                         "\"result\":\"success\",\"bytesRemaining\":0,\"layerStored\":\"success\","
                         "\"bytes\":%u", (unsigned)stored);
            else
                snprintf(body, sizeof(body),
                         "\"result\":\"success\",\"bytesRemaining\":%u",
                         (unsigned)(expect > stored ? expect - stored : 0));
            api_send(src, body);
            return;
        }
    }

    char type[32];
    if (!json_get_string(json, "type", type, sizeof(type))) {
        api_fail_echo(src, "unknownJsonType", json);
        return;
    }

    // serialPing answers even with responses disabled, so it stays usable as a liveness
    // check in every configuration. This matches CPU-B.
    if (strcmp(type, "serialPing") == 0) {
        api_ok(src);
        return;
    }

    if (!api_state.enable_json_response) return;

    if      (strcmp(type, "get") == 0)     handle_get(src, json);
    else if (strcmp(type, "set") == 0)     handle_set(src, json);
    else if (strcmp(type, "command") == 0) handle_command(src, json);
    else                                   api_fail_echo(src, "unknownJsonType", json);
}

// ---------------------------------------------------------------------------------------

void json_api_init(api_write_fn write_usb, api_write_fn write_cpub)
{
    s_write[API_SRC_USB]  = write_usb;
    s_write[API_SRC_CPUB] = write_cpub;

    for (int i = 0; i < API_SRC_COUNT; i++) {
        s_len[i]  = 0;
        s_over[i] = false;
        s_line[i][0] = '\0';
    }
}

void json_api_feed(api_source_t src, char c)
{
    if (src >= API_SRC_COUNT) return;

    if (c == '\n' || c == '\r') {
        if (s_over[src]) {                  // the line never fit; refuse it as a whole
            api_fail(src, "lineTooLong");
            s_over[src] = false;
            s_len[src]  = 0;
            return;
        }
        if (s_len[src] == 0) return;        // bare newline, nothing to do

        s_line[src][s_len[src]] = '\0';
        s_len[src] = 0;
        dispatch(src, s_line[src]);
        return;
    }

    if (s_len[src] + 1 >= JSON_API_LINE_CHARS) {
        s_over[src] = true;                 // keep draining until the line ends
        return;
    }

    s_line[src][s_len[src]++] = c;
}
