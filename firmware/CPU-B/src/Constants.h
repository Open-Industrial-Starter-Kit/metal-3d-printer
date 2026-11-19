#ifndef CONSTANTS_H
#define CONSTANTS_H

#include <cstddef>
#include <Config.h>

//Lines
#define LINE_CHUNK_SEPARATOR "|"
#define NEWLINE '\n'
#define RETURN '\r'
#define NULL_CHAR '\0'
#define TMP_FILENAME "_TMP"
#define HASH_FILENAME_SUFFIX "_hash"

//Firmware version
#define FIRMWARE_VERSION_MAX_CHARS 10
#define FIRMWARE_VERSION_CHARS (FIRMWARE_VERSION_MAX_CHARS + 1)
#define DEFAULT_FIRMWARE_VERSION "0.8.52"

//GRBL
#define GRBL_STATUS_QUERY '?'
#define GRBL_RESUME '~'
#define GRBL_HOLD '!'
#define GRBL_SAFETY_DOOR 0x84  // grblHAL realtime CMD_SAFETY_DOOR: triggers park (spindle stop + Z raise)
// Virtual safety door (our own addition to grblHAL, see CMD_VIRTUAL_DOOR_* there). The lid
// switch is wired to CPUB, so grblHAL has no door input of its own and cannot refuse a resume.
// These two bytes drive a software safety_door_ajar LEVEL in grblHAL: while "open" is asserted
// grblHAL will not start a parking restore at all. Idempotent — re-sending a state is a no-op.
#define GRBL_VIRTUAL_DOOR_OPEN   0x8D
#define GRBL_VIRTUAL_DOOR_CLOSED 0x8E
#define GRBL_UNLOCK "$X"
//const byte GRBL_CTRLX = 24;
const unsigned char GRBL_STOP_RESET = 24;

//GRBL responses
#define GRBL_MPOS "MPos"
#define GRBL_FS "FS:"
#define GRBL_OV "Ov:"
#define GRBL_WPOS "WCO"
#define GRBL_OK "ok"
#define GRBL_ERROR_PREFIX "error:"
#define GRBL_STATUS_START_STRING "<"
#define GRBL_STATUS_END_STRING ">"
#define GRBL_HOME_STATUS "Home"
#define GRBL_HOLD_STATUS "Hold"
#define GRBL_IDLE_STATUS "Idle"
#define GRBL_RUN_STATUS "Run"
#define GRBL_ALARM_STATUS "Alarm"
#define GRBL_DOOR_STATUS "Door"
#define GRBL_UNKNOWN_STATUS "Unknown"
#define GRBL_JOG_STATUS "Jog"
const unsigned char GRBL_JOG_CANCEL = 0x85;

//GRBL positions
#define GRBL_NULL_POSITION -100000.0  //this is the position that will be returned by the getMachineX/Y/ZPosition functions in case of error, so we can easily check if the parsing was successful or not

//GCode
#define M3 "M3"
#define m3 "m3"
#define M4 "M4"
#define M5 "M5"
#define m5 "m5"
#define M30 "M30"
#define m30 "m30"
#define GCODE_S "S"
//#define s "s"

//GRBL commands
#define GRBL_STATUS "?"
#define GRBL_VERSION "$I"
#define GRBL_HOMING "$H"
#define GRBL_HOMING_X "$HX"
#define GRBL_HOMING_Y "$HY"
#define GRBL_HOMING_Z "$HZ"

//GRBL-HAL real-time override bytes
#define GRBL_OVERRIDE_FEED_RESET              0x90
#define GRBL_OVERRIDE_FEED_INCREASE_COARSE    0x91
#define GRBL_OVERRIDE_FEED_DECREASE_COARSE    0x92
#define GRBL_OVERRIDE_FEED_INCREASE_FINE      0x93
#define GRBL_OVERRIDE_FEED_DECREASE_FINE      0x94
#define GRBL_OVERRIDE_SPINDLE_RESET           0x99
#define GRBL_OVERRIDE_SPINDLE_INCREASE_COARSE 0x9A
#define GRBL_OVERRIDE_SPINDLE_DECREASE_COARSE 0x9B
#define GRBL_OVERRIDE_SPINDLE_INCREASE_FINE   0x9C
#define GRBL_OVERRIDE_SPINDLE_DECREASE_FINE   0x9D
#define GRBL_OVERRIDE_SPINDLE_STOP_TOGGLE     0x9E

//GRBL-HAL override limits
#define GRBL_OVERRIDE_MIN  10
#define GRBL_OVERRIDE_MAX  200
#define GRBL_OVERRIDE_DEFAULT 100
#define GRBL_OVERRIDE_COARSE_STEP 10

//Resume spindle restore state machine stages (see handleResumeSpindleOverrideRamp)
#define RESUME_RESTORE_DROP    0  // paced override drop to GRBL_OVERRIDE_MIN while spindle is stopped
#define RESUME_RESTORE_RESTART 1  // untoggle 0x9E then resume motion ('~')
#define RESUME_RESTORE_RAMP    2  // paced override ramp up to target while running
#define RESUME_RESTORE_CONFIRM 3  // closed-loop verify grbl Ov: == target (+settle) before completing

// Resume feed (motion) ramp stages (see handleResumeFeedRamp). grblHAL ignores feed-
// override changes during HOLD, so the drop happens AFTER motion resumes.
#define RESUME_FEED_IDLE   0
#define RESUME_FEED_DROP   1  // fast paced feed-override drop to the start floor
#define RESUME_FEED_RAMPUP 2  // paced feed-override ramp back up to the captured target

#define GRBL_SET_JOB_X "G10 L20 P1 X"
#define GRBL_SET_JOB_Y "G10 L20 P1 Y"
#define GRBL_SET_JOB_Z "G10 L20 P1 Z"
#define GRBL_SET_JOB_A "G10 L20 P1 A"

//Misc
#define FROM_CPUA "CPUA: " //this is the string that will be prefixed to all the messages coming from CPUA, so we can easily identify them in the USB serial monitor
#define FROM_CPUB "CPUB: " //this is the string that will be prefixed to all the messages coming from CPUB, so we can easily identify them in the USB serial monitor

//Possible machine states, they will be used to keep track of the machine status by parsing the status reports coming from GRBL
enum MachineState {
  IDLE,
  HOME,
  HOLD,
  ALARM,
  RUN,
  JOG,
  DOOR,
  UNKNOWN
};

//Installed spindle variant. Selected at compile time via INSTALLED_SPINDLE_TYPE
//(see Config.h) and exposed read-only through the JSON API (getSpindleType).
enum SpindleType {
  SPINDLE_24V,
  SPINDLE_48V
};

#define SPINDLE_TYPE_24V_STR "24V"
#define SPINDLE_TYPE_48V_STR "48V"










#define EEPROM_START_ADDRESS 1
#define EEPROM_CHECK_CHAR 'X' //this is the character that will be written in the first byte of the EEPROM to check if the EEPROM has been initialized or not, since we need to initialize it with the default values only the first time we run the CPUB, and after that we can just read the values from the EEPROM without initializing it again

