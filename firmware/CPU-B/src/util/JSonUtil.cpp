#include "JSonUtil.h"
#include <ArduinoJson.h>
#include "../Constants.h"

bool JsonUtil::isValidJson(const char* jsonString) {

    JsonDocument json;
    DeserializationError error = deserializeJson(json, jsonString);
    
    if(error == DeserializationError::Ok) 
        return true;
     else 
        return false;
    
}

JsonValueType JsonUtil::getJsonType(JsonDocument json) {

    JsonObject jsonObject = json.as<JsonObject>();
    
    if(!jsonObject[JSON_KEY_TYPE_STR].isNull()) {

        if(strcmp(jsonObject[JSON_KEY_TYPE_STR], JSON_VALUE_COMMAND_STR) == 0)
            return COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_TYPE_STR], JSON_VALUE_SET_STR) == 0)
            return SET;
        else if(strcmp(jsonObject[JSON_KEY_TYPE_STR], JSON_VALUE_GET_STR) == 0)
            return GET;
        else if(strcmp(jsonObject[JSON_KEY_TYPE_STR], JSON_VALUE_SERIAL_PING_STR) == 0)
            return SERIAL_PING;
        else if(strcmp(jsonObject[JSON_KEY_TYPE_STR], JSON_VALUE_WEBSOCKET_PING_STR) == 0)
            return WEBSOCKET_PING;
        else
            return UNKNOWN_JSON_TYPE;

    }

    return UNKNOWN_JSON_TYPE;

}

