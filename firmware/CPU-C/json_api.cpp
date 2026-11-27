#include "json_api.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "pico/stdlib.h"

api_state_t api_state = {
    /* debugging_base       */ false,
    /* enable_json_response */ true,
    /* galvo_streaming      */ true,
    /* galvo_relays         */ false,   // head stays unpowered until asked
    /* num_points           */ 0,
    /* shape                */ 1,
};

static api_write_fn s_write[API_SRC_COUNT] = { NULL, NULL };

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

static void api_fail(api_source_t src, const char *error)
{
    char body[128];
    snprintf(body, sizeof(body), "\"result\":\"failure\",\"error\":\"%s\"", error);
    api_send(src, body);
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
                 "\"galvoStreaming\":%s,\"galvoRelays\":%s,"
                 "\"shape\":%d,\"numPoints\":%d,\"uptimeMs\":%lu",
                 JSON_API_FIRMWARE_VERSION,
                 api_state.galvo_streaming ? "true" : "false",
                 api_state.galvo_relays ? "true" : "false",
                 api_state.shape, api_state.num_points,
                 (unsigned long)to_ms_since_boot(get_absolute_time()));
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

    if (strcmp(what, "setGalvoRelays") == 0) {
        if (!json_get_bool(json, "value", &value)) { api_fail(src, "missingJsonKey"); return; }
        api_state.galvo_relays = value;      // main() drives K6/K7 from this
        api_ok(src);

    } else if (strcmp(what, "setGalvoStreaming") == 0) {
        if (!json_get_bool(json, "value", &value)) { api_fail(src, "missingJsonKey"); return; }
        api_state.galvo_streaming = value;   // main() stops pushing frames when false
        api_ok(src);

    } else {
        api_fail(src, "unknownJsonCommandType");
    }
}

static void dispatch(api_source_t src, const char *json)
{
    char type[32];
    if (!json_get_string(json, "type", type, sizeof(type))) {
        api_fail(src, "unknownJsonType");
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
    else                                   api_fail(src, "unknownJsonType");
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