//Json

//Json enums and relative string values, they will be used to parse the incoming json commands

//API CALL JSON

//keys
enum JsonCallKey {
  TYPE,
  COMMAND_TYPE,
  GET_TYPE,
};

#define JSON_KEY_TYPE_STR "type"
#define JSON_KEY_COMMAND_TYPE_STR "commandType"
#define JSON_KEY_GET_TYPE_STR "getType"
#define JSON_KEY_SET_TYPE_STR "setType"

//other key that do not require enums
#define JSON_KEY_FILENAME_STR "filename"
#define JSON_KEY_NEW_FILENAME_STR "newFilename"
#define JSON_KEY_AP_WIFI_SSID_STR "apWifiSSID"
#define JSON_KEY_AP_WIFI_PASSWORD_STR "apWifiPassword"
#define JSON_KEY_FILE_LINES_STR "filelines"
#define JSON_KEY_FILE_DATE_STR "fileDate"
#define JSON_KEY_CRC16_STR "crc"
#define JSON_KEY_FILE_CHUNK_LINES_STR "fcl"
#define JSON_KEY_JSON_RESPONSE_STR "jsonResponse"
#define JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR "heightMapSizeXmm"
#define JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR "heightMapSizeYmm"
#define JSON_KEY_HEIGHT_MAP_DENSITY_STR "heightMapDensity"
#define JSON_KEY_HEIGHT_MAP_USE_OPENED_FILE_SIZE_STR "heightMapUseOpenedFileSize"
#define JSON_KEY_HEIGHT_MAP_POINTS_START_INDEX_STR "heightMapPointsStartIndex"
#define JSON_KEY_HEIGHT_MAP_POINTS_MAX_COUNT_STR "heightMapPointsMaxCount"
#define JSON_KEY_VALUE_STR "value"

//input source tracking
enum InputSource { INPUT_SOURCE_USB, INPUT_SOURCE_NETWORK };

//values for JSON_KEY_TYPE
enum JsonValueType {
  COMMAND,
  SET,
  GET,
  SERIAL_PING,
  WEBSOCKET_PING,
  UNKNOWN_JSON_TYPE
};

#define JSON_VALUE_COMMAND_STR "command"
#define JSON_VALUE_SET_STR "set"
#define JSON_VALUE_GET_STR "get"
#define JSON_VALUE_SERIAL_PING_STR "serialPing"
#define JSON_VALUE_WEBSOCKET_PING_STR "websocketPing"

//values for JSON_KEY_COMMAND_TYPE
enum JsonValueCommandType {
  ENABLE_CPUA_PASSTHROUGH,
  DISABLE_CPUA_PASSTHROUGH,
  MOVE_TO_ROTARY_POSITION_MACRO,
  INSTALL_ROTARY_MACRO,
  FRAME_JOB_MACRO,
  PROBE_SET_JOB_Z0_MACRO,
  PROBE_TOOL_LENGTH_MACRO,
  RETURN_JOB_XY0_MACRO,
  MOVE_TO_LASER_OFFSET_ZERO_MACRO,
  CONFIRM_MATERIAL_CENTER_AND_SET_ZERO,
  OPEN_GCODE_FILE,
  START_FILE_TRANSFER,
  ABORT_FILE_TRANSFER,
  START_FILE_DOWNLOAD,
  ABORT_FILE_DOWNLOAD,
  CLOSE_GCODE_FILE,
  DELETE_GCODE_FILE,
  RENAME_GCODE_FILE,
  CLEAR_EEPROM,
  START_JOB,
  STOP_JOB_RESET,
  GRBL_SOFT_RESET_COMMAND,
  GRBL_HOMING_COMMAND,
  GRBL_HOMING_X_COMMAND,
  GRBL_HOMING_Y_COMMAND,
  GRBL_HOMING_Z_COMMAND,
  GRBL_UNLOCK_COMMAND,
  MOVE_X_COMMAND,
  MOVE_Y_COMMAND,
  MOVE_Z_COMMAND,
  MOVE_XYZ_COMMAND,
  MOVE_MACHINE_X_COMMAND,
  MOVE_MACHINE_Y_COMMAND,
  MOVE_MACHINE_Z_COMMAND,
  MOVE_MACHINE_XYZ_COMMAND,
  MOVE_A_COMMAND,
  JOG_COMMAND,
  JOG_CANCEL_COMMAND,
  RESUME_JOB,
  PAUSE_JOB,
  ENABLE_LASER_SENSOR,
  DISABLE_LASER_SENSOR,
  SET_FEED_RATE_OVERRIDE,
  SET_SPINDLE_OVERRIDE,
  PROBE_LASER_EDGES,
  PROBE_LASER_MATERIAL_BOUNDS,
  START_HEIGHT_MAP_SCAN,
  ABORT_HEIGHT_MAP_SCAN,
  ENABLE_HEIGHT_MAP_COMPENSATION,
  DISABLE_HEIGHT_MAP_COMPENSATION,
  CLEAR_HEIGHT_MAP,
  START_POINT_CLOUD_SCAN,
  ABORT_POINT_CLOUD_SCAN,
  UNKNOWN_JSON_COMMAND_TYPE
};