JsonValueCommandType JsonUtil::getJsonCommandType(JsonDocument json) {

    JsonObject jsonObject = json.as<JsonObject>();

    if(!jsonObject[JSON_KEY_COMMAND_TYPE_STR].isNull()) {

        if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ENABLE_CPUA_PASSTHROUGH_STR) == 0)
            return ENABLE_CPUA_PASSTHROUGH;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_DISABLE_CPUA_PASSTHROUGH_STR) == 0)
            return DISABLE_CPUA_PASSTHROUGH;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ENABLE_ESP32_PASSTHROUGH_STR) == 0)
            return ENABLE_ESP32_PASSTHROUGH;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_DISABLE_ESP32_PASSTHROUGH_STR) == 0)
            return DISABLE_ESP32_PASSTHROUGH;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_TO_ROTARY_POSITION_MACRO_STR) == 0)
            return MOVE_TO_ROTARY_POSITION_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_INSTALL_ROTARY_MACRO_STR) == 0)
            return INSTALL_ROTARY_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_FRAME_JOB_MACRO_STR) == 0)
            return FRAME_JOB_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_PROBE_SET_JOB_Z0_MACRO_STR) == 0)
            return PROBE_SET_JOB_Z0_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_PROBE_TOOL_LENGTH_MACRO_STR) == 0)
            return PROBE_TOOL_LENGTH_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_RETURN_JOB_XY0_MACRO_STR) == 0)
            return RETURN_JOB_XY0_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_TO_LASER_OFFSET_ZERO_MACRO_STR) == 0)
            return MOVE_TO_LASER_OFFSET_ZERO_MACRO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_CONFIRM_MATERIAL_CENTER_AND_SET_ZERO_STR) == 0)
            return CONFIRM_MATERIAL_CENTER_AND_SET_ZERO;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_OPEN_GCODE_FILE_STR) == 0)
            return OPEN_GCODE_FILE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_CLOSE_GCODE_FILE_STR) == 0)
            return CLOSE_GCODE_FILE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_CLEAR_EEPROM_STR) == 0)
            return CLEAR_EEPROM;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_START_FILE_TRANSFER_STR) == 0)
            return START_FILE_TRANSFER; 
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ABORT_FILE_TRANSFER_STR) == 0)
            return ABORT_FILE_TRANSFER;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_START_FILE_DOWNLOAD_STR) == 0)
            return START_FILE_DOWNLOAD;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ABORT_FILE_DOWNLOAD_STR) == 0)
            return ABORT_FILE_DOWNLOAD;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_RENAME_GCODE_FILE_STR) == 0)
            return RENAME_GCODE_FILE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_START_JOB_STR) == 0)
            return START_JOB;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_STOP_JOB_RESET_STR) == 0)
            return STOP_JOB_RESET;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_SOFT_RESET_COMMAND_STR) == 0)
            return GRBL_SOFT_RESET_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_HOMING_COMMAND_STR) == 0)
            return GRBL_HOMING_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_HOMING_X_COMMAND_STR) == 0)
            return GRBL_HOMING_X_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_HOMING_Y_COMMAND_STR) == 0)
            return GRBL_HOMING_Y_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_HOMING_Z_COMMAND_STR) == 0)
            return GRBL_HOMING_Z_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_GRBL_UNLOCK_COMMAND_STR) == 0)
            return GRBL_UNLOCK_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_X_COMMAND_STR) == 0)
            return MOVE_X_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_Y_COMMAND_STR) == 0)
            return MOVE_Y_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_Z_COMMAND_STR) == 0)
            return MOVE_Z_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_XYZ_COMMAND_STR) == 0)
            return MOVE_XYZ_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_MACHINE_X_COMMAND_STR) == 0)
            return MOVE_MACHINE_X_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_MACHINE_Y_COMMAND_STR) == 0)
            return MOVE_MACHINE_Y_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_MACHINE_Z_COMMAND_STR) == 0)
            return MOVE_MACHINE_Z_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_MACHINE_XYZ_COMMAND_STR) == 0)
            return MOVE_MACHINE_XYZ_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_MOVE_A_COMMAND_STR) == 0)
            return MOVE_A_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_JOG_COMMAND_STR) == 0)
            return JOG_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_JOG_CANCEL_COMMAND_STR) == 0)
            return JOG_CANCEL_COMMAND;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_PAUSE_JOB_STR) == 0)
            return PAUSE_JOB;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_RESUME_JOB_STR) == 0)
            return RESUME_JOB;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ENABLE_LASER_SENSOR_STR) == 0)
            return ENABLE_LASER_SENSOR;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_DISABLE_LASER_SENSOR_STR) == 0)
            return DISABLE_LASER_SENSOR;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_DELETE_GCODE_FILE_STR) == 0)
            return DELETE_GCODE_FILE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_SET_FEED_RATE_OVERRIDE_STR) == 0)
            return SET_FEED_RATE_OVERRIDE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_SET_SPINDLE_OVERRIDE_STR) == 0)
            return SET_SPINDLE_OVERRIDE;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_PROBE_LASER_EDGES_STR) == 0)
            return PROBE_LASER_EDGES;
            else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_PROBE_LASER_MATERIAL_BOUNDS_STR) == 0)
                return PROBE_LASER_MATERIAL_BOUNDS;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_START_HEIGHT_MAP_SCAN_STR) == 0)
            return START_HEIGHT_MAP_SCAN;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ENABLE_HEIGHT_MAP_COMPENSATION_STR) == 0)
            return ENABLE_HEIGHT_MAP_COMPENSATION;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_DISABLE_HEIGHT_MAP_COMPENSATION_STR) == 0)
            return DISABLE_HEIGHT_MAP_COMPENSATION;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_CLEAR_HEIGHT_MAP_STR) == 0)
            return CLEAR_HEIGHT_MAP;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_START_POINT_CLOUD_SCAN_STR) == 0)
            return START_POINT_CLOUD_SCAN;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ABORT_POINT_CLOUD_SCAN_STR) == 0)
            return ABORT_POINT_CLOUD_SCAN;
        else if(strcmp(jsonObject[JSON_KEY_COMMAND_TYPE_STR], JSON_VALUE_ABORT_HEIGHT_MAP_SCAN_STR) == 0)
            return ABORT_HEIGHT_MAP_SCAN;
        else
            return UNKNOWN_JSON_COMMAND_TYPE;

    }

    return UNKNOWN_JSON_COMMAND_TYPE;

}

