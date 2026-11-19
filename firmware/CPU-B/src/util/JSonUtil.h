#ifndef JSON_UTIL_H
#define JSON_UTIL_H

#include "../Constants.h"
#include <ArduinoJson.h>

class JsonUtil {
public:

    bool isValidJson(const char* jsonString);
    JsonValueType getJsonType(JsonDocument json);
    JsonValueCommandType getJsonCommandType(JsonDocument json);
    JsonValueGetType getJsonGetType(JsonDocument json);
    JsonValueSetType getJsonSetType(JsonDocument json);
    bool getJsonFilenameValue(JsonDocument json, char* buffer, size_t bufferSize);

private:


};

#endif // JSON_UTIL_H