#define JSON_VALUE_DELETE_GCODE_FILE_STR "deleteGcodeFile"
#define JSON_VALUE_ENABLE_CPUA_PASSTHROUGH_STR "enableCPUAPassthrough"
#define JSON_VALUE_DISABLE_CPUA_PASSTHROUGH_STR "disableCPUAPassthrough"
#define JSON_VALUE_MOVE_TO_ROTARY_POSITION_MACRO_STR "moveToRotaryPosition"
#define JSON_VALUE_INSTALL_ROTARY_MACRO_STR "installRotary"
#define JSON_VALUE_FRAME_JOB_MACRO_STR "frameJob"
#define JSON_VALUE_PROBE_SET_JOB_Z0_MACRO_STR "probeSetJobZ0Macro"
#define JSON_VALUE_PROBE_TOOL_LENGTH_MACRO_STR "probeToolLengthMacro"
#define JSON_VALUE_RETURN_JOB_XY0_MACRO_STR "returnJobXY0Macro"
#define JSON_VALUE_MOVE_TO_LASER_OFFSET_ZERO_MACRO_STR "moveToLaserOffsetZero"
#define JSON_VALUE_CONFIRM_MATERIAL_CENTER_AND_SET_ZERO_STR "confirmMaterialCenterAndSetZero"
#define JSON_VALUE_OPEN_GCODE_FILE_STR "openGcodeFile"
#define JSON_VALUE_START_FILE_TRANSFER_STR "startFileTransfer"
#define JSON_VALUE_ABORT_FILE_TRANSFER_STR "abortFileTransfer"
#define JSON_VALUE_START_FILE_DOWNLOAD_STR "startFileDownload"
#define JSON_VALUE_ABORT_FILE_DOWNLOAD_STR "abortFileDownload"
#define JSON_VALUE_CLOSE_GCODE_FILE_STR "closeGcodeFile"
#define JSON_VALUE_RENAME_GCODE_FILE_STR "renameGcodeFile"
#define JSON_VALUE_CLEAR_EEPROM_STR "clearEEPROM"
#define JSON_VALUE_START_JOB_STR "startJob"
#define JSON_VALUE_STOP_JOB_RESET_STR "stopJobReset"
#define JSON_VALUE_GRBL_SOFT_RESET_COMMAND_STR "grblSoftReset"
#define JSON_VALUE_GRBL_HOMING_COMMAND_STR "grblHoming"
#define JSON_VALUE_GRBL_HOMING_X_COMMAND_STR "grblHomingX"
#define JSON_VALUE_GRBL_HOMING_Y_COMMAND_STR "grblHomingY"
#define JSON_VALUE_GRBL_HOMING_Z_COMMAND_STR "grblHomingZ"
#define JSON_VALUE_GRBL_UNLOCK_COMMAND_STR "grblUnlock"
#define JSON_VALUE_MOVE_X_COMMAND_STR "moveX"
#define JSON_VALUE_MOVE_Y_COMMAND_STR "moveY"
#define JSON_VALUE_MOVE_Z_COMMAND_STR "moveZ"
#define JSON_VALUE_MOVE_XYZ_COMMAND_STR "moveXYZ"
#define JSON_VALUE_MOVE_MACHINE_X_COMMAND_STR "moveMachineX"
#define JSON_VALUE_MOVE_MACHINE_Y_COMMAND_STR "moveMachineY"
#define JSON_VALUE_MOVE_MACHINE_Z_COMMAND_STR "moveMachineZ"
#define JSON_VALUE_MOVE_MACHINE_XYZ_COMMAND_STR "moveMachineXYZ"
#define JSON_VALUE_MOVE_A_COMMAND_STR "moveA"
#define JSON_VALUE_JOG_COMMAND_STR "jog"
#define JSON_VALUE_JOG_CANCEL_COMMAND_STR "jogCancel"
#define JSON_KEY_MOVE_X_STR "x"
#define JSON_KEY_MOVE_RELATIVE_STR "relative"
#define JSON_KEY_MOVE_Y_STR "y"
#define JSON_KEY_MOVE_Z_STR "z"
#define JSON_KEY_MOVE_A_STR "a"
#define JSON_KEY_FEED_RATE_STR "feedRate"
#define JSON_VALUE_PAUSE_JOB_STR "pauseJob"
#define JSON_VALUE_RESUME_JOB_STR "resumeJob"
#define JSON_VALUE_ENABLE_LASER_SENSOR_STR "enableLaserSensor"
#define JSON_VALUE_DISABLE_LASER_SENSOR_STR "disableLaserSensor"
#define JSON_VALUE_SET_FEED_RATE_OVERRIDE_STR "setFeedRateOverride"
#define JSON_VALUE_SET_SPINDLE_OVERRIDE_STR "setSpindleOverride"
#define JSON_VALUE_PROBE_LASER_EDGES_STR "probeLaserEdges"
#define JSON_VALUE_PROBE_LASER_MATERIAL_BOUNDS_STR "probeLaserMaterialBounds"
#define JSON_VALUE_START_HEIGHT_MAP_SCAN_STR "startHeightMapScan"
#define JSON_VALUE_ENABLE_HEIGHT_MAP_COMPENSATION_STR "enableHeightMapCompensation"
#define JSON_VALUE_DISABLE_HEIGHT_MAP_COMPENSATION_STR "disableHeightMapCompensation"
#define JSON_VALUE_CLEAR_HEIGHT_MAP_STR "clearHeightMap"
#define JSON_VALUE_HEIGHT_MAP_DENSITY_COARSE_STR "coarse"
#define JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR "fine"
#define JSON_VALUE_EVENT_LASER_EDGE_PROBING_COMPLETED_STR "laserEdgeProbingCompleted"
#define JSON_VALUE_EVENT_LASER_MATERIAL_BOUNDS_COMPLETED_STR "laserMaterialBoundsCompleted"
#define JSON_VALUE_EVENT_HEIGHT_MAP_SCAN_PROGRESS_STR "heightMapScanProgress"
#define JSON_VALUE_EVENT_HEIGHT_MAP_SCAN_COMPLETED_STR "heightMapScanCompleted"
#define JSON_VALUE_START_POINT_CLOUD_SCAN_STR "startPointCloudScan"
#define JSON_VALUE_ABORT_POINT_CLOUD_SCAN_STR "abortPointCloudScan"
#define JSON_VALUE_ABORT_HEIGHT_MAP_SCAN_STR "abortHeightMapScan"
#define JSON_VALUE_POINT_CLOUD_DENSITY_ULTRAFINE_STR "ultrafine"
#define JSON_VALUE_POINT_CLOUD_DENSITY_FINE_STR "fine"
#define JSON_VALUE_POINT_CLOUD_DENSITY_MEDIUM_STR "medium"
#define JSON_VALUE_POINT_CLOUD_DENSITY_COARSE_STR "coarse"
#define JSON_VALUE_EVENT_POINT_CLOUD_SCAN_PROGRESS_STR "pointCloudScanProgress"
#define JSON_VALUE_EVENT_POINT_CLOUD_SCAN_COMPLETED_STR "pointCloudScanCompleted"
#define JSON_KEY_LASER_EDGE_X_MIN_STR "xMin"
#define JSON_KEY_LASER_EDGE_X_MAX_STR "xMax"
#define JSON_KEY_LASER_EDGE_Y_MIN_STR "yMin"
#define JSON_KEY_LASER_EDGE_Y_MAX_STR "yMax"
#define JSON_KEY_MATERIAL_CENTER_X_STR "materialCenterX"
#define JSON_KEY_MATERIAL_CENTER_Y_STR "materialCenterY"
#define JSON_KEY_MATERIAL_CENTER_VALID_STR "materialCenterValid"
// XY0 corner detection (probeLaserEdges): which corner to detect, plus an
// optional offset INTO the material applied to the origin on each axis.
#define JSON_KEY_XY0_CORNER_STR "corner"
#define JSON_KEY_XY0_X_OFFSET_STR "xOffset"
#define JSON_KEY_XY0_Y_OFFSET_STR "yOffset"
#define JSON_VALUE_XY0_CORNER_BOTTOM_LEFT_STR "bottomLeft"
#define JSON_VALUE_XY0_CORNER_BOTTOM_RIGHT_STR "bottomRight"
#define JSON_VALUE_XY0_CORNER_TOP_LEFT_STR "topLeft"
#define JSON_VALUE_XY0_CORNER_TOP_RIGHT_STR "topRight"