JsonValueGetType JsonUtil::getJsonGetType(JsonDocument json) {

    JsonObject jsonObject = json.as<JsonObject>();

    if(!jsonObject[JSON_KEY_GET_TYPE_STR].isNull()) {

        if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_STATUS_STR) == 0)
            return STATUS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_POSITIONS_STR) == 0)
            return POSITIONS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_MACHINE_POSITION_X_STR) == 0)
            return MACHINE_POSITION_X;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_MACHINE_POSITION_Y_STR) == 0)
            return MACHINE_POSITION_Y;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_MACHINE_POSITION_Z_STR) == 0)
            return MACHINE_POSITION_Z; 
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_MACHINE_POSITION_A_STR) == 0)
            return MACHINE_POSITION_A;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_MACHINE_POSITION_XYZ_STR) == 0)
            return MACHINE_POSITION_XYZ;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_POSITION_X_STR) == 0)
            return JOB_POSITION_X;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_POSITION_Y_STR) == 0)
            return JOB_POSITION_Y;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_POSITION_Z_STR) == 0)
            return JOB_POSITION_Z; 
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_POSITION_A_STR) == 0)
            return JOB_POSITION_A;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_POSITION_XYZ_STR) == 0)
            return JOB_POSITION_XYZ;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_DEVICE_ID_STR) == 0)
            return GET_DEVICE_ID;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_FILE_LIST_STR) == 0)
            return FILE_LIST;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GCODE_FILE_HASH_STR) == 0)
            return GCODE_FILE_HASH;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_OPENED_GCODE_FILE_NAME_STR) == 0)
            return OPENED_GCODE_FILE_NAME;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_JOB_PROGRESS_STR) == 0)
            return JOB_PROGRESS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_OPENED_GCODE_FILE_TOTAL_LINES_STR) == 0)
            return OPENED_GCODE_FILE_TOTAL_LINES;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_NEXT_FILE_DOWNLOAD_CHUNK_STR) == 0)
            return NEXT_FILE_DOWNLOAD_CHUNK;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_WIFI_SETTINGS_STR) == 0)
            return GET_WIFI_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_DEBUGGING_SETTINGS_STR) == 0)
            return GET_DEBUGGING_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_CURRENT_FEED_RATE_STR) == 0)
            return GET_CURRENT_FEED_RATE;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_CURRENT_SPINDLE_RPM_STR) == 0)
            return GET_CURRENT_SPINDLE_RPM;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_CPUA_PASSTHROUGH_STATUS_STR) == 0)
            return GET_CPUA_PASSTHROUGH_STATUS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_ESP32_PASSTHROUGH_STATUS_STR) == 0)
            return GET_ESP32_PASSTHROUGH_STATUS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_JSON_SETTINGS_STR) == 0)
            return GET_JSON_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_FEED_RATE_OVERRIDE_STR) == 0)
            return GET_FEED_RATE_OVERRIDE;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_SPINDLE_OVERRIDE_STR) == 0)
            return GET_SPINDLE_OVERRIDE;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_ELECTRICAL_CURRENT_STR) == 0)
            return GET_ELECTRICAL_CURRENT;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_LASER_SENSOR_ENABLED_STR) == 0)
            return GET_LASER_SENSOR_ENABLED;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_DISTANCE_LASER_MM_STR) == 0)
            return GET_DISTANCE_LASER_MM;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_MACHINE_NAME_STR) == 0)
            return GET_MACHINE_NAME;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_FIRMWARE_VERSION_STR) == 0)
            return GET_FIRMWARE_VERSION;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_TOOL_LENGTH_STR) == 0)
            return GET_TOOL_LENGTH;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_TOOL_LENGTH_REFERENCES_STR) == 0)
            return GET_TOOL_LENGTH_REFERENCES;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_PROBE_HEIGHT_STR) == 0)
            return GET_PROBE_HEIGHT;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_LASER_OFFSET_ZERO_SETTINGS_STR) == 0)
            return GET_LASER_OFFSET_ZERO_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_BRUSHLESS_MOTOR_SETTINGS_STR) == 0)
            return GET_BRUSHLESS_MOTOR_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_OPENED_FILE_BOUNDS_STR) == 0)
            return GET_OPENED_FILE_BOUNDS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_HOUSING_SETTINGS_STR) == 0)
            return GET_HOUSING_SETTINGS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_MATERIAL_CENTER_POSITION_STR) == 0)
            return GET_MATERIAL_CENTER_POSITION;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_HEIGHT_MAP_STATUS_STR) == 0)
            return GET_HEIGHT_MAP_STATUS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_HEIGHT_MAP_POINTS_STR) == 0)
            return GET_HEIGHT_MAP_POINTS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_POINT_CLOUD_STATUS_STR) == 0)
            return GET_POINT_CLOUD_STATUS;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_MACHINE_USAGE_STR) == 0)
            return GET_MACHINE_USAGE;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_JOB_HISTORY_STR) == 0)
            return GET_JOB_HISTORY;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_JOB_COUNT_STR) == 0)
            return GET_JOB_COUNT;
        else if(strcmp(jsonObject[JSON_KEY_GET_TYPE_STR], JSON_VALUE_GET_SPINDLE_TYPE_STR) == 0)
            return GET_SPINDLE_TYPE;
        else
            return UNKNOWN_JSON_GET_TYPE;

    }

    return UNKNOWN_JSON_GET_TYPE;

}