//override action values
#define JSON_VALUE_OVERRIDE_ACTION_KEY_STR "action"
#define JSON_VALUE_OVERRIDE_ACTION_RESET_STR "reset"
#define JSON_VALUE_OVERRIDE_ACTION_INCREASE_COARSE_STR "increaseCoarse"
#define JSON_VALUE_OVERRIDE_ACTION_DECREASE_COARSE_STR "decreaseCoarse"
#define JSON_VALUE_OVERRIDE_ACTION_INCREASE_FINE_STR "increaseFine"
#define JSON_VALUE_OVERRIDE_ACTION_DECREASE_FINE_STR "decreaseFine"
#define JSON_VALUE_SET_JOB_X_STR "setJobX"
#define JSON_VALUE_SET_JOB_Y_STR "setJobY"
#define JSON_VALUE_SET_JOB_Z_STR "setJobZ"
#define JSON_VALUE_SET_JOB_XYZ_STR "setJobXYZ"
#define JSON_VALUE_SET_JOB_A_STR "setJobA"

//values for JSON_KEY_GET_TYPE
enum JsonValueGetType {
  STATUS,
  POSITIONS,
  MACHINE_POSITION_X,
  MACHINE_POSITION_Y,
  MACHINE_POSITION_Z,
  MACHINE_POSITION_A,
  MACHINE_POSITION_XYZ,
  JOB_POSITION_X,
  JOB_POSITION_Y,
  JOB_POSITION_Z,
  JOB_POSITION_A,
  JOB_POSITION_XYZ,
  GET_DEVICE_ID,
  FILE_LIST,
  GCODE_FILE_HASH,
  OPENED_GCODE_FILE_NAME,
  JOB_PROGRESS,
  OPENED_GCODE_FILE_TOTAL_LINES,
  NEXT_FILE_DOWNLOAD_CHUNK,
  GET_WIFI_SETTINGS,
  GET_DEBUGGING_SETTINGS,
  GET_CURRENT_FEED_RATE,
  GET_CURRENT_SPINDLE_RPM,
  GET_CPUA_PASSTHROUGH_STATUS,
  GET_JSON_SETTINGS,
  GET_FEED_RATE_OVERRIDE,
  GET_SPINDLE_OVERRIDE,
  GET_ELECTRICAL_CURRENT,
  GET_LASER_SENSOR_ENABLED,
  GET_DISTANCE_LASER_MM,
  GET_MACHINE_NAME,
  GET_FIRMWARE_VERSION,
  GET_TOOL_LENGTH,
  GET_TOOL_LENGTH_REFERENCES,
  GET_PROBE_HEIGHT,
  GET_LASER_OFFSET_ZERO_SETTINGS,
  GET_BRUSHLESS_MOTOR_SETTINGS,
  GET_OPENED_FILE_BOUNDS,
  GET_HOUSING_SETTINGS,
  GET_MATERIAL_CENTER_POSITION,
  GET_HEIGHT_MAP_STATUS,
  GET_HEIGHT_MAP_POINTS,
  GET_POINT_CLOUD_STATUS,
  GET_MACHINE_USAGE,
  GET_JOB_HISTORY,
  GET_JOB_COUNT,
  GET_SPINDLE_TYPE,
  UNKNOWN_JSON_GET_TYPE
};

#define JSON_VALUE_STATUS_STR "status"
#define JSON_VALUE_POSITIONS_STR "positions"
#define JSON_VALUE_MACHINE_POSITION_X_STR "machinePositionX"
#define JSON_VALUE_MACHINE_POSITION_Y_STR "machinePositionY"
#define JSON_VALUE_MACHINE_POSITION_Z_STR "machinePositionZ"
#define JSON_VALUE_MACHINE_POSITION_A_STR "machinePositionA"
#define JSON_VALUE_JOB_POSITION_X_STR "jobX"
#define JSON_VALUE_JOB_POSITION_Y_STR "jobY"
#define JSON_VALUE_JOB_POSITION_Z_STR "jobZ"
#define JSON_VALUE_JOB_POSITION_A_STR "jobA"
#define JSON_VALUE_JOB_POSITION_XYZ_STR "jobXYZ"
#define JSON_VALUE_MACHINE_POSITION_XYZ_STR "machinePositionXYZ"
#define JSON_VALUE_GET_DEVICE_ID_STR "deviceId"
#define JSON_VALUE_FILE_LIST_STR "fileList"
#define JSON_VALUE_GCODE_FILE_HASH_STR "gcodeFileHash"
#define JSON_VALUE_OPENED_GCODE_FILE_NAME_STR "openedGcodeFileName"
#define JSON_VALUE_JOB_PROGRESS_STR "jobProgress"
#define JSON_VALUE_OPENED_GCODE_FILE_TOTAL_LINES_STR "openedGcodeFileTotalLines"
#define JSON_VALUE_NEXT_FILE_DOWNLOAD_CHUNK_STR "nextFileDownloadChunk"
#define JSON_VALUE_GET_WIFI_SETTINGS_STR "getWifiSettings"
#define JSON_VALUE_GET_DEBUGGING_SETTINGS_STR "getDebuggingSettings"
#define JSON_VALUE_GET_CURRENT_FEED_RATE_STR "currentFeedRate"
#define JSON_VALUE_GET_CURRENT_SPINDLE_RPM_STR "currentSpindleRPM"
#define JSON_VALUE_GET_CPUA_PASSTHROUGH_STATUS_STR "getCpuaPassthroughStatus"
#define JSON_VALUE_GET_JSON_SETTINGS_STR "getJsonSettings"
#define JSON_VALUE_GET_FEED_RATE_OVERRIDE_STR "getFeedRateOverride"
#define JSON_VALUE_GET_SPINDLE_OVERRIDE_STR "getSpindleOverride"
#define JSON_VALUE_GET_ELECTRICAL_CURRENT_STR "electricalCurrent"
#define JSON_VALUE_GET_LASER_SENSOR_ENABLED_STR "laserSensorEnabled"
#define JSON_VALUE_GET_DISTANCE_LASER_MM_STR "distanceLaserMm"
#define JSON_VALUE_GET_MACHINE_NAME_STR "machineName"
#define JSON_VALUE_GET_FIRMWARE_VERSION_STR "firmwareVersion"
#define JSON_VALUE_GET_TOOL_LENGTH_STR "getToolLength"
#define JSON_VALUE_GET_TOOL_LENGTH_REFERENCES_STR "getToolLengthReferences"
#define JSON_VALUE_GET_PROBE_HEIGHT_STR "getProbeHeight"
#define JSON_VALUE_GET_LASER_OFFSET_ZERO_SETTINGS_STR "getLaserOffsetZeroSettings"
#define JSON_VALUE_GET_BRUSHLESS_MOTOR_SETTINGS_STR "getBrushlessMotorSettings"
#define JSON_VALUE_GET_OPENED_FILE_BOUNDS_STR "getOpenedFileBounds"
#define JSON_VALUE_GET_HOUSING_SETTINGS_STR "getHousingSettings"
#define JSON_VALUE_GET_MATERIAL_CENTER_POSITION_STR "getMaterialCenterPosition"
#define JSON_VALUE_GET_HEIGHT_MAP_STATUS_STR "getHeightMapStatus"
#define JSON_VALUE_GET_HEIGHT_MAP_POINTS_STR "getHeightMapPoints"
#define JSON_VALUE_GET_MACHINE_USAGE_STR "getMachineUsage"
#define JSON_VALUE_GET_JOB_HISTORY_STR "getJobHistory"
#define JSON_VALUE_GET_JOB_COUNT_STR "getJobCount"
#define JSON_VALUE_GET_SPINDLE_TYPE_STR "getSpindleType"

//values for JSON_KEY_SET_TYPE
enum JsonValueSetType {
  SET_WIFI_SETTINGS,
  SET_DEBUGGING_BASE,
  SET_DEBUGGING_SPINDLE,
  SET_DEBUGGING_STATUS,
  SET_DEBUGGING_MACRO_BUFFER,
  SET_DEBUGGING_SD_CARD,
  SET_DEBUGGING_JSON,
  SET_DEBUGGING_NETWORK,
  SET_DEBUGGING_EEPROM,
  SET_DEBUGGING_JOB,
  SET_BENCHMARKING,
  SET_BENCHMARK_MINIMUM_MS,
  SET_JSON_SETTINGS,
  SET_MACHINE_NAME,
  SET_HOTSPOT_SETTINGS,
  SET_DRY_RUN,
  SET_JOB_X,
  SET_JOB_Y,
  SET_JOB_Z,
  SET_JOB_XYZ,
  SET_JOB_A,
  SET_TOOL_LENGTH_REFERENCES,
  SET_PROBE_HEIGHT,
  SET_LASER_OFFSET_ZERO_SETTINGS,
  SET_BRUSHLESS_MOTOR_SETTINGS,
  SET_HOUSING_SETTINGS,
  UNKNOWN_JSON_SET_TYPE
};

#define JSON_VALUE_SET_WIFI_SETTINGS_STR "setWifiSettings"
#define JSON_VALUE_SET_DEBUGGING_BASE_STR "setDebuggingBase"
#define JSON_VALUE_SET_DEBUGGING_SPINDLE_STR "setDebuggingSpindle"
#define JSON_VALUE_SET_DEBUGGING_STATUS_STR "setDebuggingStatus"
#define JSON_VALUE_SET_DEBUGGING_MACRO_BUFFER_STR "setDebuggingMacroBuffer"
#define JSON_VALUE_SET_DEBUGGING_SD_CARD_STR "setDebuggingSDCard"
#define JSON_VALUE_SET_DEBUGGING_JSON_STR "setDebuggingJson"
#define JSON_VALUE_SET_DEBUGGING_NETWORK_STR "setDebuggingNetwork"
#define JSON_VALUE_SET_DEBUGGING_EEPROM_STR "setDebuggingEEPROM"
#define JSON_VALUE_SET_DEBUGGING_JOB_STR "setDebuggingJob"
#define JSON_VALUE_SET_BENCHMARKING_STR "setBenchmarking"
#define JSON_VALUE_SET_BENCHMARK_MINIMUM_MS_STR "setBenchmarkMinimumMs"
#define JSON_VALUE_SET_JSON_SETTINGS_STR "setJsonSettings"
#define JSON_VALUE_SET_MACHINE_NAME_STR "setMachineName"
#define JSON_VALUE_SET_HOTSPOT_SETTINGS_STR "setHotspotSettings"
#define JSON_VALUE_SET_DRY_RUN_STR "setDryRun"
#define JSON_VALUE_SET_TOOL_LENGTH_REFERENCES_STR "setToolLengthReferences"
#define JSON_VALUE_SET_PROBE_HEIGHT_STR "setProbeHeight"
#define JSON_VALUE_SET_LASER_OFFSET_ZERO_SETTINGS_STR "setLaserOffsetZeroSettings"
#define JSON_VALUE_SET_BRUSHLESS_MOTOR_SETTINGS_STR "setBrushlessMotorSettings"
#define JSON_VALUE_SET_HOUSING_SETTINGS_STR "setHousingSettings"