JsonValueSetType JsonUtil::getJsonSetType(JsonDocument json) {

    if(!json[JSON_KEY_SET_TYPE_STR].isNull()) {

        if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_WIFI_SETTINGS_STR) == 0)
            return SET_WIFI_SETTINGS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_BASE_STR) == 0)
            return SET_DEBUGGING_BASE;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_SPINDLE_STR) == 0)
            return SET_DEBUGGING_SPINDLE;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_STATUS_STR) == 0)
            return SET_DEBUGGING_STATUS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_MACRO_BUFFER_STR) == 0)
            return SET_DEBUGGING_MACRO_BUFFER;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_SD_CARD_STR) == 0)
            return SET_DEBUGGING_SD_CARD;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_JSON_STR) == 0)
            return SET_DEBUGGING_JSON;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_ESP32_STR) == 0)
            return SET_DEBUGGING_ESP32;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_EEPROM_STR) == 0)
            return SET_DEBUGGING_EEPROM;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DEBUGGING_JOB_STR) == 0)
            return SET_DEBUGGING_JOB;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_BENCHMARKING_STR) == 0)
            return SET_BENCHMARKING;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_BENCHMARK_MINIMUM_MS_STR) == 0)
            return SET_BENCHMARK_MINIMUM_MS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JSON_SETTINGS_STR) == 0)
            return SET_JSON_SETTINGS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_MACHINE_NAME_STR) == 0)
            return SET_MACHINE_NAME;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_HOTSPOT_SETTINGS_STR) == 0)
            return SET_HOTSPOT_SETTINGS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_DRY_RUN_STR) == 0)
            return SET_DRY_RUN;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JOB_X_STR) == 0)
            return SET_JOB_X;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JOB_Y_STR) == 0)
            return SET_JOB_Y;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JOB_Z_STR) == 0)
            return SET_JOB_Z;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JOB_XYZ_STR) == 0)
            return SET_JOB_XYZ;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_JOB_A_STR) == 0)
            return SET_JOB_A;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_TOOL_LENGTH_REFERENCES_STR) == 0)
            return SET_TOOL_LENGTH_REFERENCES;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_PROBE_HEIGHT_STR) == 0)
            return SET_PROBE_HEIGHT;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_LASER_OFFSET_ZERO_SETTINGS_STR) == 0)
            return SET_LASER_OFFSET_ZERO_SETTINGS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_BRUSHLESS_MOTOR_SETTINGS_STR) == 0)
            return SET_BRUSHLESS_MOTOR_SETTINGS;
        else if(strcmp(json[JSON_KEY_SET_TYPE_STR], JSON_VALUE_SET_HOUSING_SETTINGS_STR) == 0)
            return SET_HOUSING_SETTINGS;
        else
            return UNKNOWN_JSON_SET_TYPE;


    }

    return UNKNOWN_JSON_SET_TYPE;

}