//API RESPONSE JSON
#define JSON_KEY_RESULT_STR "result"
#define JSON_KEY_ERROR_STR "error"
#define JSON_KEY_MACHINE_STATUS_STR "machineStatus"
#define JSON_KEY_MACHINE_POSITIONX_STR "machinePositionX"
#define JSON_KEY_MACHINE_POSITIONY_STR "machinePositionY"
#define JSON_KEY_MACHINE_POSITIONZ_STR "machinePositionZ"
#define JSON_KEY_MACHINE_POSITIONA_STR "machinePositionA"
#define JSON_KEY_JOB_POSITIONX_STR "jobPositionX"
#define JSON_KEY_JOB_POSITIONY_STR "jobPositionY"
#define JSON_KEY_JOB_POSITIONZ_STR "jobPositionZ"
#define JSON_KEY_JOB_POSITIONA_STR "jobPositionA"
#define JSON_KEY_DEVICE_ID_STR "deviceId"
#define JSON_KEY_OPENED_FILE_NAME_STR "openedFileName"
#define JSON_KEY_FILE_OPENED_STR "fileOpened"
#define JSON_KEY_JOB_RUNNING_STR "jobRunning"
#define JSON_KEY_DRY_RUN_ACTIVE_STR "dryRunActive"
#define JSON_KEY_DRY_RUN_ENABLED_STR "dryRunEnabled"
#define JSON_KEY_PROCEDURE_BUSY_STR "procedureBusy"
#define JSON_KEY_JOB_PAUSED_STR "jobPaused"
#define JSON_KEY_JOB_RESUMING_STR "jobResuming"
#define JSON_KEY_FILE_TRANSFER_IN_PROGRESS_STR "fileTransferInProgress"
#define JSON_KEY_FILE_DOWNLOAD_IN_PROGRESS_STR "fileDownloadInProgress"
#define JSON_KEY_PROBE_TRIGGERED_STR "probeTriggered"
#define JSON_KEY_CURRENT_FILE_LINE_STR "currentFileLine"
#define JSON_KEY_JOB_PROGRESS_STR "jobProgress"
#define JSON_KEY_ESTIMATED_REMAINING_SECONDS_STR "estimatedRemainingSeconds"
#define JSON_KEY_TOTAL_FILE_LINES_STR "totalFileLines"
#define JSON_KEY_DOWNLOADED_BYTES_STR "downloadedBytes"
#define JSON_KEY_LINES_REMAINING_STR "linesRemaining"
#define JSON_KEY_EOF_STR "eof"
#define JSON_KEY_CURRENT_FEED_RATE_STR "currentFeedRate"
#define JSON_KEY_CURRENT_SPINDLE_RPM_STR "currentSpindleRPM"
#define JSON_KEY_FEED_RATE_OVERRIDE_STR "feedRateOverride"
#define JSON_KEY_SPINDLE_OVERRIDE_STR "spindleOverride"
#define JSON_KEY_MINIMUM_SPINDLE_RPM_STR "minimumSpindleRPM"
#define JSON_KEY_ELECTRICAL_CURRENT_MA_STR "electricalCurrentmA"
#define JSON_KEY_LASER_SENSOR_ENABLED_STR "laserSensorEnabled"
#define JSON_KEY_DISTANCE_LASER_MM_STR "distanceLaserMm"
#define JSON_KEY_DISTANCE_LASER_SENSOR_CONNECTED_STR "distanceLaserSensorConnected"
#define JSON_KEY_SD_CARD_CONNECTED_STR "sdCardConnected"
#define JSON_KEY_ROTARY_AXIS_CONNECTED_STR "rotaryAxisConnected"
#define JSON_KEY_HOMED_SINCE_POWER_ON_STR "homedSincePowerOn"
#define JSON_KEY_FILE_LIST_STR "fileList"
#define JSON_KEY_FILE_NAME_STR "fileName"
#define JSON_KEY_FILE_SIZE_STR "fileSize"
#define JSON_KEY_FILE_DEPTH_STR "fileDepth"
#define JSON_KEY_FILE_WIDTH_STR "fileWidth"
#define JSON_KEY_FILE_HEIGHT_STR "fileHeight"
// Work-area limits, sent alongside fileWidth/fileDepth/fileHeight when an upload is
// rejected for size, so the client can point at the offending axis.
#define JSON_KEY_MAX_FILE_DEPTH_STR "maxFileDepth"
#define JSON_KEY_MAX_FILE_WIDTH_STR "maxFileWidth"
#define JSON_KEY_MAX_FILE_HEIGHT_STR "maxFileHeight"
#define JSON_KEY_FILE_SHA256_STR "fileSha256"
#define JSON_KEY_ESTIMATED_DURATION_SECONDS_STR "estimatedDurationSeconds"
#define JSON_RESPONSE_KEY_AP_WIFI_SSID_STR "apWifiSSID"
#define JSON_RESPONSE_KEY_AP_WIFI_PASSWORD_STR "apWifiPassword"
#define JSON_KEY_CLIENT_WIFI_SSID_STR "clientWifiSSID"
#define JSON_KEY_CLIENT_WIFI_PASSWORD_STR "clientWifiPassword"
#define JSON_KEY_HOTSPOT_ENABLED_STR "hotspotEnabled"
#define JSON_KEY_MACHINE_NAME_STR "machineName"
#define JSON_KEY_FIRMWARE_VERSION_STR "firmwareVersion"
#define JSON_KEY_TOOL_LENGTH_STR "toolLength"
#define JSON_KEY_TOOL_LENGTH_REFERENCE_Z_STR "toolLengthReferenceZ"
#define JSON_KEY_PROBE_HEIGHT_STR "probeHeight"
#define JSON_KEY_LASER_OFFSET_ZERO_X_BACK_MM_STR "laserOffsetZeroXBackMm"
#define JSON_KEY_LASER_OFFSET_ZERO_Y_BACK_MM_STR "laserOffsetZeroYBackMm"
// Level driven on the brushless accessory's direction pin: false = LOW, true = HIGH. Named by level
// rather than by rotation, since which way the motor turns depends on the accessory's wiring.
#define JSON_KEY_BRUSHLESS_MOTOR_DIRECTION_REVERSED_STR "brushlessMotorDirectionReversed"
#define JSON_KEY_HOUSING_INSTALLED_STR "housingInstalled"
#define JSON_KEY_ALLOW_MOTION_WHEN_DOOR_OPEN_STR "allowMotionWhenDoorOpen"
#define JSON_KEY_DOOR_OPEN_STR "doorOpen"
#define JSON_KEY_SPINDLE_TYPE_STR "spindleType"
#define JSON_KEY_POWER_ON_HOURS_STR "powerOnHours"
#define JSON_KEY_RUN_HOURS_STR "runHours"
#define JSON_KEY_SPINDLE_HOURS_STR "spindleHours"
#define JSON_KEY_JOB_COUNT_STR "jobCount"
#define JSON_KEY_USAGE_DATA_VALID_STR "usageDataValid"
#define JSON_KEY_START_TIME_STR "startTime"
#define JSON_KEY_JOB_HISTORY_STR "jobHistory"
#define JSON_KEY_RANGE_START_STR "rangeStart"
#define JSON_KEY_RANGE_END_STR "rangeEnd"
#define JSON_KEY_OFFSET_STR "offset"
#define JSON_KEY_RETURNED_STR "returned"
#define JSON_KEY_HAS_MORE_STR "hasMore"
#define JSON_KEY_HEIGHT_MAP_CONFIGURED_STR "heightMapConfigured"
#define JSON_KEY_HEIGHT_MAP_SCAN_IN_PROGRESS_STR "heightMapScanInProgress"
#define JSON_KEY_HEIGHT_MAP_READY_STR "heightMapReady"
#define JSON_KEY_HEIGHT_MAP_COMPENSATION_ENABLED_STR "heightMapCompensationEnabled"
#define JSON_KEY_HEIGHT_MAP_BOUND_FILE_STR "heightMapBoundFile"
#define JSON_KEY_HEIGHT_MAP_GRID_POINTS_X_STR "heightMapGridPointsX"
#define JSON_KEY_HEIGHT_MAP_GRID_POINTS_Y_STR "heightMapGridPointsY"
#define JSON_KEY_HEIGHT_MAP_POINTS_SCANNED_STR "heightMapPointsScanned"
#define JSON_KEY_HEIGHT_MAP_ACTIVE_FOR_OPENED_FILE_STR "heightMapActiveForOpenedFile"
#define JSON_KEY_HEIGHT_MAP_POINTS_TOTAL_STR "heightMapPointsTotal"
#define JSON_KEY_HEIGHT_MAP_POINTS_RETURNED_STR "heightMapPointsReturned"
#define JSON_KEY_HEIGHT_MAP_POINTS_STR "heightMapPoints"
#define JSON_KEY_HEIGHT_MAP_POINT_COORDINATES_STR "heightMapPointCoordinates"
#define JSON_KEY_HEIGHT_MAP_COORDINATE_SPACE_STR "heightMapCoordinateSpace"
#define JSON_KEY_HEIGHT_MAP_ABORT_STAGE_STR "heightMapAbortStage"
#define JSON_KEY_HEIGHT_MAP_ABORT_DETAIL_STR "heightMapAbortDetail"
// Line scan acquisition (see handleHeightMapScan). Progress is reported per ROW now that a whole row
// is one continuous move; the point counters are still filled in (cells resolved so far) so existing
// progress bars keep working.
#define JSON_KEY_HEIGHT_MAP_LINE_SCAN_STR "heightMapLineScan"
#define JSON_KEY_HEIGHT_MAP_ROWS_SCANNED_STR "heightMapRowsScanned"
#define JSON_KEY_HEIGHT_MAP_ROWS_TOTAL_STR "heightMapRowsTotal"
#define JSON_KEY_HEIGHT_MAP_SAMPLES_TAKEN_STR "heightMapSamplesTaken"
#define JSON_KEY_HEIGHT_MAP_SPACING_MM_STR "heightMapSpacingMm"
// Where the map's (0,0) cell sits, in job coordinates. Lets the frontend check the map is anchored
// over the toolpath it is meant to compensate, rather than assuming it.
#define JSON_KEY_HEIGHT_MAP_ORIGIN_X_MM_STR "heightMapOriginXmm"
#define JSON_KEY_HEIGHT_MAP_ORIGIN_Y_MM_STR "heightMapOriginYmm"
// Scan quality figures, so the frontend can warn instead of showing a smooth-looking but fabricated
// map: cells that had to be filled from neighbours, the worst per-cell sample count, and the widest
// trimmed spread seen in any cell (a direct read on how noisy the sensor was on this material).
#define JSON_KEY_HEIGHT_MAP_CELLS_INTERPOLATED_STR "heightMapCellsInterpolated"
#define JSON_KEY_HEIGHT_MAP_MIN_CELL_SAMPLES_STR "heightMapMinCellSamples"
#define JSON_KEY_HEIGHT_MAP_MAX_CELL_SPREAD_MM_STR "heightMapMaxCellSpreadMm"
// Where the worst cell is, in mm from the scan start corner (same frame as getHeightMapPoints), and
// how many cells were noisy at all. Position tells you whether the trouble is at the edge of the scan
// or in the middle of the work; the count tells you whether it is one spot or the whole surface.
#define JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_X_MM_STR "heightMapNoisiestCellXmm"
#define JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_Y_MM_STR "heightMapNoisiestCellYmm"
#define JSON_KEY_HEIGHT_MAP_NOISY_CELLS_STR "heightMapNoisyCells"
// Quality of the Z datum itself: how many of the stationary reads at job zero were valid, and how
// far apart the ones that survived trimming were. A wide baseline spread means the reference every
// map value is measured against is itself unreliable.
#define JSON_KEY_HEIGHT_MAP_BASELINE_SAMPLES_STR "heightMapBaselineSamples"
#define JSON_KEY_HEIGHT_MAP_BASELINE_SPREAD_MM_STR "heightMapBaselineSpreadMm"
// True when the Z datum came from the swept cells at job (0,0) (the accurate path), false when it fell
// back to the stationary reading because job zero lies outside the scanned rectangle. The fallback is
// measured differently from the cells, so a constant depth offset can reappear with it.
#define JSON_KEY_HEIGHT_MAP_DATUM_FROM_CELLS_STR "heightMapDatumFromCells"
// Cell-derived datum minus the parked reading at the same point: how differently the sensor reads
// while sweeping versus standing still. It is cancelled out of the map (both sides of the subtraction
// now come from the cells), so this is a diagnostic, not an error -- but a large value means the
// sweep is not measuring the same thing a parked reading would.
#define JSON_KEY_HEIGHT_MAP_DATUM_BIAS_MM_STR "heightMapDatumBiasMm"
// The toolpath's own extent, in job coordinates, from the opened file's MIN/MAX metadata. Unlike the
// SIZEX/SIZEY figures these say WHERE the job sits, not just how big it is -- which is what makes a
// coverage answer possible rather than a size comparison.
#define JSON_KEY_FILE_MIN_X_MM_STR "fileMinXmm"
#define JSON_KEY_FILE_MAX_X_MM_STR "fileMaxXmm"
#define JSON_KEY_FILE_MIN_Y_MM_STR "fileMinYmm"
#define JSON_KEY_FILE_MAX_Y_MM_STR "fileMaxYmm"
// Whether the current map's rectangle contains the opened file's toolpath. OMITTED when the answer
// cannot be determined -- no map, no file open, or a file written before MIN/MAX metadata existed --
// so an absent key means "unknown", never "does not cover".
#define JSON_KEY_HEIGHT_MAP_COVERS_OPENED_JOB_STR "heightMapCoversOpenedJob"
// Extreme raw sensor readings seen during the scan. The sensor spans about +/-30 mm, so these show
// how much headroom the scan actually had: values crowding either limit mean part of the map was
// measured at the edge of the sensor's range and cannot be trusted.
#define JSON_KEY_HEIGHT_MAP_MIN_READING_MM_STR "heightMapMinReadingMm"
#define JSON_KEY_HEIGHT_MAP_MAX_READING_MM_STR "heightMapMaxReadingMm"

#define JSON_VALUE_COORDINATE_SPACE_MACHINE_STR "machine"
#define JSON_VALUE_COORDINATE_SPACE_RELATIVE_STR "relative"
#define JSON_VALUE_COORDINATE_SPACE_WORK_STR "work"
#define JSON_KEY_JSON_ENABLE_JSON_RESPONSE_STR "enableJsonResponse"

#define JSON_KEY_DEBUGGING_BASE_STR "debuggingBase"
#define JSON_KEY_DEBUGGING_SPINDLE_STR "debuggingSpindle"
#define JSON_KEY_DEBUGGING_STATUS_STR "debuggingStatus"
#define JSON_KEY_DEBUGGING_MACRO_BUFFER_STR "debuggingMacroBuffer"
#define JSON_KEY_DEBUGGING_SD_CARD_STR "debuggingSDCard"
#define JSON_KEY_DEBUGGING_JSON_STR "debuggingJson"
#define JSON_KEY_DEBUGGING_NETWORK_STR "debuggingNetwork"
#define JSON_KEY_DEBUGGING_EEPROM_STR "debuggingEEPROM"
#define JSON_KEY_DEBUGGING_JOB_STR "debuggingJob"
#define JSON_KEY_BENCHMARKING_STR "benchmarking"
#define JSON_KEY_BENCHMARK_MINIMUM_MS_STR "benchmarkMinimumMs"
#define JSON_KEY_CPUA_PASSTHROUGH_STATUS_STR "cpuaPassthroughStatus"

#define JSON_KEY_JSON_ENABLE_JSON_RESPONSE_STR "enableJsonResponse"

#define JSON_VALUE_FILE_WRITTEN_STR "fileWritten"


#define JSON_VALUE_RESULT_SUCCESS_STR "success"
#define JSON_VALUE_RESULT_FAILURE_STR "failure"
#define JSON_VALUE_EVENT_HOMING_COMPLETED_STR "homingCompleted"
#define JSON_VALUE_EVENT_RESUME_COMPLETED_STR "resumeCompleted"
#define JSON_VALUE_EVENT_PROBE_Z0_COMPLETED_STR "probeZ0Completed"
#define JSON_VALUE_EVENT_PROBE_TOOL_LENGTH_COMPLETED_STR "probeToolLengthCompleted"
#define JSON_VALUE_RESULT_MACHINE_STATUS_STR "machineStatus"
#define JSON_VALUE_ERROR_FILE_NOT_FOUND_STR "fileNotFound"
#define JSON_VALUE_ERROR_FILE_HASH_NOT_FOUND_STR "fileHashNotFound"
#define JSON_VALUE_ERROR_UNKNOWN_JSON_TYPE_STR "unknownJsonType"
#define JSON_VALUE_ERROR_UNKNOWN_JSON_COMMAND_TYPE_STR "unknownJsonCommandType"
#define JSON_VALUE_ERROR_WIFI_SETTINGS_NOT_FOUND_STR "wifiSettingsNotFound"
#define JSON_VALUE_ERROR_MISSING_JSON_KEY_STR "missingJsonKey"
#define JSON_VALUE_ERROR_FILE_CREATION_FAILED_STR "fileCreationFailed"
#define JSON_VALUE_ERROR_FILE_RENAME_FAILED_STR "fileRenameFailed"
#define JSON_VALUE_ERROR_FILE_DIMENSIONS_EXCEEDED_STR "fileDimensionsExceeded"
// Upload failure causes. Reported in the failure response of the last file chunk so the
// client can tell a rejected job (dimensions) from an SD/IO problem, instead of every
// cause arriving as the same generic error.
#define JSON_VALUE_ERROR_FILE_TRANSFER_NOT_STARTED_STR "fileTransferNotStarted"
#define JSON_VALUE_ERROR_FILE_READ_FAILED_STR "fileReadFailed"
#define JSON_VALUE_ERROR_FILE_WRITE_FAILED_STR "fileWriteFailed"
#define JSON_VALUE_ERROR_FILE_HASH_FAILED_STR "fileHashFailed"
#define JSON_VALUE_ERROR_FILE_FINALIZE_FAILED_STR "fileFinalizeFailed"
#define JSON_VALUE_ERROR_FILE_ALREADY_EXISTS_STR "fileAlreadyExists"
#define JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR "jobAlreadyRunning"
#define JSON_VALUE_ERROR_JOB_RUNNING_STR "jobRunning"
#define JSON_VALUE_ERROR_NO_FILE_OPENED_STR "noFileOpened"
#define JSON_VALUE_ERROR_FILE_BOUNDS_UNAVAILABLE_STR "fileBoundsUnavailable"
#define JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR "invalidJsonValue"
#define JSON_VALUE_ERROR_NO_JOB_RUNNING_STR "noJobRunning"
#define JSON_VALUE_ERROR_JOB_ALREADY_PAUSED_STR "jobAlreadyPaused"
#define JSON_VALUE_ERROR_JOB_NOT_PAUSED_STR "jobNotPaused"
#define JSON_VALUE_ERROR_FILE_TRANSFER_IN_PROGRESS_STR "fileTransferInProgress"
#define JSON_VALUE_ERROR_FILE_TRANSFER_TIMEOUT_STR "fileTransferTimeout"
#define JSON_VALUE_ERROR_FILE_DOWNLOAD_IN_PROGRESS_STR "fileDownloadInProgress"
#define JSON_VALUE_ERROR_NO_FILE_DOWNLOAD_IN_PROGRESS_STR "noFileDownloadInProgress"
#define JSON_VALUE_ERROR_SOFT_LIMIT_EXCEEDED_STR "softLimitExceeded"
#define JSON_VALUE_ERROR_SPINDLE_RPM_BELOW_MINIMUM_STR "spindleRpmBelowMinimum"
#define JSON_VALUE_ERROR_NOT_HOMED_STR "notHomed"
#define JSON_VALUE_ERROR_DOOR_OPEN_STR "doorOpen"
#define JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR "rotaryYLock"
#define JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR "macroAlreadyRunning"
#define JSON_VALUE_ERROR_MACRO_RUNNING_STR "macroRunning"
#define JSON_VALUE_ERROR_MACRO_BUFFER_FULL_STR "macroBufferFull"
#define JSON_VALUE_ERROR_CURRENT_SENSOR_NOT_INITIALIZED_STR "currentSensorNotInitialized"
#define JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR "distanceLaserSensorNotInitialized"
#define JSON_VALUE_ERROR_DISTANCE_LASER_READ_FAILED_STR "distanceLaserReadFailed"
#define JSON_VALUE_ERROR_DISTANCE_LASER_OUT_OF_RANGE_STR "distanceLaserOutOfRange"
#define JSON_VALUE_ERROR_MATERIAL_CENTER_NOT_AVAILABLE_STR "materialCenterNotAvailable"
#define JSON_VALUE_ERROR_HEIGHT_MAP_RESCAN_REQUIRED_STR "heightMapRescanRequired"
#define JSON_VALUE_ERROR_HEIGHT_MAP_NOT_READY_STR "heightMapNotReady"
// startJob refused: the map does not cover the opened file's toolpath, so part of the job would be
// compensated with clamped edge values instead of measured ones.
#define JSON_VALUE_ERROR_HEIGHT_MAP_DOES_NOT_COVER_JOB_STR "heightMapDoesNotCoverJob"
#define JSON_VALUE_ERROR_POINT_CLOUD_SCAN_IN_PROGRESS_STR "pointCloudScanInProgress"
#define JSON_VALUE_ERROR_NO_POINT_CLOUD_SCAN_IN_PROGRESS_STR "noPointCloudScanInProgress"
#define JSON_VALUE_ERROR_NO_HEIGHT_MAP_SCAN_IN_PROGRESS_STR "noHeightMapScanInProgress"
#define JSON_VALUE_GET_POINT_CLOUD_STATUS_STR "getPointCloudStatus"
#define JSON_KEY_POINT_CLOUD_SCAN_IN_PROGRESS_STR "pointCloudScanInProgress"
#define JSON_KEY_POINT_CLOUD_READY_STR "pointCloudReady"
#define JSON_KEY_POINT_CLOUD_FILENAME_STR "pointCloudFilename"
#define JSON_KEY_POINT_CLOUD_GRID_POINTS_X_STR "pointCloudGridPointsX"
#define JSON_KEY_POINT_CLOUD_GRID_POINTS_Y_STR "pointCloudGridPointsY"
#define JSON_KEY_POINT_CLOUD_CURRENT_ROW_STR "pointCloudCurrentRow"
#define JSON_KEY_POINT_CLOUD_POINTS_SCANNED_STR "pointCloudPointsScanned"
#define JSON_KEY_POINT_CLOUD_POINTS_TOTAL_STR "pointCloudPointsTotal"
#define JSON_KEY_POINT_CLOUD_DENSITY_STR "pointCloudDensity"
#define JSON_KEY_POINT_CLOUD_SAVED_STR "pointCloudSaved"
#define JSON_KEY_POINT_CLOUD_SIZE_X_MM_STR "pointCloudSizeXmm"
#define JSON_KEY_POINT_CLOUD_SIZE_Y_MM_STR "pointCloudSizeYmm"

#endif // CONSTANTS_H