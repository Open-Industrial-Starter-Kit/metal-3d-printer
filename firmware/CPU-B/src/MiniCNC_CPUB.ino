//TO-DO
//decide if it is necessary to send ok or not during tool change
//decide if it is necessary to send status report or not during tool  change (if not sent the gui will not see the position during tool change)
//check bug of quick consequencial tool changers not executing actions
//set fixed positions as global variables for easy settings every time
//define generic add and return tools functions given the macro
//convert the set tool offset to string and use the macro buffer lines to write it to the teensy‚
/////////////////////////////////INCLUDES////////////////////////////////////////////
//#include <EEPROM.h>
#include <cstddef>
#include <cctype>
#include <cstdlib>
#include "Config.h"
#include <Constants.h>
#include <util/Benchmark.h>
#include "serial/SerialManager.h"
#include "util/ArrayUtil.h"
#include "util/FileHashUtil.h"
#include "util/GCodeBoundsUtil.h"
#include "util/TimerUtil.h"
#include "util/GrblUtil.h"
#include "sdcard/SDCardManager.h"
#include <ArduinoJson.h>
#include "util/JSonUtil.h"
#include "esp32c3/ESP32C3Manager.h"
#include "eeprom/EEPROMManager.h"
#include <pico/unique_id.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <ModbusMaster.h>
#include "macros/JobMacros.h"
#include "macros/RotaryMacros.h"

//buffer variables
unsigned long transferFileLines = 0;
unsigned long globalStartTime = 0.0;
int rpmDifference = 0;
long currentFeedRate = 0;
int wantedSpindleRPM = 0;
long currentSpindleRPM = 0;
long lastRequestedSpindleRPM = 0;
int feedRateOverride = GRBL_OVERRIDE_DEFAULT;
int spindleOverride = GRBL_OVERRIDE_DEFAULT;
bool isFileTransferInProgress = false;
InputSource fileTransferInputSource = INPUT_SOURCE_USB; // channel that started the upload, so a watchdog abort can notify the right client
bool fileTransferHasExistingMetadata = false;
bool fileTransferMetadataChecked = false;
bool isFileDownloadInProgress = false;
bool startRamping = false;
bool ledStatus = false;
bool startUpExection = true;
bool spindleEnabled = false;
bool isManualSpindleRampActive = false;
bool enableCPUAPassthrough = DEFAULT_ENABLE_CPUA_PASSTHROUGH;
bool enableESP32Passthrough = DEFAULT_ENABLE_ESP32_PASSTHROUGH;
bool initialHoming = DEFAULT_INITIAL_HOMING;
float toolLength = 0.0f;

enum FileTransferFailureReason {
  FILE_TRANSFER_FAILURE_NONE,
  FILE_TRANSFER_FAILURE_NOT_STARTED,
  FILE_TRANSFER_FAILURE_INVALID_CHUNK,
  FILE_TRANSFER_FAILURE_SD_READ,
  FILE_TRANSFER_FAILURE_SD_WRITE,
  FILE_TRANSFER_FAILURE_FILE_CREATION,
  FILE_TRANSFER_FAILURE_HASH,
  FILE_TRANSFER_FAILURE_FINALIZE,
  FILE_TRANSFER_FAILURE_DIMENSIONS_EXCEEDED
};

enum OutgoingLineRejectReason {
  OUTGOING_LINE_REJECT_NONE,
  OUTGOING_LINE_REJECT_SOFT_LIMIT,
  OUTGOING_LINE_REJECT_ROTARY_Y_LOCK
};

FileTransferFailureReason fileTransferFailureReason = FILE_TRANSFER_FAILURE_NONE;
// Measured job size of the rejected upload, kept past resetFileTransferState so the failure
// response can report what was actually too big.
float fileTransferFailureSizeX = 0.0f;
float fileTransferFailureSizeY = 0.0f;
float fileTransferFailureSizeZ = 0.0f;
OutgoingLineRejectReason outgoingLineRejectReason = OUTGOING_LINE_REJECT_NONE;

InputSource currentInputSource = INPUT_SOURCE_USB;
char boardDeviceId[16] = {0};

//connection status variables
bool isClientConnected = false;
bool isCurrentSensorInitialized = false;
bool isDistanceLaserSensorInitialized = false;
bool hasDistanceLaserSensorPresence = false;
bool distanceLaserSensorInitRequested = true;
bool distanceLaserSensorKeepEnabledAfterInit = false;
unsigned long distanceLaserSensorProbeReadyMs = 0;
uint8_t distanceLaserSensorInitRetries = 0;
bool isRotaryAxisConnected = false;

//mobileWebsocketConnection status
bool isMobileWebSocketConnected = false;
bool isMobileWebSocketConnecting = false;
bool isClientWiFiConnected = false;
bool isClientWiFiConnecting = false;

//startup auto-homing
bool autoHomingPending = false;
unsigned long autoHomingScheduledMs = 0;

//job related flags
bool isJobRunning = false;
bool isDryRunActive = false;
bool dryRunEnabled = false; // global flag: when true, startJob behaves like startDryRun
bool isJobPaused = false;
// True from a RESUME_JOB request until the machine actually leaves Hold and reaches Run.
// grblHAL's parking resume (spindle soft-start ramp + Z descent) takes a few seconds during
// which the status is still Hold; this lets the app show a "Resuming" state, not "paused".
bool isResuming = false;
bool pauseResumeSpindleRestorePending = false;
bool pendingPauseSpindleStop = false;
unsigned long pauseHoldRequestedMs = 0;
// Gentle-pause feed-drop state: the API pause ramps the feed override down to the resume
// floor WHILE STILL MOVING (grblHAL honors it out of HOLD, and it persists through the hold),
// then issues the actual feed hold. See handlePauseFeedDrop.
bool isPauseFeedDropActive = false;
unsigned long pauseFeedDropLastStepMs = 0;
bool pauseWillStopSpindle = false;
unsigned long lastGRBLStatusReceivedMs = 0;
int pauseResumeOriginalSpindleOverride = GRBL_OVERRIDE_DEFAULT;
bool isResumeSpindleOverrideRampActive = false;
int resumeSpindleOverrideRampTargetPercent = GRBL_OVERRIDE_DEFAULT;
unsigned long resumeSpindleOverrideRampIntervalMs = 0;
unsigned long resumeSpindleOverrideRampLastStepMs = 0;
bool resumeMotionAfterOverrideRampPending = false;
int resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
// Last spindle override percent GRBL actually reported in its "Ov:" status field, plus
// the millis() at which it was last updated. Used by the resume CONFIRM stage to close
// the loop (the ramp itself runs open-loop on the local counter). -1 = never reported.
int lastReportedSpindleOverride = -1;
unsigned long lastReportedSpindleOverrideMs = 0;
// CONFIRM-stage timers.
unsigned long resumeConfirmEnteredMs = 0;     // when CONFIRM started (overall timeout base)
unsigned long resumeConfirmLastActionMs = 0;  // last nudge/entry; gates "fresh Ov: only"
unsigned long resumeConfirmReachedMs = 0;     // when GRBL was first read at target (settle base); 0 = not yet
// Resume feed (motion) ramp state — runs in parallel with the spindle recycle; see
// handleResumeFeedRamp. On resume the spindle needs time to reach RPM, so motion is
// dropped to a low feed override and ramped back up so the tool does not cut at full feed.
bool isResumeFeedRampActive = false;
int resumeFeedRampStage = RESUME_FEED_IDLE;
int resumeFeedRampTargetPercent = GRBL_OVERRIDE_DEFAULT;   // feed override to restore (captured at pause)
unsigned long resumeFeedRampLastStepMs = 0;
unsigned long resumeFeedRampStepIntervalMs = 0;
int pauseResumeOriginalFeedOverride = GRBL_OVERRIDE_DEFAULT;
// The resumeCompleted event fires only once BOTH the spindle recycle and the feed ramp
// have finished; this guards the single push across the two parallel machines.
bool resumeCompletionPending = false;
bool pendingDryRunFeedRate = false;
bool outboundAbsoluteMode = true;
bool outboundMetricUnits = true;
int outboundLastMotionMode = 0; // 0 = unknown/non-motion, 1 = G0, 2 = G1, 3 = G2, 4 = G3
float outboundCurrentWorkX = 0.0f; // tracked programmed work X (not compensated)
float outboundCurrentWorkY = 0.0f; // tracked programmed work Y (not compensated)
float outboundCurrentWorkZ = 0.0f; // tracked programmed work Z (not compensated)
float outboundCurrentFeedRate = 0.0f; // tracked programmed feed rate (mm/min)

struct JobLineSeg {
  bool  active;
  float x0, y0, z0;    // start work position and programmed Z
  float x1, y1, z1;    // end work position and programmed Z
  float totalLen;       // total XY segment length (mm)
  float sentLen;        // XY length of sub-moves already sent
  float stepLen;        // step size per sub-move (mm)
  float feedRate;       // F word (mm/min)
} jobLineSeg = {};

struct JobArcSeg {
  bool  active;
  float cx, cy;         // arc centre in work coords
  float radius;
  float currentAngle;   // current angle position (rad)
  float totalAngle;     // total arc sweep (rad, always positive)
  float angleCompleted; // arc swept so far (rad)
  float stepAngle;      // step size per sub-move (rad)
  float z0, z1;         // programmed Z at start and end
  float feedRate;       // F word (mm/min)
  float x1, y1;         // exact end position (avoids float accumulation on last step)
  bool  clockwise;      // true = G2, false = G3
} jobArcSeg = {};

unsigned long jobLineIndex = 0;
unsigned long openedReadFileTotalLines = 0;
unsigned long jobStartMs = 0;

bool isMacroRunning = false;
int macroLineIndex = 0;

//ESP32C3 initial config variables
bool initialESP32C3Config = false;
bool atRequested = false;
bool wsLink1CloseRequested = false; // close any previously-open WebSocket link 1 before reconfiguring
bool accessPointModeRequested = false;
bool startAccessPoint = false;
bool clientWiFiConnectionAttempted = false;

bool mobileWebsocketConfigured = false;

//BLE scan variables
bool bleDeInitRequested = false;
bool bleDeInitComplete = false;
bool bleInitRequested = false;
bool bleInitComplete = false;
bool bleScanParamsRequested = false;
bool bleScanParamsComplete = false;
bool bleScanRequested = false;
bool bleScanComplete = false;


int initialESP32ResponsesReceived = 0;
int initialESP32ExpectedResponses = 6; // 6 with hotspot enabled, 5 without (1 WSCLOSE cleanup + WebSocket config + WiFi steps)

//other variables
char bufferCharUSB = NULL_CHAR;
char bufferCharCPUA = NULL_CHAR;
char USBLineArray[LINE_BUFFER_CHARACTERS];
char CPUALineArray[CPUA_LINE_BUFFER_CHARACTERS];
char bufferMacroLines[MACRO_BUFFER_LINES][MACRO_LINE_BUFFER_CHARS];
char gcodeLineBuffer[LINE_BUFFER_CHARACTERS];
char openedReadFileName[SD_MAX_FILE_NAME_LENGTH];
char transferFileName[SD_MAX_FILE_NAME_LENGTH];
char downloadFileName[SD_MAX_FILE_NAME_LENGTH];
char mobileWebSocketMessageBuffer[ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE];
char mobileServerName[ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE] = {0};
bool isBleActive = false;
unsigned long mobileWebSocketNextRetryMs = 0;
unsigned long mobileWebSocketConnectingStartMs = 0;
unsigned long bleScanSessionStartMs = 0;
unsigned long serialLastApiPingMs = 0;
unsigned long mobileWebSocketLastApiPingMs = 0;
unsigned long fileTransferLastChunkActivityMs = 0;
unsigned long fileDownloadLastChunkActivityMs = 0;
unsigned long manualSpindleRampLastStepMs = 0;
unsigned long manualSpindleRampIntervalMs = 0;
int pendingForcedWebSocketCloseLinkId = -1;
unsigned long pendingForcedWebSocketCloseStartMs = 0;
uint16_t transferFileYear = 0;
uint8_t transferFileMonth = 0;
uint8_t transferFileDay = 0;
uint8_t transferFileHour = 0;
uint8_t transferFileMinute = 0;
uint8_t transferFileSecond = 0;

char fileChunkLines[FILE_CHUNK_LINES_BUFFER];
char downloadFileChunkBuffer[DOWNLOAD_FILE_CHUNK_BUFFER];
unsigned long downloadFileCurrentLine = 0;
int downloadChunkSendFailures = 0; // consecutive send failures for the current chunk; bounds the rewind/re-request loop
long manualSpindleRampTargetRPM = 0;
long manualSpindleRampNextRPM = 0;
long manualSpindleRampLastCommandedRPM = 0;
// Set only for a ramp triggered by an M3 read from a JOB. Such a ramp holds motion until GRBL's
// planner has drained, so the spindle comes up with the machine stopped and motion only restarts once
// it is at speed. Never set for a console M3 (nothing is streaming) nor for any other path.
bool manualSpindleRampAwaitingDrain = false;
unsigned long manualSpindleRampDrainStartedMs = 0;
// The final speed has been SENT but the spindle has not yet had time to reach it. The ramp stays
// active through this, so the job stays held and the reported state stays Run.
bool manualSpindleRampSettlePending = false;

// Modal spindle state (mirrors GRBL's modal words so the ramp engages regardless
// of how the CAM splits M3/S across lines). commandedSpindleRPM is the RPM we
// last actually sent to GRBL — the ramp base is always derived from it, never
// from status reports (which lag during a job and would cause jump starts).
bool isSpindleOnCommanded = false;              // M3 seen, cleared by M5/M30/reset/job start+stop
long commandedSpindleRPM = 0;                   // last RPM actually commanded to GRBL
// A line carrying more than spindle words (e.g. "G1 X10 S9000") cannot be consumed
// by the ramp: it is held here and released once the ramp completes.
bool isSpindleLineHeld = false;
bool heldSpindleLineIsJob = false;              // held from the job reader vs console passthrough
char heldSpindleLineBuffer[LINE_BUFFER_CHARACTERS];
unsigned long downloadFileTotalLines = 0;

byte bufferByteUSB = 0;
byte bufferByteCPUA = 0;
int syncActionIndex = 0;
int bufferMacroLineIndex = 0;

float currentMachinePositionX = 0.0;
float currentMachinePositionY = 0.0;
float currentMachinePositionZ = 0.0;
float currentMachinePositionA = 0.0;

float currentJobPositionX = 0.0;
float currentJobPositionY = 0.0;
float currentJobPositionZ = 0.0;
float currentJobPositionA = 0.0;

//cached work coordinate offset (WCO from GRBL status); used to derive job position = MPos - WCO
float wcoX = 0.0;
float wcoY = 0.0;
float wcoZ = 0.0;
float wcoA = 0.0;

WifiSettings wifiSettings;
DebuggingSettings debuggingSettings;
JsonSettings jsonSettings;
MachineNameSettings machineNameSettings;
ToolLengthReferenceSettings toolLengthReferenceSettings;
LaserOffsetZeroSettings laserOffsetZeroSettings;
BrushlessMotorSettings brushlessMotorSettings;
HousingSettings housingSettings;
ProbeHeightSettings probeHeightSettings;
unsigned long doorSensorOpenSinceMs = 0; // millis() when the door pin first read OPEN, for debounce (0 = closed)
bool doorOpenDebounced = false;          // true once the door has read OPEN for DOOR_SENSOR_DEBOUNCE_MS (housing installed only)
bool virtualDoorLatched = false;         // CPUB's interlock decision mirrored to grblHAL; latches while the lid is open

// Machine usage meter — accumulated in RAM, persisted to a hidden SD file. Read-only via API.
uint32_t usagePowerOnSeconds = 0;
uint32_t usageRunSeconds = 0;
uint32_t usageSpindleSeconds = 0;
uint32_t usageJobCount = 0;             // lifetime count of jobs launched
uint32_t usageSequence = 0;            // highest persisted record sequence number
bool usageDataValid = false;           // a valid record was loaded from SD at boot
unsigned long usageLastTickMs = 0;     // last 1-second accumulation tick
unsigned long usageLastFlushMs = 0;    // last SD persistence
char firmwareVersion[FIRMWARE_VERSION_CHARS];

JsonDocument receivedJsonBuffer;
JsonDocument responseJsonBuffer;
char jsonResponseCharBuffer[JSON_BUFFER_SIZE];

MachineState currentMachineState = UNKNOWN;
bool currentProbeTriggered = false;
bool isHoming = false;
InputSource homingInputSource = INPUT_SOURCE_USB;
InputSource resumeInputSource = INPUT_SOURCE_USB;
bool homedSincePowerOn = false;
bool isProbeZ0MacroRunning = false;
InputSource probeZ0MacroInputSource = INPUT_SOURCE_USB;
bool isProbeToolLengthMacroRunning = false;
InputSource probeToolLengthMacroInputSource = INPUT_SOURCE_USB;
bool probeToolLengthZCaptured = false;
bool isRotaryYLockBypassed = false;

// Laser edge probing state machine
// Coarse scan states (one continuous move per direction) + fine scan + corner positioning
enum LaserEdgeProbeState : uint8_t {
  LASER_EDGE_PROBE_IDLE = 0,
  LASER_EDGE_PROBE_COARSE_SCAN,    // send long G1, monitor laser while moving
  LASER_EDGE_PROBE_COARSE_STOP,    // feed-hold → soft-reset → unlock sequence
  LASER_EDGE_PROBE_RETURN_FINE,    // G90 move to fine-scan start position
  LASER_EDGE_PROBE_FINE_SCAN,      // step-and-settle fine detection
  LASER_EDGE_PROBE_FINE_RETURN,    // G90 return to start X/Y for next direction
  LASER_EDGE_PROBE_Y_BASELINE,     // re-measure laser baseline before Y phase
  LASER_EDGE_PROBE_GO_CORNER,
  LASER_EDGE_PROBE_SET_ORIGIN
};
enum LaserEdgeProbeSubState : uint8_t {
  LASER_EDGE_SUB_SEND = 0,
  LASER_EDGE_SUB_SEND_MOTION,      // second half of two-step send (mode cmd already sent)
  LASER_EDGE_SUB_WAIT,
  LASER_EDGE_SUB_READ,
  LASER_EDGE_SUB_MONITOR,          // monitoring laser during continuous motion
  LASER_EDGE_SUB_WAIT_HOLD,        // waiting for HOLD state after feed-hold
  LASER_EDGE_SUB_WAIT_ALARM,       // waiting for ALARM after soft reset
  LASER_EDGE_SUB_WAIT_IDLE         // waiting for IDLE after $X unlock
};
enum LaserEdgeProbeDirState : uint8_t {
  PROBE_DIR_XNEG = 0,
  PROBE_DIR_XPOS = 1,
  PROBE_DIR_YNEG = 2,
  PROBE_DIR_YPOS = 3
};

enum LaserMaterialBoundsProbeState : uint8_t {
  LASER_MATERIAL_BOUNDS_PROBE_IDLE = 0,
  LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN,
  LASER_MATERIAL_BOUNDS_PROBE_COARSE_STOP,
  LASER_MATERIAL_BOUNDS_PROBE_RETURN_FINE,
  LASER_MATERIAL_BOUNDS_PROBE_FINE_SCAN,
  LASER_MATERIAL_BOUNDS_PROBE_FINE_RETURN,
  LASER_MATERIAL_BOUNDS_PROBE_AXIS_BASELINE,
  LASER_MATERIAL_BOUNDS_PROBE_CENTER_MOVE,
  LASER_MATERIAL_BOUNDS_PROBE_WAIT_CENTER
};
bool isLaserEdgeProbingActive = false;
bool laserEdgeProbeSuppressAlarmAbort = false;
bool laserEdgeProbeWaitingForInitialUnlock = false;
InputSource laserEdgeProbeInputSource = INPUT_SOURCE_USB;
LaserEdgeProbeState laserEdgeProbeState = LASER_EDGE_PROBE_IDLE;
LaserEdgeProbeSubState laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
LaserEdgeProbeDirState laserEdgeProbeCurrentDir = PROBE_DIR_XNEG;
float laserEdgeProbeBaselineMm = 0.0f;
float laserEdgeProbeStartX = 0.0f;          // work-coord start (used for scan targets)
float laserEdgeProbeStartY = 0.0f;
float laserEdgeProbeStartMachineX = 0.0f;  // machine-coord start (used for return moves)
float laserEdgeProbeStartMachineY = 0.0f;
float laserEdgeProbeXNeg = 0.0f;
float laserEdgeProbeYNeg = 0.0f;
float laserEdgeProbeRoughEdge = 0.0f;         // rough edge position captured at HOLD after coarse scan
float laserEdgeProbeCoarseCurrentTarget = 0.0f; // absolute work-coord target for the current streaming increment
float laserEdgeProbeTraveled = 0.0f;          // total distance sent in coarse scan
float laserEdgeProbeBaselineSampleSum = 0.0f;
float laserEdgeProbeReturnTargetX = 0.0f;
float laserEdgeProbeReturnTargetY = 0.0f;
unsigned long laserEdgeProbeStepSentMs = 0;
uint8_t laserEdgeProbeSetOriginStep = 0;
uint8_t laserEdgeProbeBaselineSampleCount = 0;
bool laserEdgeProbeReturnMotionObserved = false;
bool laserEdgeProbeRecoveryNeedsReset = false;
// Selected corner → the two edge directions to probe (X first, then Y) and the
// offset moved INTO the material along each axis before the origin is set.
// Defaults describe the bottom-left corner with zero offset.
LaserEdgeProbeDirState laserEdgeProbeFirstDir = PROBE_DIR_XNEG;   // X-axis edge for the corner
LaserEdgeProbeDirState laserEdgeProbeSecondDir = PROBE_DIR_YNEG;  // Y-axis edge for the corner
float laserEdgeProbeXOffset = 0.0f;
float laserEdgeProbeYOffset = 0.0f;

bool isLaserMaterialBoundsProbingActive = false;
bool laserMaterialBoundsProbeSuppressAlarmAbort = false;
bool laserMaterialBoundsProbeWaitingForInitialUnlock = false;
InputSource laserMaterialBoundsProbeInputSource = INPUT_SOURCE_USB;
LaserMaterialBoundsProbeState laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_IDLE;
LaserEdgeProbeSubState laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
LaserEdgeProbeDirState laserMaterialBoundsProbeCurrentDir = PROBE_DIR_XNEG;
float laserMaterialBoundsProbeBaselineMm = 0.0f;
float laserMaterialBoundsProbeStartX = 0.0f;
float laserMaterialBoundsProbeStartY = 0.0f;
float laserMaterialBoundsProbeXMin = 0.0f;
float laserMaterialBoundsProbeXMax = 0.0f;
float laserMaterialBoundsProbeYMin = 0.0f;
float laserMaterialBoundsProbeYMax = 0.0f;
float laserMaterialBoundsProbeRoughEdge = 0.0f;
float laserMaterialBoundsProbeCoarseCurrentTarget = 0.0f;
float laserMaterialBoundsProbeTraveled = 0.0f;
float laserMaterialBoundsProbeCenterX = 0.0f;
float laserMaterialBoundsProbeCenterY = 0.0f;
float lastDetectedMaterialCenterX = 0.0f;
float lastDetectedMaterialCenterY = 0.0f;
bool hasLastDetectedMaterialCenter = false;
float laserMaterialBoundsProbeReturnTargetX = 0.0f;
float laserMaterialBoundsProbeReturnTargetY = 0.0f;
float laserMaterialBoundsProbeBaselineSampleSum = 0.0f;
uint8_t laserMaterialBoundsProbeBaselineSampleCount = 0;
unsigned long laserMaterialBoundsProbeStepSentMs = 0;
bool laserMaterialBoundsProbeReturnMotionObserved = false;
bool laserMaterialBoundsProbeRecoveryNeedsReset = false;
const char* laserMaterialBoundsProbeAbortStage = nullptr;
const char* laserMaterialBoundsProbeAbortDetail = nullptr;

enum HeightMapScanState : uint8_t {
  HEIGHT_MAP_SCAN_IDLE = 0,
  HEIGHT_MAP_SCAN_WAIT_SENSOR_READY,
  // Point-by-point states (legacy, HEIGHT_MAP_USE_LEGACY_GRID_SCAN)
  HEIGHT_MAP_SCAN_MOVE,
  HEIGHT_MAP_SCAN_WAIT_IDLE,
  HEIGHT_MAP_SCAN_SETTLE,
  HEIGHT_MAP_SCAN_SAMPLE,
  // Line scan states (active)
  HEIGHT_MAP_SCAN_MOVE_BASELINE,
  HEIGHT_MAP_SCAN_WAIT_BASELINE_IDLE,
  HEIGHT_MAP_SCAN_BASELINE_SAMPLE,
  HEIGHT_MAP_SCAN_MOVE_ROW_START,
  HEIGHT_MAP_SCAN_WAIT_ROW_START_IDLE,
  HEIGHT_MAP_SCAN_START_ROW,
  HEIGHT_MAP_SCAN_SAMPLING,
  // Shared return states
  HEIGHT_MAP_SCAN_RETURN_ORIGIN,
  HEIGHT_MAP_SCAN_WAIT_RETURN_IDLE
};
bool heightMapConfigured = false;
float heightMapSizeXmm = 0.0f;
float heightMapSizeYmm = 0.0f;
bool heightMapFineDensity = false;
bool heightMapScanInProgress = false;
bool heightMapReady = false;
bool heightMapCompensationEnabled = false;
bool heightMapUsedForCurrentFile = false;
char heightMapBoundFile[SD_MAX_FILE_NAME_LENGTH] = {0};
InputSource heightMapInputSource = INPUT_SOURCE_USB;
HeightMapScanState heightMapScanState = HEIGHT_MAP_SCAN_IDLE;
uint16_t heightMapGridPointsX = 0;
uint16_t heightMapGridPointsY = 0;
uint16_t heightMapTotalPoints = 0;
uint16_t heightMapNextPointIndex = 0;
float heightMapSpacingXmm = 0.0f;
float heightMapSpacingYmm = 0.0f;
float heightMapOriginX = 0.0f;
float heightMapOriginY = 0.0f;
float heightMapStartMachineX = 0.0f;
float heightMapStartMachineY = 0.0f;
// Where the head was when startHeightMapScan was accepted. The scan returns HERE when it finishes,
// which since the map can be anchored at job zero is no longer necessarily the map origin.
float heightMapReturnX = 0.0f;
float heightMapReturnY = 0.0f;
// The work offset that was in effect when the scan started. The map is anchored in job coordinates,
// so this is what tells us later whether those coordinates still mean the same place on the material.
float heightMapWcoX = 0.0f;
float heightMapWcoY = 0.0f;
float heightMapBaselineDistanceMm = 0.0f;
// Work-coord target of the in-flight scan move; WAIT_IDLE gates sampling on the head reaching it.
float heightMapMoveTargetX = 0.0f;
float heightMapMoveTargetY = 0.0f;
// Legacy point-by-point sample accumulator (averaging): reads are spaced out and averaged.
float heightMapSampleSumMm = 0.0f;
int heightMapSampleValidCount = 0;
int heightMapSampleAttempt = 0;
unsigned long heightMapNextSampleMs = 0;
unsigned long heightMapSettleUntilMs = 0;
unsigned long heightMapWaitOkSinceMs = 0;
unsigned long heightMapSensorReadyMs = 0;
const char* heightMapAbortStage = nullptr;
const char* heightMapAbortDetail = nullptr;
// Finished map: one value per grid cell, in serpentine scan order (see hmPhysToScanIdx). Holds the
// raw per-cell trimmed-mean DISTANCE during a line scan and is re-expressed relative to the origin
// cell in finalizeHeightMapLineScan().
float heightMapRelativeMm[HEIGHT_MAP_MAX_POINTS_PER_AXIS * HEIGHT_MAP_MAX_POINTS_PER_AXIS] = {0};

// ---- Line scan acquisition state ----
// Row geometry, in the TOOL frame. The G1s add the laser offset so the LASER rides the row while the
// map stays in the tool frame, exactly as the legacy per-point scan did.
uint16_t heightMapCurrentRow = 0;
float heightMapRowStartX = 0.0f;        // exact X at row start (theoretical)
float heightMapRowEndX = 0.0f;          // exact X at row end (theoretical)
float heightMapRowY = 0.0f;             // exact Y for the current row
unsigned long heightMapRowStartMs = 0;  // millis() when the current row G1 was issued
uint32_t heightMapRowDurationMs = 0;    // expected row duration (acceleration negligible at this feed)
// Samples arrive in X order along a row, so leaving a column means that column is finished. Only ONE
// cell's samples are ever held in RAM; the cell is collapsed to a trimmed mean on the way out.
float heightMapCellSamples[HEIGHT_MAP_CELL_MAX_SAMPLES] = {0};
uint16_t heightMapCellSampleCount = 0;
int32_t heightMapCellActiveCol = -1;
// Per-cell "has a real measurement" bitset. Cells that never reached HEIGHT_MAP_CELL_MIN_SAMPLES stay
// clear and are filled from their neighbours at the end of the scan instead of silently inheriting the
// previous cell's value.
uint8_t heightMapCellValid[(HEIGHT_MAP_MAX_POINTS_PER_AXIS * HEIGHT_MAP_MAX_POINTS_PER_AXIS + 7) / 8] = {0};
// Scan quality figures reported through the API.
uint32_t heightMapSamplesTaken = 0;
uint32_t heightMapCellDroppedSamples = 0;
uint16_t heightMapCellsInterpolated = 0;
uint16_t heightMapMinCellSamples = 0;
float heightMapMaxCellSpreadMm = 0.0f;
// Physical grid position of the cell that produced heightMapMaxCellSpreadMm, and how many cells were
// noisy at all, so a bad scan can be located instead of merely detected.
uint16_t heightMapNoisiestCellRow = 0;
uint16_t heightMapNoisiestCellCol = 0;
uint16_t heightMapNoisyCells = 0;
// Extreme raw readings seen during the scan, to show how much sensor headroom it actually had.
// Deliberately NOT cleared by invalidateHeightMap: they have to survive an abort so the failure event
// can report the reading that caused it. Reset when a scan starts instead.
float heightMapMinReadingMm = 0.0f;
float heightMapMaxReadingMm = 0.0f;
bool heightMapHasReading = false;
// Z datum, measured standing still at job XY zero before the sweep. Like the reading extremes above,
// these are NOT cleared by invalidateHeightMap so a failure event can still report them; they are
// reset when a scan starts.
bool heightMapBaselineCaptured = false;
bool heightMapDatumFromCells = false;
float heightMapDatumBiasMm = 0.0f;
float heightMapBaselineSpreadMm = 0.0f;
uint16_t heightMapBaselineSampleCount = 0;
int heightMapBaselineAttempt = 0;

// Point cloud scan state
enum PointCloudScanState : uint8_t {
  POINT_CLOUD_SCAN_IDLE = 0,
  POINT_CLOUD_SCAN_WAIT_SENSOR_READY,
  // Point-by-point states (legacy, used by commented-out scan mode)
  POINT_CLOUD_SCAN_MOVE,
  POINT_CLOUD_SCAN_WAIT_IDLE,
  POINT_CLOUD_SCAN_SETTLE,
  POINT_CLOUD_SCAN_SAMPLE,
  // Line scan states (active)
  POINT_CLOUD_SCAN_START_ROW,
  POINT_CLOUD_SCAN_SAMPLING,
  POINT_CLOUD_SCAN_MOVE_Y,
  POINT_CLOUD_SCAN_WAIT_Y_IDLE,
  // Shared return states
  POINT_CLOUD_SCAN_RETURN_ORIGIN,
  POINT_CLOUD_SCAN_WAIT_RETURN_IDLE
};
bool pointCloudScanInProgress = false;
bool pointCloudReady = false;
PointCloudScanState pointCloudScanState = POINT_CLOUD_SCAN_IDLE;
float pointCloudSizeXmm = 0.0f;
float pointCloudSizeYmm = 0.0f;
float pointCloudSpacingMm = 0.0f;
uint16_t pointCloudGridPointsX = 0;
uint16_t pointCloudGridPointsY = 0;
uint32_t pointCloudTotalPoints = 0;
uint32_t pointCloudNextPointIndex = 0;
float pointCloudOriginX = 0.0f;
float pointCloudOriginY = 0.0f;
float pointCloudBaselineDistanceMm = 0.0f;
unsigned long pointCloudSettleUntilMs = 0;
unsigned long pointCloudWaitOkSinceMs = 0;
unsigned long pointCloudSensorReadyMs = 0;
char pointCloudFilename[SD_MAX_FILE_NAME_LENGTH] = {0};
char pointCloudDensityStr[12] = {0};
const char* pointCloudAbortStage = nullptr;
const char* pointCloudAbortDetail = nullptr;
InputSource pointCloudInputSource = INPUT_SOURCE_USB;
uint32_t pointCloudVertexCountOffset = 0;
uint8_t pointCloudSyncCounter = 0;
uint16_t pointCloudCurrentRow = 0;
float pointCloudRowStartX = 0.0f;      // exact X at row start (theoretical)
float pointCloudRowEndX = 0.0f;        // exact X at row end (theoretical)
float pointCloudRowY = 0.0f;           // exact Y for current row (theoretical)
unsigned long pointCloudRowStartMs = 0;       // millis() when current row G1 was issued
uint32_t pointCloudRowDurationMs = 0;          // expected row duration (acceleration negligible)
uint16_t pointCloudFileYear = 0;
uint8_t pointCloudFileMonth = 0;
uint8_t pointCloudFileDay = 0;
uint8_t pointCloudFileHour = 0;
uint8_t pointCloudFileMinute = 0;
uint8_t pointCloudFileSecond = 0;

//COUNTERS
int USBLinePos = 0;
int CPUALinePos = 0;
int mobileWebSocketMessageBufferPos = 0;
//when we are executing the macros for the tool change, we need wait for an ok from GRBL before proceeding further
//this counter keeps the number of oks we need to wait before sending the next line
int waitForOk = 0;
//number of oks accepted during the current job only thanks to the tolerant matching (damaged or glued), see logRecoveredJobOk
unsigned long jobRecoveredOkCount = 0;

//Press-and-hold jogging state (see handleJogWatchdog / sendJogStep / JOG command).
//isJogging is true while a jog is being streamed from the app; jogLastActivityMs is the
//millis() timestamp of the last received jog step, used by the keep-alive watchdog.
bool isJogging = false;
unsigned long jogLastActivityMs = 0;

//Array of sync actions
SyncAction syncActions[SYNC_ACTION_AMOUNT];

//Objects
Benchmark benchmark;
SerialManager serialManager;
ArrayUtil arrayUtil;
TimerUtil timerUtil;
GrblUtil grblUtil;
GCodeBoundsUtil gcodeBoundsUtil;
SDCardManager sdCardManager;
JsonUtil jsonUtil;
ESP32C3Manager esp32c3Manager;
EEPROMManager eepromManager;
Adafruit_INA219 currentSensor(CURRENT_SENSOR_I2C_ADDRESS);
ModbusMaster distanceLaserSensorModbus;

void preDistanceLaserTransmission() {

  digitalWrite(DISTANCE_LASER_SENSOR_DIR_PIN, HIGH);   // HIGH = transmit
  delayMicroseconds(DISTANCE_LASER_SENSOR_DIR_SETTLE_US);   // let the transceiver settle before bit 1

}

void postDistanceLaserTransmission() {

  // SoftwareSerial::write() blocks, so the last bit is already on the wire by now.
  delayMicroseconds(DISTANCE_LASER_SENSOR_DIR_SETTLE_US);
  digitalWrite(DISTANCE_LASER_SENSOR_DIR_PIN, LOW);    // LOW = receive

}

void requestDistanceLaserSensorInitialization(unsigned long probeDelayMs, bool keepEnabledAfterInit) {

  distanceLaserSensorInitRequested = true;
  distanceLaserSensorKeepEnabledAfterInit = keepEnabledAfterInit;
  isDistanceLaserSensorInitialized = false;
  distanceLaserSensorInitRetries = 0;
  distanceLaserSensorProbeReadyMs = millis() + probeDelayMs;
  digitalWrite(LASER_SENSOR_ENABLE_PIN, HIGH);

}

void disableDistanceLaserSensor() {

  distanceLaserSensorInitRequested = false;
  distanceLaserSensorKeepEnabledAfterInit = false;
  isDistanceLaserSensorInitialized = false;
  distanceLaserSensorInitRetries = 0;
  distanceLaserSensorProbeReadyMs = 0;
  digitalWrite(LASER_SENSOR_ENABLE_PIN, LOW);

}

// Drives the brushless accessory's direction pin to the configured level. Called once after the
// settings are restored from EEPROM, and again whenever setBrushlessMotorSettings changes them.
// The pin is an output held at a static level: the accessory reads it, nothing toggles it per move.
void applyBrushlessMotorDirection() {

  digitalWrite(BRUSHLESS_MOTOR_DIR_PIN, brushlessMotorSettings.directionReversed ? HIGH : LOW);

  if (debuggingSettings.debuggingBase) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrint("Brushless motor direction pin driven ");
    serialManager.USBSerialPrintln(brushlessMotorSettings.directionReversed ? "HIGH (reversed)" : "LOW (default)");
  }

}

// Reads the opened file's toolpath extent, in job coordinates, from its metadata line. Returns false
// when no file is open or the file predates MIN/MAX metadata -- in which case WHERE the toolpath sits
// is simply unknown, and no caller should guess from SIZEX/SIZEY, which carry extent only.
bool readOpenedFileBounds(float* minX, float* maxX, float* minY, float* maxY) {

  if (openedReadFileName[0] == NULL_CHAR)
    return false;

  char metadataLine[256];
  return sdCardManager.readFirstLine(openedReadFileName, metadataLine, sizeof(metadataLine))
      && GCodeBoundsUtil::parseMetadataBounds(metadataLine, minX, maxX, minY, maxY);

}

// Does the current map's rectangle contain the opened file's toolpath?
//
// This is the question a size comparison cannot answer: a map big enough for the job still misses it
// if it was scanned somewhere else, and interpolateHeightMapZ CLAMPS lookups outside its rectangle
// rather than failing, so an uncovered job runs to completion on edge values.
//
// `known` receives false when the answer cannot be determined (no map, no file open, or no MIN/MAX
// metadata). A false return with known == false means UNKNOWN, not "does not cover".
bool doesHeightMapCoverOpenedJob(bool* known) {

  if (known != nullptr)
    *known = false;

  if (!heightMapReady || heightMapGridPointsX == 0 || heightMapGridPointsY == 0)
    return false;

  float jobMinX = 0.0f, jobMaxX = 0.0f, jobMinY = 0.0f, jobMaxY = 0.0f;
  if (!readOpenedFileBounds(&jobMinX, &jobMaxX, &jobMinY, &jobMaxY))
    return false;

  if (known != nullptr)
    *known = true;

  // The map spans +X and -Y from its origin, so the origin is its top-left corner.
  return !(jobMinX < heightMapOriginX - HEIGHT_MAP_JOB_COVERAGE_TOLERANCE_MM
        || jobMaxX > heightMapOriginX + heightMapSizeXmm + HEIGHT_MAP_JOB_COVERAGE_TOLERANCE_MM
        || jobMinY < heightMapOriginY - heightMapSizeYmm - HEIGHT_MAP_JOB_COVERAGE_TOLERANCE_MM
        || jobMaxY > heightMapOriginY + HEIGHT_MAP_JOB_COVERAGE_TOLERANCE_MM);

}

void logLaserProbeStartFailure(const char* reason) {

  serialManager.USBSerialPrint(FROM_CPUB);
  serialManager.USBSerialPrint("[LaserProbe] START rejected: ");
  serialManager.USBSerialPrintln(reason);

}

void invalidateHeightMap(bool clearConfiguration) {

  heightMapScanInProgress = false;
  heightMapReady = false;
  heightMapUsedForCurrentFile = false;
  heightMapScanState = HEIGHT_MAP_SCAN_IDLE;
  heightMapGridPointsX = 0;
  heightMapGridPointsY = 0;
  heightMapTotalPoints = 0;
  heightMapNextPointIndex = 0;
  heightMapSpacingXmm = 0.0f;
  heightMapSpacingYmm = 0.0f;
  heightMapOriginX = 0.0f;
  heightMapOriginY = 0.0f;
  heightMapStartMachineX = 0.0f;
  heightMapStartMachineY = 0.0f;
  heightMapReturnX = 0.0f;
  heightMapReturnY = 0.0f;
  heightMapWcoX = 0.0f;
  heightMapWcoY = 0.0f;
  heightMapBaselineDistanceMm = 0.0f;
  heightMapSettleUntilMs = 0;
  heightMapWaitOkSinceMs = 0;
  heightMapSensorReadyMs = 0;
  heightMapAbortStage = nullptr;
  heightMapAbortDetail = nullptr;
  // Line scan acquisition state and quality figures
  heightMapCurrentRow = 0;
  heightMapRowStartX = 0.0f;
  heightMapRowEndX = 0.0f;
  heightMapRowY = 0.0f;
  heightMapRowStartMs = 0;
  heightMapRowDurationMs = 0;
  heightMapCellSampleCount = 0;
  heightMapCellActiveCol = -1;
  heightMapSamplesTaken = 0;
  heightMapCellDroppedSamples = 0;
  heightMapCellsInterpolated = 0;
  heightMapMinCellSamples = 0;
  heightMapMaxCellSpreadMm = 0.0f;
  arrayUtil.clearCharArray(heightMapBoundFile, SD_MAX_FILE_NAME_LENGTH);

  if (clearConfiguration) {
    heightMapConfigured = false;
    heightMapSizeXmm = 0.0f;
    heightMapSizeYmm = 0.0f;
  }

}

uint16_t getHeightMapGridPointsForSize(float sizeMm, float spacingMm, uint16_t maxPoints) {

  if (sizeMm <= 0.0f || spacingMm <= 0.0f)
    return 0;

  int points = (int)(sizeMm / spacingMm + 0.999f) + 1;
  if (points < 2)
    points = 2;
  if (points > (int)maxPoints)
    points = (int)maxPoints;
  return (uint16_t)points;

}

void getHeightMapPointTarget(uint16_t pointIndex, float* x, float* y) {

  if (x == nullptr || y == nullptr || heightMapGridPointsX == 0)
    return;

  uint16_t row = pointIndex / heightMapGridPointsX;
  uint16_t colInRow = pointIndex % heightMapGridPointsX;
  uint16_t col = (row % 2 == 0) ? colInRow : (heightMapGridPointsX - 1 - colInRow);

  *x = heightMapOriginX + ((float)col * heightMapSpacingXmm);
  *y = heightMapOriginY - ((float)row * heightMapSpacingYmm);

}

void pushHeightMapScanCompletedEvent(bool success) {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = heightMapInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_HEIGHT_MAP_SCAN_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_SCANNED_STR] = heightMapNextPointIndex;
  if (success) {
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_X_STR] = heightMapGridPointsX;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_Y_STR] = heightMapGridPointsY;
#if !HEIGHT_MAP_USE_LEGACY_GRID_SCAN
    // Scan quality, so the frontend can warn on a poor scan instead of trusting a smooth-looking map.
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SAMPLES_TAKEN_STR] = heightMapSamplesTaken;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_CELLS_INTERPOLATED_STR] = heightMapCellsInterpolated;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MIN_CELL_SAMPLES_STR] = heightMapMinCellSamples;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MAX_CELL_SPREAD_MM_STR] =
      roundf(heightMapMaxCellSpreadMm * 1000.0f) / 1000.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISY_CELLS_STR] = heightMapNoisyCells;
    // Offset from the scan start corner, matching the frame getHeightMapPoints reports in.
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_X_MM_STR] =
      roundf((float)heightMapNoisiestCellCol * heightMapSpacingXmm * 1000.0f) / 1000.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_Y_MM_STR] =
      roundf(-(float)heightMapNoisiestCellRow * heightMapSpacingYmm * 1000.0f) / 1000.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_BASELINE_SAMPLES_STR] = heightMapBaselineSampleCount;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_BASELINE_SPREAD_MM_STR] =
      roundf(heightMapBaselineSpreadMm * 1000.0f) / 1000.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DATUM_FROM_CELLS_STR] = heightMapDatumFromCells;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DATUM_BIAS_MM_STR] =
      roundf(heightMapDatumBiasMm * 1000.0f) / 1000.0f;
#endif
  } else {
    if (heightMapAbortStage != nullptr)
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_STAGE_STR] = heightMapAbortStage;
    if (heightMapAbortDetail != nullptr)
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_DETAIL_STR] = heightMapAbortDetail;
  }
#if !HEIGHT_MAP_USE_LEGACY_GRID_SCAN
  // Reported either way: on a SENSOR_RANGE abort these carry the reading that caused it, which is
  // what tells the operator how far to move Z.
  if (heightMapHasReading) {
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MIN_READING_MM_STR] = roundf(heightMapMinReadingMm * 100.0f) / 100.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MAX_READING_MM_STR] = roundf(heightMapMaxReadingMm * 100.0f) / 100.0f;
  }
#endif
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;

}

void pushHeightMapScanProgressEvent() {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = heightMapInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_HEIGHT_MAP_SCAN_PROGRESS_STR;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_SCANNED_STR] = heightMapNextPointIndex;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_TOTAL_STR] = heightMapTotalPoints;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_X_STR] = heightMapGridPointsX;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_Y_STR] = heightMapGridPointsY;
#if !HEIGHT_MAP_USE_LEGACY_GRID_SCAN
  // A whole row is one continuous move now, so the event fires per row rather than per point. The
  // point counters above still carry cells-resolved / cells-total for existing progress bars.
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ROWS_SCANNED_STR] = heightMapCurrentRow;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ROWS_TOTAL_STR] = heightMapGridPointsY;
  responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SAMPLES_TAKEN_STR] = heightMapSamplesTaken;
#endif
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;

}

void abortHeightMapScan(const char* stage, const char* detail) {

  heightMapAbortStage = stage;
  heightMapAbortDetail = detail;
  // Stop any motion currently in flight on GRBL (Ctrl-X soft reset) and clear the
  // pending-ack counter so the firmware does not keep waiting for an `ok` that the
  // cancelled G1 will never produce.
  serialManager.CPUASerialWrite(GRBL_STOP_RESET);
  waitForOk = 0;
  invalidateHeightMap(false);
  heightMapAbortStage = stage;
  heightMapAbortDetail = detail;
  pushHeightMapScanCompletedEvent(false);

}

void logHeightMapScanDebug(const char* phase, const char* detail) {

  if (!debuggingSettings.debuggingMacroBuffer && !debuggingSettings.debuggingStatus && !debuggingSettings.debuggingBase)
    return;

  char dbg[220];
  snprintf(dbg, sizeof(dbg),
           "[HeightMap] phase=%s detail=%s state=%d idx=%u total=%u waitForOk=%d machineState=%d ready=%d scanInProgress=%d sensorReadyMs=%lu settleUntilMs=%lu",
           phase,
           detail,
           (int)heightMapScanState,
           (unsigned)heightMapNextPointIndex,
           (unsigned)heightMapTotalPoints,
           waitForOk,
           (int)currentMachineState,
           heightMapReady ? 1 : 0,
           heightMapScanInProgress ? 1 : 0,
           (unsigned long)heightMapSensorReadyMs,
           (unsigned long)heightMapSettleUntilMs);
  serialManager.USBSerialPrintln(dbg);

}

// Drops the map when the job XY zero moves.
//
// The map is anchored in JOB coordinates, captured when the scan started. If the work offset moves
// afterwards, those same numbers point somewhere else on the material: the map slides with the new
// zero while the stock stays put, and every compensation value is then read from the wrong place. The
// 3D view still looks perfect, because the data is fine -- only its registration to the work is wrong.
//
// The check lives on the WCO cache update because that is the one point every zero change passes
// through, whatever set it: the laser-offset-zero macro, the material-centre macro, an API command, or
// a G10/G92 typed by hand. Hooking the individual command paths would miss whichever one we forgot.
//
// Z is deliberately NOT checked. The stored values are deltas from the origin cell, so re-zeroing Z
// (re-probing after a tool change, say) shifts the cut depth uniformly and leaves the map itself valid.
static void checkHeightMapWorkOffsetChange() {

  if (!heightMapReady && !heightMapScanInProgress)
    return;

  if (fabsf(wcoX - heightMapWcoX) <= HEIGHT_MAP_WCO_CHANGE_TOLERANCE_MM &&
      fabsf(wcoY - heightMapWcoY) <= HEIGHT_MAP_WCO_CHANGE_TOLERANCE_MM)
    return;

  char dbg[160];
  snprintf(dbg, sizeof(dbg),
           "HeightMap dropped: job XY zero moved by (%.3f, %.3f) mm since the scan - rescan required",
           wcoX - heightMapWcoX, wcoY - heightMapWcoY);
  serialManager.USBSerialPrint(FROM_CPUB);
  serialManager.USBSerialPrintln(dbg);

  if (heightMapScanInProgress) {
    // Mid-scan there is nothing to salvage: the rows already stored are in the old frame.
    abortHeightMapScan("ZERO_CHANGED", "work_offset_moved");
    return;
  }

  invalidateHeightMap(false);
  heightMapCompensationEnabled = false;

}

// ─── Height map cell statistics ─────────────────────────────────

// Maps a physical grid position to its index in heightMapRelativeMm. Storage is in serpentine scan
// order: row 0 runs +X, row 1 runs -X, and so on, which is also the order the line scan fills them in.
static uint16_t hmPhysToScanIdx(uint16_t row, uint16_t col) {
  if (row % 2 == 0) return row * heightMapGridPointsX + col;
  return row * heightMapGridPointsX + (heightMapGridPointsX - 1 - col);
}

// Median of up to 9 floats (used by the height-map outlier filter). Sorts the array in place.
static float hmMedian(float* v, int n) {
  for (int i = 1; i < n; i++) {
    float x = v[i];
    int j = i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }
  return (n & 1) ? v[n / 2] : 0.5f * (v[n / 2 - 1] + v[n / 2]);
}

static bool hmCellValid(uint16_t idx) {
  return (heightMapCellValid[idx >> 3] & (uint8_t)(1u << (idx & 7))) != 0;
}

static void hmSetCellValid(uint16_t idx) {
  heightMapCellValid[idx >> 3] |= (uint8_t)(1u << (idx & 7));
}

static void hmClearAllCellValid() {
  memset(heightMapCellValid, 0, sizeof(heightMapCellValid));
}

// Sorts v in place and returns the mean of the middle (100 - 2*HEIGHT_MAP_CELL_TRIM_PERCENT)% of it.
// Trimming BOTH ends is what makes a cell stable on reflective stock: a specular glint reads short and
// a dropout reads long, both land at an extreme, and both are discarded outright -- where a plain mean
// would drag the cell toward them by enough to lift the tool clear of a shallow cut. spreadOut gets the
// range of the values that survived the trim, i.e. a per-cell noise figure.
static float hmTrimmedMean(float* v, uint16_t n, float* spreadOut) {

  for (uint16_t i = 1; i < n; i++) {
    float x = v[i];
    int j = (int)i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }

  uint16_t trim = (uint16_t)(((uint32_t)n * HEIGHT_MAP_CELL_TRIM_PERCENT) / 100u);
  uint16_t lo = trim;
  uint16_t hi = (uint16_t)(n - trim);
  if (hi <= lo) { lo = 0; hi = n; }   // too few samples to trim: use them all

  float sum = 0.0f;
  for (uint16_t i = lo; i < hi; i++)
    sum += v[i];

  if (spreadOut != nullptr)
    *spreadOut = v[hi - 1] - v[lo];

  return sum / (float)(hi - lo);

}

// Collapses the samples gathered for heightMapCellActiveCol into that cell. Called when the head
// crosses into the next column and once more when the row ends.
static void closeHeightMapCell() {

  if (heightMapCellActiveCol < 0 || heightMapCurrentRow >= heightMapGridPointsY) {
    heightMapCellSampleCount = 0;
    return;
  }

  if (heightMapCellSampleCount >= HEIGHT_MAP_CELL_MIN_SAMPLES) {

    uint16_t idx = hmPhysToScanIdx(heightMapCurrentRow, (uint16_t)heightMapCellActiveCol);
    float spread = 0.0f;
    heightMapRelativeMm[idx] = hmTrimmedMean(heightMapCellSamples, heightMapCellSampleCount, &spread);
    hmSetCellValid(idx);

    if (spread > heightMapMaxCellSpreadMm) {
      heightMapMaxCellSpreadMm = spread;
      heightMapNoisiestCellRow = heightMapCurrentRow;
      heightMapNoisiestCellCol = (uint16_t)heightMapCellActiveCol;
    }
    if (spread > HEIGHT_MAP_CELL_NOISY_SPREAD_MM)
      ++heightMapNoisyCells;
    if (heightMapMinCellSamples == 0 || heightMapCellSampleCount < heightMapMinCellSamples)
      heightMapMinCellSamples = heightMapCellSampleCount;

  }
  // Fewer than HEIGHT_MAP_CELL_MIN_SAMPLES: the cell stays EMPTY and is filled from its neighbours
  // once the scan finishes. It is deliberately NOT given the previous cell's value.

  heightMapCellSampleCount = 0;

}

// Fills cells with no real measurement from their valid neighbours, propagating over several passes so
// a hole closes from its rim inwards. Returns how many cells were filled. This replaces the legacy
// "carry the previous point's value" behaviour, which silently produced a flat patch that looked
// entirely plausible in the 3D view while the tool rode over uncut material.
static uint16_t fillHeightMapGaps() {

  static uint8_t pending[(HEIGHT_MAP_MAX_POINTS_PER_AXIS * HEIGHT_MAP_MAX_POINTS_PER_AXIS + 7) / 8];
  uint16_t filled = 0;

  for (int pass = 0; pass < HEIGHT_MAP_GAP_FILL_PASSES; pass++) {

    memset(pending, 0, sizeof(pending));
    bool any = false;

    for (int row = 0; row < (int)heightMapGridPointsY; row++) {
      for (int col = 0; col < (int)heightMapGridPointsX; col++) {

        uint16_t idx = hmPhysToScanIdx((uint16_t)row, (uint16_t)col);
        if (hmCellValid(idx)) continue;

        float sum = 0.0f;
        int n = 0;
        for (int dr = -1; dr <= 1; dr++) {
          for (int dc = -1; dc <= 1; dc++) {
            if (dr == 0 && dc == 0) continue;
            int r = row + dr, c = col + dc;
            if (r < 0 || r >= (int)heightMapGridPointsY || c < 0 || c >= (int)heightMapGridPointsX) continue;
            uint16_t nIdx = hmPhysToScanIdx((uint16_t)r, (uint16_t)c);
            if (!hmCellValid(nIdx)) continue;
            sum += heightMapRelativeMm[nIdx];
            n++;
          }
        }
        if (n == 0) continue;

        heightMapRelativeMm[idx] = sum / (float)n;
        // Marked valid only after the whole pass, so a hole fills symmetrically from its rim rather
        // than smearing along the iteration order.
        pending[idx >> 3] |= (uint8_t)(1u << (idx & 7));
        filled++;
        any = true;

      }
    }

    for (unsigned int i = 0; i < sizeof(pending); i++)
      heightMapCellValid[i] |= pending[i];

    if (!any)
      break;

  }

  return filled;

}

// Turns the raw per-cell distances left by the line scan into the finished map. Returns false, having
// already aborted the scan, if the result cannot be trusted.
static bool finalizeHeightMapLineScan() {

  uint16_t totalCells = (uint16_t)((uint32_t)heightMapGridPointsX * heightMapGridPointsY);
  if (totalCells == 0) {
    abortHeightMapScan("FINALIZE", "empty_grid");
    return false;
  }

  uint16_t validCells = 0;
  for (uint16_t idx = 0; idx < totalCells; idx++)
    if (hmCellValid(idx)) validCells++;

  // Nothing measured at all: the laser never produced a usable read anywhere on the work. Fail loudly
  // rather than hand back a map of zeroes that would read as a perfectly flat surface.
  if (validCells == 0) {
    logHeightMapScanDebug("FINALIZE", "no_valid_cells");
    abortHeightMapScan("FINALIZE", "no_valid_cells");
    return false;
  }

  heightMapCellsInterpolated = fillHeightMapGaps();

  // Anything the neighbour passes could not reach (a whole edge of dropouts) falls back to the mean of
  // the measured cells so the map stays continuous, and is counted as interpolated too.
  float sum = 0.0f;
  uint16_t n = 0;
  for (uint16_t idx = 0; idx < totalCells; idx++)
    if (hmCellValid(idx)) { sum += heightMapRelativeMm[idx]; n++; }
  float mapMean = (n > 0) ? (sum / (float)n) : 0.0f;

  for (uint16_t idx = 0; idx < totalCells; idx++) {
    if (hmCellValid(idx)) continue;
    heightMapRelativeMm[idx] = mapMean;
    hmSetCellValid(idx);
    heightMapCellsInterpolated++;
  }

  // The filter only ever compares a cell against its own neighbours, so running it on absolute
  // distances is equivalent to running it on relative ones.
  filterHeightMapOutliers();

  // The datum was measured standing still at job XY zero before the sweep (HEIGHT_MAP_SCAN_BASELINE_
  // SAMPLE), so it is independent of the map's own cells. Cells hold absolute distances until here;
  // subtracting the datum expresses every one of them as a height relative to the surface at job XY
  // zero, which is where Z gets touched off.
  if (!heightMapBaselineCaptured) {
    logHeightMapScanDebug("FINALIZE", "no_baseline");
    abortHeightMapScan("FINALIZE", "no_baseline");
    return false;
  }

  // Take the datum from the SWEPT CELLS at job (0,0) rather than from the stationary reading taken
  // before the sweep. Both refer to the same point, but the stationary read and the cells are acquired
  // differently -- parked versus moving at the row feed rate -- and any systematic difference between
  // those two conditions lands in every map value as a constant, which is a constant cutting-depth
  // error. Reading the datum out of the cells puts the same acquisition path on both sides of the
  // subtraction, so that difference cancels.
  //
  // The cells still hold absolute distances here, so interpolateHeightMapZ returns the absolute
  // distance at job (0,0), interpolated from the four cells around it -- roughly 80 readings rather
  // than the 15 the stationary measurement takes, and spread over a few mm so a local defect at job
  // zero (a touch-off dimple or the burr it raises) is diluted and trimmed instead of BEING the datum.
  //
  // Only valid if job (0,0) is actually inside the scanned rectangle: outside it interpolateHeightMapZ
  // clamps to the nearest edge cell and would silently return the wrong reference. In that case keep
  // the stationary datum and say so, since the constant offset can come back with it.
  const float datumMargin = HEIGHT_MAP_JOB_COVERAGE_TOLERANCE_MM;
  bool jobZeroInsideMap =
       (0.0f >= heightMapOriginX - datumMargin)
    && (0.0f <= heightMapOriginX + heightMapSizeXmm + datumMargin)
    && (0.0f <= heightMapOriginY + datumMargin)
    && (0.0f >= heightMapOriginY - heightMapSizeYmm - datumMargin);

  // Both branches read the datum out of the SWEPT CELLS, so it shares whatever bias the cells carry
  // and that bias cancels in `cell - datum`. The stationary reading is never the datum: it is acquired
  // differently (parked vs moving) and mixing the two puts a constant error into every map value.
  float datumMm;

  if (jobZeroInsideMap) {
    datumMm = interpolateHeightMapZ(0.0f, 0.0f);
    heightMapDatumFromCells = true;
  } else {
    // Job zero is outside the scanned rectangle, so there is no cell there to read. Fall back to the
    // map's own origin cell -- which means Z must be touched off at the SCAN'S START CORNER for this
    // map, not at job zero.
    datumMm = heightMapRelativeMm[hmPhysToScanIdx(0, 0)];
    heightMapDatumFromCells = false;
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("HeightMap: job zero is outside the scanned area - datum taken at the scan's start corner, so set Z zero THERE, not at job zero");
  }

  heightMapDatumBiasMm = datumMm - heightMapBaselineDistanceMm;

  for (uint16_t idx = 0; idx < totalCells; idx++)
    heightMapRelativeMm[idx] -= datumMm;

  // Sized for the full line: it silently truncated at 200 and lost the bias figure, which is the one
  // number that says whether the sweep reads differently from a parked measurement.
  char dbg[320];
  snprintf(dbg, sizeof(dbg),
           "HeightMap line scan: cells=%u measured=%u interpolated=%u samples=%lu dropped=%lu minCellSamples=%u maxCellSpread=%.3fmm at(%.1f,%.1f) noisyCells=%u readings[%.2f..%.2f] datum=%.3fmm(%s) parked=%.3fmm bias=%.3fmm",
           (unsigned)totalCells,
           (unsigned)validCells,
           (unsigned)heightMapCellsInterpolated,
           (unsigned long)heightMapSamplesTaken,
           (unsigned long)heightMapCellDroppedSamples,
           (unsigned)heightMapMinCellSamples,
           heightMapMaxCellSpreadMm,
           (double)((float)heightMapNoisiestCellCol * heightMapSpacingXmm),
           (double)(-(float)heightMapNoisiestCellRow * heightMapSpacingYmm),
           (unsigned)heightMapNoisyCells,
           heightMapMinReadingMm,
           heightMapMaxReadingMm,
           datumMm,
           heightMapDatumFromCells ? "cells" : "parked",
           heightMapBaselineDistanceMm,
           datumMm - heightMapBaselineDistanceMm);
  serialManager.USBSerialPrint(FROM_CPUB);
  serialManager.USBSerialPrintln(dbg);

  return true;

}

void handleHeightMapScan() {

  if (!heightMapScanInProgress)
    return;

  if (heightMapScanState == HEIGHT_MAP_SCAN_WAIT_SENSOR_READY) {
    if ((long)(millis() - heightMapSensorReadyMs) < 0)
      return;
    logHeightMapScanDebug("WAIT_SENSOR_READY", "sensor_ready");
#if HEIGHT_MAP_USE_LEGACY_GRID_SCAN
    heightMapScanState = HEIGHT_MAP_SCAN_MOVE;
#else
    // Every cell starts with no measurement; only a cell that actually collects
    // HEIGHT_MAP_CELL_MIN_SAMPLES reads is marked valid.
    hmClearAllCellValid();
    heightMapCurrentRow = 0;
    heightMapCellActiveCol = -1;
    heightMapCellSampleCount = 0;
    heightMapSamplesTaken = 0;
    heightMapCellDroppedSamples = 0;
    heightMapCellsInterpolated = 0;
    heightMapMinCellSamples = 0;
    heightMapMaxCellSpreadMm = 0.0f;
    heightMapNoisiestCellRow = 0;
    heightMapNoisiestCellCol = 0;
    heightMapNoisyCells = 0;
    heightMapMinReadingMm = 0.0f;
    heightMapMaxReadingMm = 0.0f;
    heightMapHasReading = false;
    heightMapBaselineCaptured = false;
    heightMapDatumFromCells = false;
    heightMapDatumBiasMm = 0.0f;
    heightMapBaselineSpreadMm = 0.0f;
    heightMapBaselineSampleCount = 0;
    heightMapBaselineAttempt = 0;
    // The datum is measured first, standing still at job XY zero, before any sweeping.
    heightMapScanState = HEIGHT_MAP_SCAN_MOVE_BASELINE;
#endif
    return;
  }

#if HEIGHT_MAP_USE_LEGACY_GRID_SCAN

  // ============ LEGACY POINT-BY-POINT GRID SCAN (kept for reinstatement) ============
  // Stops at every grid point, waits out HEIGHT_MAP_SCAN_SETTLE_DELAY_MS, then averages up to
  // HEIGHT_MAP_SAMPLE_RETRY_COUNT reads. Superseded by the line scan below; see Config.h.

  if (heightMapScanState == HEIGHT_MAP_SCAN_MOVE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("MOVE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      logHeightMapScanDebug("MOVE", "machine_not_idle_before_send");
    if (currentMachineState != IDLE)
      return;

    float targetX = 0.0f;
    float targetY = 0.0f;
    getHeightMapPointTarget(heightMapNextPointIndex, &targetX, &targetY);

    // The laser sensor sits behind the spindle by (xBackMm, yBackMm): laser = spindle - offset.
    // getHeightMapPointTarget returns the grid point in the TOOL frame (where compensation is
    // later applied). To measure the surface there, drive the SPINDLE to gridTarget + offset so
    // the LASER lands on the grid point. The stored map index/origin stay in the tool frame, so
    // interpolation during the job lines up with the cut.
    float scanX = targetX + laserOffsetZeroSettings.xBackMm;
    float scanY = targetY + laserOffsetZeroSettings.yBackMm;

    // Remember the target (work coords) so WAIT_IDLE can confirm the head actually arrives here
    // before sampling — an IDLE status alone can be the stale pre-move one.
    heightMapMoveTargetX = scanX;
    heightMapMoveTargetY = scanY;

    logHeightMapScanDebug("MOVE", "sending_move");

    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d", scanX, scanY, HEIGHT_MAP_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      logHeightMapScanDebug("MOVE", "schedule_failed");
      abortHeightMapScan("MOVE", "schedule_failed");
      return;
    }
    logHeightMapScanDebug("MOVE", "move_accepted");
    heightMapScanState = HEIGHT_MAP_SCAN_WAIT_IDLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_WAIT_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("WAIT_IDLE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      logHeightMapScanDebug("WAIT_IDLE", "machine_not_idle");
    if (currentMachineState != IDLE)
      return;
    // IDLE alone is not enough: currentMachineState is a polled value and can still read the stale
    // pre-move IDLE right after the move is sent (before the poll updates to Run). On the long first
    // move that samples point 0 before arrival and corrupts the whole map's baseline. Require the
    // reported work position (MPos - WCO) to actually reach the target. The position is parsed from
    // the same status reports as the state, so a stale IDLE carries the stale old position and fails
    // this check until a fresh report confirms arrival.
    if (fabsf((currentMachinePositionX - wcoX) - heightMapMoveTargetX) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM ||
        fabsf((currentMachinePositionY - wcoY) - heightMapMoveTargetY) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM) {
      logHeightMapScanDebug("WAIT_IDLE", "waiting_arrival");
      return;
    }
    logHeightMapScanDebug("WAIT_IDLE", "settle_start");
    heightMapSettleUntilMs = millis() + HEIGHT_MAP_SCAN_SETTLE_DELAY_MS;
    heightMapScanState = HEIGHT_MAP_SCAN_SETTLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_SETTLE) {
    if ((long)(millis() - heightMapSettleUntilMs) < 0)
      return;
    logHeightMapScanDebug("SETTLE", "settle_complete");
    heightMapSampleSumMm = 0.0f;
    heightMapSampleValidCount = 0;
    heightMapSampleAttempt = 0;
    heightMapNextSampleMs = 0;
    heightMapScanState = HEIGHT_MAP_SCAN_SAMPLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_SAMPLE) {

    // Non-blocking multi-sample averaging: take up to HEIGHT_MAP_SAMPLE_RETRY_COUNT reads spaced by
    // HEIGHT_MAP_SAMPLE_RETRY_DELAY_MS (the Modbus laser mis-reads / de-inits if hammered back-to-back)
    // and average the valid ones. This cuts the per-point noise that, on a shallow PCB cut, would lift
    // the tool off the work, and gives reflective spots several chances at a real read before carrying.
    if (heightMapNextSampleMs != 0 && (long)(millis() - heightMapNextSampleMs) < 0)
      return; // waiting out the inter-sample delay

    if (heightMapSampleAttempt < HEIGHT_MAP_SAMPLE_RETRY_COUNT) {
      float distanceMm = 0.0f;
      bool outOfRange = false;
      if (readDistanceLaserMm(&distanceMm, &outOfRange) && !outOfRange) {
        heightMapSampleSumMm += distanceMm;
        heightMapSampleValidCount++;
      } else {
        logHeightMapScanDebug("SAMPLE", outOfRange ? "sample_out_of_range_ignored" : "sample_read_failed_ignored");
      }
      heightMapSampleAttempt++;
      heightMapNextSampleMs = millis() + HEIGHT_MAP_SAMPLE_RETRY_DELAY_MS;
      return; // take the next read on a later loop pass
    }

    // All attempts done for this point — average the valid reads.
    bool hasValidSample = (heightMapSampleValidCount > 0);
    float representativeDistanceMm = hasValidSample
      ? (heightMapSampleSumMm / (float)heightMapSampleValidCount) : 0.0f;

    // Only the FIRST point is mandatory: it defines the baseline the whole map is relative to, so an
    // unreadable first point must abort. Any LATER point that can't be read (out of range, reflective
    // spot, hole, edge) must NOT kill the whole scan — carry the previous point's value and continue.
    if (!hasValidSample && heightMapNextPointIndex == 0) {
      logHeightMapScanDebug("SAMPLE", "no_valid_samples_abort_first_point");
      abortHeightMapScan("SAMPLE", "no_valid_samples_first_point");
      return;
    }

    if (heightMapNextPointIndex == 0)
      heightMapBaselineDistanceMm = representativeDistanceMm;

    if (heightMapNextPointIndex < (HEIGHT_MAP_MAX_POINTS_PER_AXIS * HEIGHT_MAP_MAX_POINTS_PER_AXIS)) {
      if (hasValidSample)
        // Sign is `distance - baseline`, matching the point-cloud scan and verified on hardware with a
        // 4mm gauge block: a higher surface yields a POSITIVE offset and a lower surface a NEGATIVE one,
        // so `Z_new = programmedZ + offset` correctly raises the tool over highs and lowers it over lows.
        // (The previous `baseline - distance` was inverted, driving Z the wrong way.)
        heightMapRelativeMm[heightMapNextPointIndex] = representativeDistanceMm - heightMapBaselineDistanceMm;
      else {
        // Unreadable non-first point: reuse the previous point's value so the map still completes.
        logHeightMapScanDebug("SAMPLE", "no_valid_samples_carry_previous");
        heightMapRelativeMm[heightMapNextPointIndex] = heightMapRelativeMm[heightMapNextPointIndex - 1];
      }
    }

    ++heightMapNextPointIndex;
    pushHeightMapScanProgressEvent();

    if (heightMapNextPointIndex >= heightMapTotalPoints) {
      logHeightMapScanDebug("SAMPLE", "all_points_scanned_return_origin");
      heightMapScanState = HEIGHT_MAP_SCAN_RETURN_ORIGIN;
      return;
    }

    heightMapScanState = HEIGHT_MAP_SCAN_MOVE;
    return;
  }

#else

  // ==================== LINE SCAN MODE (ACTIVE) ====================
  // One G1 per grid row at HEIGHT_MAP_LINE_SCAN_FEED_RATE_MM_MIN, with the laser polled on every
  // loop() pass while GRBL moves, instead of stopping and settling at each of the grid points.
  // X is dead reckoned from the row start time: the feed rate is constant and the acceleration ramp
  // at 300 mm/min is a fraction of one sample, so this avoids the status-poll latency that would
  // otherwise cluster samples. Each sample is binned into its nearest grid column, and a column is
  // collapsed into a trimmed mean the moment the head leaves it -- so a cell is tens of independent
  // reads rather than the 3 the legacy per-point scan took, at roughly half the wall clock.

  // ---- Z datum ----
  // Measured standing still at job XY zero, before any sweeping. Every value in the finished map is
  // expressed relative to this, so Z is touched off at job XY zero. Taking it from the map's own
  // origin cell (the previous behaviour) rested the whole map on a half-width row-end cell carrying
  // about half the samples of any other -- the weakest cell in the map.

  if (heightMapScanState == HEIGHT_MAP_SCAN_MOVE_BASELINE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("MOVE_BASELINE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE) {
      logHeightMapScanDebug("MOVE_BASELINE", "machine_not_idle_before_send");
      return;
    }

    // Drive the SPINDLE to the map's origin cell + laser offset, so the LASER sits on the first point
    // of the scan.
    //
    // This reading is the SENSOR RANGE CHECK, not the Z datum -- the datum is derived from the swept
    // cells in finalizeHeightMapLineScan. Taking it over job (0,0) instead required the laser to
    // physically reach job zero, and with a +9.21 mm Y offset against a machine whose Y ceiling is 0,
    // that refused nearly every scan on a soft limit no matter how small the patch. The map's own
    // origin is guaranteed reachable: the scan rectangle is soft-limit checked before the scan starts.
    // It is also the more useful place to check range, being on the surface actually being scanned.
    heightMapMoveTargetX = heightMapOriginX + laserOffsetZeroSettings.xBackMm;
    heightMapMoveTargetY = heightMapOriginY + laserOffsetZeroSettings.yBackMm;

    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d",
             heightMapMoveTargetX, heightMapMoveTargetY, HEIGHT_MAP_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      logHeightMapScanDebug("MOVE_BASELINE", "schedule_failed");
      abortHeightMapScan("BASELINE", "schedule_failed");
      return;
    }

    logHeightMapScanDebug("MOVE_BASELINE", "baseline_move_accepted");
    heightMapScanState = HEIGHT_MAP_SCAN_WAIT_BASELINE_IDLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_WAIT_BASELINE_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("WAIT_BASELINE_IDLE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    // A polled IDLE can still be the stale pre-move one; require confirmed arrival before reading,
    // or the datum would be measured somewhere on the way.
    if (fabsf((currentMachinePositionX - wcoX) - heightMapMoveTargetX) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM ||
        fabsf((currentMachinePositionY - wcoY) - heightMapMoveTargetY) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM) {
      logHeightMapScanDebug("WAIT_BASELINE_IDLE", "waiting_arrival");
      return;
    }

    // The per-cell buffer is free at this point in the scan, so the datum reuses it. Keeping
    // heightMapCellActiveCol at -1 makes closeHeightMapCell() a no-op if it is ever reached.
    heightMapCellActiveCol = -1;
    heightMapCellSampleCount = 0;
    heightMapBaselineAttempt = 0;
    heightMapNextSampleMs = 0;
    logHeightMapScanDebug("WAIT_BASELINE_IDLE", "baseline_sampling_start");
    heightMapScanState = HEIGHT_MAP_SCAN_BASELINE_SAMPLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_BASELINE_SAMPLE) {

    // Spaced reads: the Modbus laser mis-reads if hammered back to back.
    if (heightMapNextSampleMs != 0 && (long)(millis() - heightMapNextSampleMs) < 0)
      return;

    if (heightMapBaselineAttempt < HEIGHT_MAP_BASELINE_SAMPLE_COUNT) {

      float distanceMm = 0.0f;
      bool outOfRange = false;
      if (readDistanceLaserMm(&distanceMm, &outOfRange) && !outOfRange) {
        if (heightMapCellSampleCount < HEIGHT_MAP_CELL_MAX_SAMPLES)
          heightMapCellSamples[heightMapCellSampleCount++] = distanceMm;
        if (!heightMapHasReading) {
          heightMapMinReadingMm = distanceMm;
          heightMapMaxReadingMm = distanceMm;
          heightMapHasReading = true;
        } else {
          if (distanceMm < heightMapMinReadingMm) heightMapMinReadingMm = distanceMm;
          if (distanceMm > heightMapMaxReadingMm) heightMapMaxReadingMm = distanceMm;
        }
        ++heightMapSamplesTaken;
      } else {
        logHeightMapScanDebug("BASELINE_SAMPLE", outOfRange ? "read_out_of_range" : "read_failed");
      }

      ++heightMapBaselineAttempt;
      heightMapNextSampleMs = millis() + HEIGHT_MAP_SAMPLE_RETRY_DELAY_MS;
      return;

    }

    if (heightMapCellSampleCount < HEIGHT_MAP_CELL_MIN_SAMPLES) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("HeightMap refused: could not read the Z datum at job XY zero. Check the Z height and that the stock covers job zero.");
      logHeightMapScanDebug("BASELINE_SAMPLE", "not_enough_valid_readings");
      heightMapCellSampleCount = 0;
      abortHeightMapScan("BASELINE", "no_valid_readings");
      return;
    }

    // Same trimmed mean a cell uses: a stationary glint still has to be rejected.
    float baselineSpread = 0.0f;
    heightMapBaselineSampleCount = heightMapCellSampleCount;
    heightMapBaselineDistanceMm = hmTrimmedMean(heightMapCellSamples, heightMapCellSampleCount, &baselineSpread);
    heightMapBaselineSpreadMm = baselineSpread;
    heightMapBaselineCaptured = true;
    heightMapCellSampleCount = 0;

    // The sensor spans about +/-30 mm and tracks Z roughly 1:1, so a datum near either limit means the
    // surface will fall out of range somewhere on the work. Refuse now rather than after the sweep.
    if (fabsf(heightMapBaselineDistanceMm) > HEIGHT_MAP_START_READING_MAX_ABS_MM) {
      char dbg[200];
      snprintf(dbg, sizeof(dbg),
               "HeightMap refused: laser reads %.2f mm at job zero, outside +/-%.1f mm of mid-range. Jog Z until it reads near 0, then rescan.",
               heightMapBaselineDistanceMm, (double)HEIGHT_MAP_START_READING_MAX_ABS_MM);
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln(dbg);
      logHeightMapScanDebug("BASELINE_SAMPLE", "reading_near_range_limit");
      abortHeightMapScan("BASELINE", "reading_near_range_limit");
      return;
    }

    {
      char dbg[160];
      snprintf(dbg, sizeof(dbg),
               "HeightMap Z datum at job zero: %.3f mm from %u/%u reads, spread %.3f mm",
               heightMapBaselineDistanceMm,
               (unsigned)heightMapBaselineSampleCount,
               (unsigned)HEIGHT_MAP_BASELINE_SAMPLE_COUNT,
               heightMapBaselineSpreadMm);
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln(dbg);
    }

    heightMapCurrentRow = 0;
    heightMapScanState = HEIGHT_MAP_SCAN_MOVE_ROW_START;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_MOVE_ROW_START) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("MOVE_ROW_START", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE) {
      logHeightMapScanDebug("MOVE_ROW_START", "machine_not_idle_before_send");
      return;
    }

    // Boustrophedon: even rows run +X, odd rows run -X, which is also the serpentine order
    // hmPhysToScanIdx stores cells in, so the head never has to traverse back across the work.
    bool evenRow = (heightMapCurrentRow % 2 == 0);
    heightMapRowStartX = evenRow ? heightMapOriginX : (heightMapOriginX + heightMapSizeXmm);
    heightMapRowEndX   = evenRow ? (heightMapOriginX + heightMapSizeXmm) : heightMapOriginX;
    heightMapRowY      = heightMapOriginY - ((float)heightMapCurrentRow * heightMapSpacingYmm);

    // The laser sits behind the spindle by (xBackMm, yBackMm), so the SPINDLE is driven to
    // toolTarget + offset to put the LASER on the row. The map itself stays in the tool frame, which
    // is what the job-time interpolation expects.
    heightMapMoveTargetX = heightMapRowStartX + laserOffsetZeroSettings.xBackMm;
    heightMapMoveTargetY = heightMapRowY + laserOffsetZeroSettings.yBackMm;

    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d",
             heightMapMoveTargetX, heightMapMoveTargetY, HEIGHT_MAP_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      logHeightMapScanDebug("MOVE_ROW_START", "schedule_failed");
      abortHeightMapScan("MOVE_ROW_START", "schedule_failed");
      return;
    }

    logHeightMapScanDebug("MOVE_ROW_START", "row_start_move_accepted");
    heightMapScanState = HEIGHT_MAP_SCAN_WAIT_ROW_START_IDLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_WAIT_ROW_START_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("WAIT_ROW_START_IDLE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    // currentMachineState is polled, so a fresh IDLE can still be the stale pre-move one. The row
    // timing clock is only meaningful if the head really is at heightMapRowStartX when the row G1 is
    // issued -- starting it early would skew every dead-reckoned X in the row -- so require the
    // reported work position to confirm arrival first.
    if (fabsf((currentMachinePositionX - wcoX) - heightMapMoveTargetX) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM ||
        fabsf((currentMachinePositionY - wcoY) - heightMapMoveTargetY) > HEIGHT_MAP_ARRIVE_TOLERANCE_MM) {
      logHeightMapScanDebug("WAIT_ROW_START_IDLE", "waiting_arrival");
      return;
    }

    // The sensor window was already validated on the stationary datum reading at job zero, which is a
    // far more reliable check than a single reading taken here would be.
    heightMapScanState = HEIGHT_MAP_SCAN_START_ROW;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_START_ROW) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("START_ROW", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE) {
      logHeightMapScanDebug("START_ROW", "machine_not_idle_before_send");
      return;
    }

    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f F%d",
             heightMapRowEndX + laserOffsetZeroSettings.xBackMm, HEIGHT_MAP_LINE_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      logHeightMapScanDebug("START_ROW", "schedule_failed");
      abortHeightMapScan("START_ROW", "schedule_failed");
      return;
    }

    heightMapCellActiveCol = -1;
    heightMapCellSampleCount = 0;
    heightMapRowStartMs = millis();
    // Purely time based. Acceleration is negligible at this feed rate, so the row takes essentially
    // exactly sizeXmm / feed, and this avoids racing GRBL's near-instant "ok" against the 200 ms
    // cached machine-state poll.
    heightMapRowDurationMs = (uint32_t)(
      heightMapSizeXmm * 60000.0f / (float)HEIGHT_MAP_LINE_SCAN_FEED_RATE_MM_MIN + 0.5f);

    logHeightMapScanDebug("START_ROW", "row_move_accepted");
    heightMapScanState = HEIGHT_MAP_SCAN_SAMPLING;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_SAMPLING) {

    bool rowComplete = ((uint32_t)(millis() - heightMapRowStartMs) >= heightMapRowDurationMs);

    if (!rowComplete) {

      float distanceMm = 0.0f;
      bool outOfRange = false;
      if (readDistanceLaserMm(&distanceMm, &outOfRange) && !outOfRange) {

        // Time-based X dead reckoning, independently timestamped per read.
        float speedMmPerMs = (float)HEIGHT_MAP_LINE_SCAN_FEED_RATE_MM_MIN / 60000.0f;
        float direction = (heightMapRowEndX >= heightMapRowStartX) ? 1.0f : -1.0f;
        float capturedX = heightMapRowStartX
          + direction * speedMmPerMs * (float)(millis() - heightMapRowStartMs);

        int32_t col = (int32_t)floorf((capturedX - heightMapOriginX) / heightMapSpacingXmm + 0.5f);
        if (col < 0)
          col = 0;
        if (col > (int32_t)heightMapGridPointsX - 1)
          col = (int32_t)heightMapGridPointsX - 1;

        // Samples arrive in X order, so leaving a column means that column is finished: collapse it
        // now and keep only one cell's worth of samples in RAM for the whole scan.
        if (col != heightMapCellActiveCol) {
          closeHeightMapCell();
          heightMapCellActiveCol = col;
        }

        if (heightMapCellSampleCount < HEIGHT_MAP_CELL_MAX_SAMPLES)
          heightMapCellSamples[heightMapCellSampleCount++] = distanceMm;
        else
          ++heightMapCellDroppedSamples;

        if (!heightMapHasReading) {
          heightMapMinReadingMm = distanceMm;
          heightMapMaxReadingMm = distanceMm;
          heightMapHasReading = true;
        } else {
          if (distanceMm < heightMapMinReadingMm) heightMapMinReadingMm = distanceMm;
          if (distanceMm > heightMapMaxReadingMm) heightMapMaxReadingMm = distanceMm;
        }

        ++heightMapSamplesTaken;

      }

      return;

    }

    closeHeightMapCell();          // collapse the final column of the row
    heightMapCellActiveCol = -1;
    ++heightMapCurrentRow;
    // Cells resolved so far, so the existing points-based progress bars keep working unchanged.
    heightMapNextPointIndex = (uint16_t)((uint32_t)heightMapCurrentRow * heightMapGridPointsX);
    pushHeightMapScanProgressEvent();

    if (heightMapCurrentRow >= heightMapGridPointsY) {
      logHeightMapScanDebug("SAMPLING", "all_rows_scanned_return_origin");
      heightMapScanState = HEIGHT_MAP_SCAN_RETURN_ORIGIN;
    } else {
      heightMapScanState = HEIGHT_MAP_SCAN_MOVE_ROW_START;
    }
    return;
  }

#endif

  if (heightMapScanState == HEIGHT_MAP_SCAN_RETURN_ORIGIN) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("RETURN_ORIGIN", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }
    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      logHeightMapScanDebug("RETURN_ORIGIN", "machine_not_idle_before_send");
    if (currentMachineState != IDLE)
      return;

    logHeightMapScanDebug("RETURN_ORIGIN", "sending_return_move");

    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d", heightMapReturnX, heightMapReturnY, HEIGHT_MAP_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      logHeightMapScanDebug("RETURN_ORIGIN", "schedule_failed");
      abortHeightMapScan("RETURN_ORIGIN", "schedule_failed");
      return;
    }

    logHeightMapScanDebug("RETURN_ORIGIN", "return_move_accepted");
    heightMapScanState = HEIGHT_MAP_SCAN_WAIT_RETURN_IDLE;
    return;
  }

  if (heightMapScanState == HEIGHT_MAP_SCAN_WAIT_RETURN_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (heightMapWaitOkSinceMs == 0)
          heightMapWaitOkSinceMs = millis();
        else if ((long)(millis() - heightMapWaitOkSinceMs) >= HEIGHT_MAP_WAIT_OK_STALE_MS) {
          logHeightMapScanDebug("WAIT_RETURN_IDLE", "stale_waitForOk_cleared");
          waitForOk = 0;
          heightMapWaitOkSinceMs = 0;
        }
      } else {
        heightMapWaitOkSinceMs = 0;
      }
      return;
    }

    heightMapWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      logHeightMapScanDebug("WAIT_RETURN_IDLE", "machine_not_idle");
    if (currentMachineState != IDLE)
      return;

#if HEIGHT_MAP_USE_LEGACY_GRID_SCAN
    heightMapScanInProgress = false;
    heightMapScanState = HEIGHT_MAP_SCAN_IDLE;
    filterHeightMapOutliers();   // remove isolated spikes before the map goes live
#else
    // Gap fill, spike filter and baseline, in that order. Aborts internally (and reports why) if the
    // scan produced nothing usable, rather than publishing a map that only looks plausible.
    if (!finalizeHeightMapLineScan())
      return;
    heightMapScanInProgress = false;
    heightMapScanState = HEIGHT_MAP_SCAN_IDLE;
#endif
    heightMapReady = true;
    heightMapUsedForCurrentFile = false;
    arrayUtil.clearCharArray(heightMapBoundFile, SD_MAX_FILE_NAME_LENGTH);
    if (openedReadFileName[0] != NULL_CHAR)
      strncpy(heightMapBoundFile, openedReadFileName, SD_MAX_FILE_NAME_LENGTH - 1);
    pushHeightMapScanCompletedEvent(true);
    return;
  }

}

// ==================== Point Cloud Scan ====================

void pushPointCloudScanProgressEvent() {
  if (!jsonSettings.enableJsonResponse)
    return;
  InputSource savedSource = currentInputSource;
  currentInputSource = pointCloudInputSource;
  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_POINT_CLOUD_SCAN_PROGRESS_STR;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_SCANNED_STR] = pointCloudNextPointIndex;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_TOTAL_STR] = pointCloudTotalPoints;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_X_STR] = pointCloudGridPointsX;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_Y_STR] = pointCloudGridPointsY;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_CURRENT_ROW_STR] = pointCloudCurrentRow;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();
  currentInputSource = savedSource;
}

void pushPointCloudScanCompletedEvent(bool success, bool saved) {
  if (!jsonSettings.enableJsonResponse)
    return;
  InputSource savedSource = currentInputSource;
  currentInputSource = pointCloudInputSource;
  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_POINT_CLOUD_SCAN_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
  responseJsonBuffer[JSON_KEY_POINT_CLOUD_SAVED_STR] = saved;
  if (success) {
    responseJsonBuffer[JSON_KEY_POINT_CLOUD_FILENAME_STR] = pointCloudFilename;
    responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_X_STR] = pointCloudGridPointsX;
    responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_Y_STR] = pointCloudGridPointsY;
    responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_SCANNED_STR] = pointCloudNextPointIndex;
  } else {
    if (pointCloudAbortStage != nullptr)
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_STAGE_STR] = pointCloudAbortStage;
    if (pointCloudAbortDetail != nullptr)
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_DETAIL_STR] = pointCloudAbortDetail;
  }
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();
  currentInputSource = savedSource;
}

void resetPointCloudScanState() {
  pointCloudScanInProgress = false;
  pointCloudScanState = POINT_CLOUD_SCAN_IDLE;
  pointCloudNextPointIndex = 0;
  pointCloudSettleUntilMs = 0;
  pointCloudWaitOkSinceMs = 0;
  pointCloudSensorReadyMs = 0;
  pointCloudBaselineDistanceMm = 0.0f;
  pointCloudAbortStage = nullptr;
  pointCloudAbortDetail = nullptr;
  pointCloudVertexCountOffset = 0;
  pointCloudSyncCounter = 0;
  pointCloudCurrentRow = 0;
  pointCloudRowStartX = 0.0f;
  pointCloudRowEndX = 0.0f;
  pointCloudRowY = 0.0f;
  pointCloudRowStartMs = 0;
  pointCloudRowDurationMs = 0;
  pointCloudFileYear = 0;
  pointCloudFileMonth = 0;
  pointCloudFileDay = 0;
  pointCloudFileHour = 0;
  pointCloudFileMinute = 0;
  pointCloudFileSecond = 0;
}

void abortPointCloudScan(const char* stage, const char* detail) {
  pointCloudAbortStage = stage;
  pointCloudAbortDetail = detail;
  // Stop any motion currently in flight on GRBL (Ctrl-X soft reset) and clear the
  // pending-ack counter so the firmware does not keep waiting for an `ok` that the
  // cancelled G1 will never produce.
  serialManager.CPUASerialWrite(GRBL_STOP_RESET);
  waitForOk = 0;
  // Follow reset with an explicit unlock so controllers that enter Alarm/lock
  // after Ctrl-X can recover without requiring a separate API call.
  serialManager.CPUASerialPrintln(GRBL_UNLOCK);
  ++waitForOk;
  if (sdCardManager.isWriteFileOpen()) {
    // Rewrite actual (partial) vertex count so the file is a valid partial PLY
    char countStr[11];
    snprintf(countStr, sizeof(countStr), "%010lu", (unsigned long)pointCloudNextPointIndex);
    if (pointCloudVertexCountOffset > 0 && sdCardManager.seekWriteFile(pointCloudVertexCountOffset)) {
      sdCardManager.writeText(countStr);
      sdCardManager.syncWriteFile();
    }
    sdCardManager.closeWriteFile();
  }
  resetPointCloudScanState();
  pointCloudAbortStage = stage;
  pointCloudAbortDetail = detail;
  pushPointCloudScanCompletedEvent(false, false);
}

// Returns the scan-order (serpentine) index for physical grid position (row, col)
static uint32_t pcPhysToScanIdx(uint16_t row, uint16_t col) {
  if (row % 2 == 0) return (uint32_t)row * pointCloudGridPointsX + col;
  return (uint32_t)row * pointCloudGridPointsX + (pointCloudGridPointsX - 1 - col);
}

void getPointCloudPointTarget(uint32_t pointIndex, float* x, float* y) {
  if (x == nullptr || y == nullptr || pointCloudGridPointsX == 0)
    return;
  uint16_t row = (uint16_t)(pointIndex / pointCloudGridPointsX);
  uint16_t colInRow = (uint16_t)(pointIndex % pointCloudGridPointsX);
  uint16_t col = (row % 2 == 0) ? colInRow : (pointCloudGridPointsX - 1 - colInRow);
  *x = pointCloudOriginX + ((float)col * pointCloudSpacingMm);
  *y = pointCloudOriginY - ((float)row * pointCloudSpacingMm);
}

bool savePointCloudPlyHeader() {
  if (sdCardManager.fileExists(pointCloudFilename))
    sdCardManager.deleteFile(pointCloudFilename);
  if (!sdCardManager.createTextFile(pointCloudFilename))
    return false;
  if (!sdCardManager.openTextFileForWriting(pointCloudFilename))
    return false;
  if (pointCloudFileYear != 0)
    sdCardManager.setWriteFileTimestamp(pointCloudFileYear, pointCloudFileMonth, pointCloudFileDay, pointCloudFileHour, pointCloudFileMinute, pointCloudFileSecond);
  char line[128];
  sdCardManager.writeTextLine("ply");
  sdCardManager.writeTextLine("format ascii 1.0");
  sdCardManager.writeTextLine("comment AkkuraMini point cloud");
  snprintf(line, sizeof(line), "comment file=%s density=%s spacingXY=%.3f gridX=%u gridY=%u",
    pointCloudFilename, pointCloudDensityStr, pointCloudSpacingMm,
    pointCloudGridPointsX, pointCloudGridPointsY);
  sdCardManager.writeTextLine(line);
  // Flush buffer so position is accurate; record offset of the 10-digit count field
  pointCloudVertexCountOffset = sdCardManager.getWriteFilePosition() + 15; // "element vertex " is 15 chars
  sdCardManager.writeTextLine("element vertex 0000000000");
  sdCardManager.writeTextLine("property float x");
  sdCardManager.writeTextLine("property float y");
  sdCardManager.writeTextLine("property float z");
  sdCardManager.writeTextLine("end_header");
  return true;
}

void handlePointCloudScan() {
  if (!pointCloudScanInProgress)
    return;

  // ==================== LINE SCAN MODE (ACTIVE) ====================
  // Scans one full row at a time at POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN mm/min,
  // sampling the laser on every loop() call while GRBL is moving.
  // X position is read live from currentJobPositionX; Y is the exact row coordinate.
  // Point spacing is irregular (determined by Modbus read rate), but each point has
  // precise XYZ stored in the PLY file.

  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_SENSOR_READY) {
    if ((long)(millis() - pointCloudSensorReadyMs) < 0)
      return;
    // Z reference: the first valid in-scan sample is taken as Z=0; all subsequent points
    // are stored as offsets from it. Sentinel 0.0 means "not yet captured" — SAMPLING
    // sets it from the first successful read.
    pointCloudBaselineDistanceMm = 0.0f;
    if (!savePointCloudPlyHeader()) {
      abortPointCloudScan("WAIT_SENSOR_READY", "file_create_failed");
      return;
    }
    pointCloudCurrentRow = 0;
    pointCloudScanState = POINT_CLOUD_SCAN_START_ROW;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_START_ROW) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    // Even rows scan left→right; odd rows scan right→left (boustrophedon)
    bool evenRow = (pointCloudCurrentRow % 2 == 0);
    float rowStartX = evenRow ? pointCloudOriginX : (pointCloudOriginX + pointCloudSizeXmm);
    float rowEndX   = evenRow ? (pointCloudOriginX + pointCloudSizeXmm) : pointCloudOriginX;
    float rowY = pointCloudOriginY - ((float)pointCloudCurrentRow * pointCloudSpacingMm);
    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f F%d", rowEndX, POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      abortPointCloudScan("START_ROW", "schedule_failed");
      return;
    }
    // Record row geometry and start time for time-based X dead reckoning in SAMPLING
    pointCloudRowStartX = rowStartX;
    pointCloudRowEndX   = rowEndX;
    pointCloudRowY      = rowY;
    pointCloudRowStartMs = millis();
    // Row duration is purely time-based (acceleration negligible). No GRBL state polling.
    pointCloudRowDurationMs = (uint32_t)(
      pointCloudSizeXmm * 60000.0f / (float)POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN + 0.5f);
    pointCloudScanState = POINT_CLOUD_SCAN_SAMPLING;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_SAMPLING) {
    // Pure time-based row completion. Acceleration is negligible at 300 mm/min so the
    // machine reaches feed rate within a fraction of a sample, and finishes the row in
    // essentially exactly sizeXmm / feed ms. This avoids races between GRBL's near-instant
    // "ok" and the 200 ms cached machine-state polling cadence.
    bool rowComplete = ((uint32_t)(millis() - pointCloudRowStartMs) >= pointCloudRowDurationMs);

    if (!rowComplete) {
      // Machine is still moving — sample the laser
      float distanceMm = 0.0f;
      bool outOfRange = false;
      if (readDistanceLaserMm(&distanceMm, &outOfRange) && !outOfRange) {
        // Time-based X dead reckoning: avoids GRBL status-poll latency and clustering.
        // Each Modbus read (~15 ms) is independently timestamped; position is computed
        // from the known row start X, feed rate, and elapsed time since G1 was issued.
        float speedMmPerMs = (float)POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN / 60000.0f;
        float direction = (pointCloudRowEndX >= pointCloudRowStartX) ? 1.0f : -1.0f;
        float capturedX = pointCloudRowStartX
          + direction * speedMmPerMs * (float)(millis() - pointCloudRowStartMs);
        float capturedY = pointCloudRowY;
        // First valid sample of the whole scan defines Z=0; all later points are
        // offsets from it. Sign is `distance - baseline` (empirically the correct
        // orientation for this sensor / mounting): a surface farther than the first
        // point yields positive Z, closer yields negative Z.
        if (pointCloudBaselineDistanceMm == 0.0f) {
          pointCloudBaselineDistanceMm = distanceMm;
        }
        float pz = distanceMm - pointCloudBaselineDistanceMm;
        char pointLine[64];
        snprintf(pointLine, sizeof(pointLine), "%.3f %.3f %.3f", capturedX, capturedY, pz);
        if (!sdCardManager.writeTextLine(pointLine)) {
          abortPointCloudScan("SAMPLING", "write_failed");
          return;
        }
        ++pointCloudNextPointIndex;
        if (++pointCloudSyncCounter >= POINT_CLOUD_SYNC_INTERVAL) {
          pointCloudSyncCounter = 0;
          sdCardManager.syncWriteFile();
        }
      }
      return;
    }

    // Row complete — sync, fire progress, then advance to next row or finish
    sdCardManager.syncWriteFile();
    pointCloudSyncCounter = 0;
    ++pointCloudCurrentRow;
    pushPointCloudScanProgressEvent();
    if (pointCloudCurrentRow >= pointCloudGridPointsY)
      pointCloudScanState = POINT_CLOUD_SCAN_RETURN_ORIGIN;
    else
      pointCloudScanState = POINT_CLOUD_SCAN_MOVE_Y;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_MOVE_Y) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    float nextRowY = pointCloudOriginY - ((float)pointCloudCurrentRow * pointCloudSpacingMm);
    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 Y%.3f F%d", nextRowY, POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      abortPointCloudScan("MOVE_Y", "schedule_failed");
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_WAIT_Y_IDLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_Y_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    pointCloudScanState = POINT_CLOUD_SCAN_START_ROW;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_RETURN_ORIGIN) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    // Rewrite actual vertex count in PLY header before closing
    {
      char countStr[11];
      snprintf(countStr, sizeof(countStr), "%010lu", (unsigned long)pointCloudNextPointIndex);
      if (sdCardManager.seekWriteFile(pointCloudVertexCountOffset)) {
        sdCardManager.writeText(countStr);
        sdCardManager.syncWriteFile();
      }
    }
    sdCardManager.closeWriteFile();
    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d", pointCloudOriginX, pointCloudOriginY, POINT_CLOUD_LINE_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      abortPointCloudScan("RETURN_ORIGIN", "schedule_failed");
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_WAIT_RETURN_IDLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_RETURN_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    resetPointCloudScanState();
    pointCloudReady = true;
    pushPointCloudScanCompletedEvent(true, true);
    return;
  }

  // ==================== POINT-BY-POINT SCAN MODE (LEGACY, COMMENTED OUT) ====================
  /*
  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_SENSOR_READY) {
    if ((long)(millis() - pointCloudSensorReadyMs) < 0)
      return;
    if (!savePointCloudPlyHeader()) {
      abortPointCloudScan("WAIT_SENSOR_READY", "file_create_failed");
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_MOVE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_MOVE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    float targetX = 0.0f, targetY = 0.0f;
    getPointCloudPointTarget(pointCloudNextPointIndex, &targetX, &targetY);
    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d", targetX, targetY, POINT_CLOUD_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      abortPointCloudScan("MOVE", "schedule_failed");
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_WAIT_IDLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    pointCloudSettleUntilMs = millis() + POINT_CLOUD_SCAN_SETTLE_DELAY_MS;
    pointCloudScanState = POINT_CLOUD_SCAN_SETTLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_SETTLE) {
    if ((long)(millis() - pointCloudSettleUntilMs) < 0)
      return;
    pointCloudScanState = POINT_CLOUD_SCAN_SAMPLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_SAMPLE) {
    float representativeDistanceMm = 0.0f;
    bool hasValidSample = false;
    for (int sampleIndex = 0; sampleIndex < POINT_CLOUD_SAMPLE_COUNT; ++sampleIndex) {
      float distanceMm = 0.0f;
      bool outOfRange = false;
      if (!readDistanceLaserMm(&distanceMm, &outOfRange) || outOfRange)
        continue;
      representativeDistanceMm = distanceMm;
      hasValidSample = true;
      break;
    }
    if (!hasValidSample) {
      abortPointCloudScan("SAMPLE", "no_valid_samples");
      return;
    }

    if (pointCloudNextPointIndex == 0)
      pointCloudBaselineDistanceMm = representativeDistanceMm;

    uint16_t row = (uint16_t)(pointCloudNextPointIndex / pointCloudGridPointsX);
    uint16_t colInRow = (uint16_t)(pointCloudNextPointIndex % pointCloudGridPointsX);
    uint16_t col = (row % 2 == 0) ? colInRow : (pointCloudGridPointsX - 1 - colInRow);
    float px = pointCloudOriginX + ((float)col * pointCloudSpacingMm);
    float py = pointCloudOriginY - ((float)row * pointCloudSpacingMm);
    float pz = pointCloudBaselineDistanceMm - representativeDistanceMm;

    char pointLine[64];
    snprintf(pointLine, sizeof(pointLine), "%.3f %.3f %.3f", px, py, pz);
    if (!sdCardManager.writeTextLine(pointLine)) {
      abortPointCloudScan("SAMPLE", "write_failed");
      return;
    }

    ++pointCloudNextPointIndex;
    pushPointCloudScanProgressEvent();

    if (++pointCloudSyncCounter >= POINT_CLOUD_SYNC_INTERVAL) {
      pointCloudSyncCounter = 0;
      sdCardManager.syncWriteFile();
    }

    if (pointCloudNextPointIndex >= pointCloudTotalPoints) {
      pointCloudScanState = POINT_CLOUD_SCAN_RETURN_ORIGIN;
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_MOVE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_RETURN_ORIGIN) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    {
      char countStr[11];
      snprintf(countStr, sizeof(countStr), "%010lu", (unsigned long)pointCloudNextPointIndex);
      if (sdCardManager.seekWriteFile(pointCloudVertexCountOffset)) {
        sdCardManager.writeText(countStr);
        sdCardManager.syncWriteFile();
      }
    }
    sdCardManager.closeWriteFile();
    char cmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(cmd, sizeof(cmd), "G90 G1 X%.3f Y%.3f F%d", pointCloudOriginX, pointCloudOriginY, POINT_CLOUD_SCAN_FEED_RATE_MM_MIN);
    if (!scheduleOrSendCPUA(cmd)) {
      abortPointCloudScan("RETURN_ORIGIN", "schedule_failed");
      return;
    }
    pointCloudScanState = POINT_CLOUD_SCAN_WAIT_RETURN_IDLE;
    return;
  }

  if (pointCloudScanState == POINT_CLOUD_SCAN_WAIT_RETURN_IDLE) {
    if (waitForOk > 0) {
      if (currentMachineState == IDLE) {
        if (pointCloudWaitOkSinceMs == 0)
          pointCloudWaitOkSinceMs = millis();
        else if ((long)(millis() - pointCloudWaitOkSinceMs) >= POINT_CLOUD_WAIT_OK_STALE_MS) {
          waitForOk = 0;
          pointCloudWaitOkSinceMs = 0;
        }
      } else {
        pointCloudWaitOkSinceMs = 0;
      }
      return;
    }
    pointCloudWaitOkSinceMs = 0;
    if (currentMachineState != IDLE)
      return;
    resetPointCloudScanState();
    pointCloudReady = true;
    pushPointCloudScanCompletedEvent(true, true);
    return;
  }
  */
  // ==================== END POINT-BY-POINT SCAN MODE ====================
}

bool buildHashSidecarFilename(const char* filename, char* buffer, size_t bufferSize) {

  if (filename == nullptr || buffer == nullptr || bufferSize == 0)
    return false;

  int written = snprintf(buffer, bufferSize, "%s%s", filename, HASH_FILENAME_SUFFIX);
  return written > 0 && static_cast<size_t>(written) < bufferSize;

}

void deleteHashSidecarIfPresent(const char* filename) {

  char hashFilename[SD_MAX_FILE_NAME_LENGTH];
  arrayUtil.clearCharArray(hashFilename, SD_MAX_FILE_NAME_LENGTH);
  if (!buildHashSidecarFilename(filename, hashFilename, sizeof(hashFilename)))
    return;

  if (sdCardManager.fileExists(hashFilename))
    sdCardManager.deleteFile(hashFilename);

}

bool prepareHashSidecarForOverwrite(const char* filename, char* hashFilename, size_t hashFilenameSize) {

  if (!buildHashSidecarFilename(filename, hashFilename, hashFilenameSize))
    return false;

  if (!sdCardManager.fileExists(hashFilename))
    return true;

  if (!sdCardManager.deleteFile(hashFilename))
    return false;

  return !sdCardManager.fileExists(hashFilename);

}

bool readDistanceLaserMm(float* distanceMm, bool* isOutOfRange) {

  if(distanceMm == nullptr)
    return false;

  if(isOutOfRange != nullptr)
    *isOutOfRange = false;

  if(!isDistanceLaserSensorInitialized)
    return false;

  uint8_t result = distanceLaserSensorModbus.readInputRegisters(
    DISTANCE_LASER_SENSOR_MODBUS_INPUT_REGISTER,
    DISTANCE_LASER_SENSOR_MODBUS_REGISTER_COUNT
  );

  if(result != distanceLaserSensorModbus.ku8MBSuccess) {
    isDistanceLaserSensorInitialized = false;
    distanceLaserSensorInitRetries = 0;
    distanceLaserSensorProbeReadyMs = millis() + 2000;
    return false;
  }

#if INSTALLED_DISTANCE_LASER_SENSOR == LASER_SENSOR_NEW
  // One 32-bit signed value across two registers, big-endian.
  uint16_t rawHigh = distanceLaserSensorModbus.getResponseBuffer(0);
  uint16_t rawLow  = distanceLaserSensorModbus.getResponseBuffer(1);
  int32_t micrometers = (int32_t)(((uint32_t)rawHigh << 16) | (uint32_t)rawLow);
  micrometers *= DISTANCE_LASER_SENSOR_LSB_UM;
#else
  // One 16-bit signed register, scaled to the same micrometre units so the checks below are shared.
  int32_t micrometers = (int32_t)(int16_t)distanceLaserSensorModbus.getResponseBuffer(0)
                        * DISTANCE_LASER_SENSOR_LSB_UM;
#endif

  // The sensor's own validity rule comes first: it marks out of range with a specific value rather
  // than by saturating, and the GRK additionally treats anything beyond +/-9999 mm as invalid.
  if(micrometers == DISTANCE_LASER_SENSOR_INVALID_UM
     || micrometers >  DISTANCE_LASER_SENSOR_MAX_ABS_VALID_UM
     || micrometers < -DISTANCE_LASER_SENSOR_MAX_ABS_VALID_UM) {
    if(isOutOfRange != nullptr)
      *isOutOfRange = true;
    return false;
  }

  *distanceMm = ((float)micrometers) / 1000.0f;

  // Two-sided: the reading is an inverted distance and legitimately goes negative when the head is
  // high, so the guard has to bracket the range rather than reject the negative half.
  if(*distanceMm >= DISTANCE_LASER_SENSOR_MAX_VALID_MM
     || *distanceMm <= DISTANCE_LASER_SENSOR_MIN_VALID_MM) {
    if(isOutOfRange != nullptr)
      *isOutOfRange = true;
    return false;
  }

  return true;

}

bool renameHashSidecarIfPresent(const char* oldFilename, const char* newFilename) {

  char oldHashFilename[SD_MAX_FILE_NAME_LENGTH];
  char newHashFilename[SD_MAX_FILE_NAME_LENGTH];
  arrayUtil.clearCharArray(oldHashFilename, SD_MAX_FILE_NAME_LENGTH);
  arrayUtil.clearCharArray(newHashFilename, SD_MAX_FILE_NAME_LENGTH);

  if (!buildHashSidecarFilename(oldFilename, oldHashFilename, sizeof(oldHashFilename))
    || !buildHashSidecarFilename(newFilename, newHashFilename, sizeof(newHashFilename))) {
    return false;
  }

  if (!sdCardManager.fileExists(oldHashFilename))
    return true;

  if (sdCardManager.fileExists(newHashFilename))
    sdCardManager.deleteFile(newHashFilename);

  return sdCardManager.renameFile(oldHashFilename, newHashFilename);

}

void setup() {

  delay(START_DELAY); //this delay is needed to allow other devices of the PCB to boot correctly

  // compute board device ID at the start of setup so it is available to all code
  pico_unique_board_id_t boardUniqueId;
  pico_get_unique_board_id(&boardUniqueId);
  bool encoded = arrayUtil.encodeBytesToBase62(
      boardUniqueId.id, PICO_UNIQUE_BOARD_ID_SIZE_BYTES,
      boardDeviceId, sizeof(boardDeviceId)
  );
  if (!encoded) {
    pico_get_unique_board_id_string(boardDeviceId, sizeof(boardDeviceId));
  }

  //init of the default debugging settings, they will be overwritten by the settings from the EEPROM if they are present, otherwise the default settings will be saved to the EEPROM for future use
  debuggingSettings.debuggingBase = DEFAULT_DEBUGGING_BASE;
  debuggingSettings.debuggingSpindle = DEFAULT_DEBUGGING_SPINDLE;
  debuggingSettings.debuggingStatus = DEFAULT_DEBUGGING_STATUS;
  debuggingSettings.debuggingMacroBuffer = DEFAULT_DEBUGGING_MACRO_BUFFER;
  debuggingSettings.debuggingSDCard = DEFAULT_DEBUGGING_SD_CARD;
  debuggingSettings.debuggingJson = DEFAULT_DEBUGGING_JSON;
  debuggingSettings.debuggingESP32 = DEFAULT_DEBUGGING_ESP32;
  debuggingSettings.debuggingEEPROM = DEFAULT_DEBUGGING_EEPROM;
  debuggingSettings.debuggingJob = DEFAULT_DEBUGGING_JOB;
  debuggingSettings.benchmarking = DEFAULT_BENCHMARKING;
  debuggingSettings.benchmarkMinimumMs = DEFAULT_BENCHMARK_MINIMUM_MS;
  debuggingSettings.checkChar = EEPROM_CHECK_CHAR; //this char is used to check if the struct has been initialized or not, since we will read it from the EEPROM and if it's not initialized it will be filled with null chars
  
  //init default JsonSettings
  jsonSettings.enableJsonResponse = DEFAULT_ENABLE_JSON_RESPONSE;
  jsonSettings.checkChar = EEPROM_CHECK_CHAR; //this char is used to check if the struct has been initialized or not, since we will read it from the EEPROM and if it's not initialized it will be filled with null chars

  //init default wifi settings, they will be overwritten by the settings from the EEPROM if they are present, otherwise the default settings will be saved to the EEPROM for future use
  arrayUtil.clearCharArray(wifiSettings.apSsid, ACCESS_POINT_SSID_CHARS);
  arrayUtil.clearCharArray(wifiSettings.apPassword, ACCESS_POINT_PASSWORD_CHARS);
  arrayUtil.clearCharArray(wifiSettings.clientSsid, ACCESS_POINT_SSID_CHARS);
  arrayUtil.clearCharArray(wifiSettings.clientPassword, ACCESS_POINT_PASSWORD_CHARS);
  arrayUtil.clearCharArray(machineNameSettings.machineName, MACHINE_NAME_CHARS);
  arrayUtil.clearCharArray(firmwareVersion, FIRMWARE_VERSION_CHARS);
  arrayUtil.clearCharArray(mobileWebSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE); //clear the buffer for mobile websocket messages
  arrayUtil.clearCharArray(mobileServerName, ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE); //clear the mobile server name buffer
  arrayUtil.clearCharArray(jsonResponseCharBuffer, JSON_BUFFER_SIZE); //clear the buffer for the json responses that we will send to the websocket clients, this is needed to avoid sending old data in case we have a json response that is smaller than the previous one stored in the buffer, since we will only overwrite the portion of the buffer corresponding to the new json response, if the new json response is smaller than the previous one we need to clear the buffer to avoid sending old data in the portion of the buffer that is not overwritten by the new json response
  
  strcat(wifiSettings.apSsid, DEFAULT_ESP32C3_AP_SSID_PREFIX); // use prefix + device ID as default AP name
  strcat(wifiSettings.apSsid, boardDeviceId);
  strcat(wifiSettings.apPassword, DEFAULT_ESP32C3_AP_PASSWORD);
  strcat(wifiSettings.clientSsid, DEFAULT_ESP32C3_CLIENT_SSID);
  strcat(wifiSettings.clientPassword, DEFAULT_ESP32C3_CLIENT_PASSWORD);
  wifiSettings.hotspotEnabled = DEFAULT_HOTSPOT_ENABLED;
  wifiSettings.checkChar = EEPROM_CHECK_CHAR; //this char is used to check if the struct has been initialized or not, since we will read it from the EEPROM and if it's not initialized it will be filled with null chars
  strcat(machineNameSettings.machineName, DEFAULT_MACHINE_NAME);
  machineNameSettings.checkChar = EEPROM_CHECK_CHAR;
  toolLengthReferenceSettings.refZ      = BED_PROBE_REFERENCE_Z_MM;
  toolLengthReferenceSettings.checkChar = EEPROM_CHECK_CHAR;
  brushlessMotorSettings.directionReversed = BRUSHLESS_MOTOR_DIRECTION_REVERSED_DEFAULT;
  brushlessMotorSettings.checkChar = EEPROM_CHECK_CHAR;

  laserOffsetZeroSettings.xBackMm = LASER_OFFSET_ZERO_X_BACK_MM;
  laserOffsetZeroSettings.yBackMm = LASER_OFFSET_ZERO_Y_BACK_MM;
  laserOffsetZeroSettings.checkChar = EEPROM_CHECK_CHAR;

  //default housing state (no housing) — overwritten by EEPROM if a value was saved
  housingSettings.housingInstalled = DEFAULT_HOUSING_INSTALLED;
  housingSettings.allowMotionWhenDoorOpen = DEFAULT_ALLOW_MOTION_WHEN_DOOR_OPEN;
  housingSettings.checkChar = EEPROM_CHECK_CHAR;

  //default probe height — overwritten by EEPROM if a value was saved
  probeHeightSettings.heightMm  = DEFAULT_PROBE_HEIGHT_MM;
  probeHeightSettings.checkChar = EEPROM_CHECK_CHAR;

  strcat(firmwareVersion, DEFAULT_FIRMWARE_VERSION);
 
    //IO
  pinMode(RED_LED_OUTPUT_PIN, OUTPUT);
  pinMode(BLUE_LED_OUTPUT_PIN, OUTPUT);
  pinMode(GREEN_LED_OUTPUT_PIN, OUTPUT);
  pinMode(BLINK_LED_PIN, OUTPUT);
    pinMode(ENABLE_X_PIN, OUTPUT);
    pinMode(ENABLE_Y_PIN, OUTPUT);
    pinMode(ENABLE_Z_PIN, OUTPUT);
    pinMode(ENABLE_A_PIN, OUTPUT);
    pinMode(DISTANCE_LASER_SENSOR_DIR_PIN, OUTPUT);
    pinMode(LASER_SENSOR_ENABLE_PIN, OUTPUT);
    pinMode(DOOR_SENSOR_PIN, INPUT_PULLUP); // LOW = door closed (held to GND), HIGH = door open (released to internal pull-up)
    doorSensorOpenSinceMs = 0;
    pinMode(BRUSHLESS_MOTOR_BRAKE_PIN, INPUT_PULLUP);
    pinMode(BRUSHLESS_MOTOR_DIR_PIN, OUTPUT);
    digitalWrite(ENABLE_X_PIN, LOW);
    digitalWrite(ENABLE_Y_PIN, LOW);
    digitalWrite(ENABLE_Z_PIN, LOW);
    digitalWrite(ENABLE_A_PIN, LOW);
    digitalWrite(DISTANCE_LASER_SENSOR_DIR_PIN, LOW);
    digitalWrite(LASER_SENSOR_ENABLE_PIN, LOW);
    // Defaults apply here; EEPROM is read later in setup and re-applies the stored level.
    applyBrushlessMotorDirection();

    //Current sensor I2C init
    Wire.setSDA(CURRENT_SENSOR_SDA_PIN);
    Wire.setSCL(CURRENT_SENSOR_SCL_PIN);
    Wire.begin();
    isCurrentSensorInitialized = currentSensor.begin(&Wire);

  //Serial
  serialManager.init();

  serialManager.connectDistanceLaserSensor();
  distanceLaserSensorModbus.begin(DISTANCE_LASER_SENSOR_MODBUS_SLAVE_ID, serialManager.getDistanceLaserSensorSerial());
  distanceLaserSensorModbus.preTransmission(preDistanceLaserTransmission);
  distanceLaserSensorModbus.postTransmission(postDistanceLaserTransmission);
  // Schedule the initial probe after a 3-second non-blocking delay.
  requestDistanceLaserSensorInitialization(DISTANCE_LASER_SENSOR_INIT_DELAY_MS, false);

  //first connect to USBSerial to make sure the debugging messages are printed during the connection of the other serial ports
  if(serialManager.connectUSB())
    if (debuggingSettings.debuggingBase) { 
      
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("USB Serial connected"); 
    
    }

  //check settings from EEPROM and apply them
  //we should handle this first if we want to apply the debugging settings to the rest of the init phase
  handleEEPROMSettings();

  if(serialManager.connectCPUA())
    if (debuggingSettings.debuggingBase) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("CPU A Serial connected"); 
    }
  else
    if (debuggingSettings.debuggingBase) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("CPU A Serial connection failed"); 
    }

  if(serialManager.connectESP32())
    if (debuggingSettings.debuggingBase) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("ESP32 Serial connected"); 
    }  
  else
    if (debuggingSettings.debuggingBase) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("ESP32 Serial connection failed"); 
    }

  //Buffers
  resetUSBLine();
  resetCPUALine();
  arrayUtil.clearSyncActions(syncActions);
  arrayUtil.clear2DCharArray(bufferMacroLines);
  arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
  openedReadFileTotalLines = 0;
  arrayUtil.clearCharArray(downloadFileName, SD_MAX_FILE_NAME_LENGTH);
  arrayUtil.clearCharArray(downloadFileChunkBuffer, DOWNLOAD_FILE_CHUNK_BUFFER);
  arrayUtil.clearCharArray(transferFileName, SD_MAX_FILE_NAME_LENGTH);
  arrayUtil.clearCharArray(jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  arrayUtil.clearCharArray(gcodeLineBuffer, LINE_BUFFER_CHARACTERS);

  if (debuggingSettings.debuggingBase) { 
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("buffer arrays cleared"); 
  }

  //other inits
  sdCardManager.init();
  bool sdCardConnectedAtBoot = sdCardManager.connect();
  if(debuggingSettings.debuggingSDCard) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln(sdCardConnectedAtBoot ? "SD card connected" : "SD card connection failed");
  }

  // Load the persisted usage meter (hidden SD file) so it resumes from its last value.
  loadMachineUsage();

  esp32c3Manager.init(serialManager);

  //get the time from the finish of the init phase
  globalStartTime = millis();

  if(AUTO_HOMING_ON_STARTUP) {
    autoHomingPending = true;
    autoHomingScheduledMs = millis() + AUTO_HOMING_DELAY_MS;
  }

}

void updateRotaryAxisDetection() {
  
  if(!isCurrentSensorInitialized)
    return;

  float currentmA = currentSensor.getCurrent_mA();

  // Hysteresis: < 60mA = not detected, > 120mA = detected
  if(currentmA < 60.0f) {
    isRotaryAxisConnected = false;
  } else if(currentmA > 120.0f) {
    isRotaryAxisConnected = true;
  }
  // Between 60-120mA: maintain current state (hysteresis)

}

////////////MAIN LOOP////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

void loop() {

  if (debuggingSettings.benchmarking)
    benchmark.setStartTime(millis());

  //todo to implement these commented functions
  //handleSpindleRamping();
  handleSerialClientWatchdog();
  handleJogWatchdog();
  handleFileTransferTimeouts();

  readFromUSB();
  handleRGBLed();
  readFromCPUA();
  handleAutoHoming();
  handleManualSpindleRamp();
  handleHeldSpindleLineRelease();
  handlePauseFeedDrop();
  handlePendingPauseSpindleStop();
  handleResumeSpindleOverrideRamp();
  handleResumeFeedRamp();
  handleDoorSensor();
  handleVirtualDoorSignal();
  handleMachineUsage();
  handleSyncActions();
  handleMacros();
  blinkLed();
  askGRBLStatus();
  handleESP32Connection();
  handleESP32Comms();
  handleJob();
  updateRotaryAxisDetection();
  handleDistanceLaserSensorInit();
  handleLaserEdgeProbing();
  handleLaserMaterialBoundsProbing();
  handleHeightMapScan();
  handlePointCloudScan();

  if (debuggingSettings.benchmarking){

    benchmark.setStopTime(millis());

  if(benchmark.getElapsedTime(debuggingSettings.benchmarkMinimumMs) != -1){

      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("loop time in ms ");
      serialManager.USBSerialPrintln(benchmark.getElapsedTime(debuggingSettings.benchmarkMinimumMs));

  }

  }
}

/////////////////////////IMPLEMENTATION METHODS////////////////////////////////
void handleDistanceLaserSensorInit() {
  if (!distanceLaserSensorInitRequested) return;
  if (isDistanceLaserSensorInitialized) return;
  if (distanceLaserSensorInitRetries >= DISTANCE_LASER_SENSOR_MAX_INIT_RETRIES) return;

  digitalWrite(LASER_SENSOR_ENABLE_PIN, HIGH);

  if (millis() < distanceLaserSensorProbeReadyMs) return;

  uint8_t probeResult = distanceLaserSensorModbus.readInputRegisters(
    DISTANCE_LASER_SENSOR_MODBUS_INPUT_REGISTER,
    DISTANCE_LASER_SENSOR_MODBUS_REGISTER_COUNT
  );
  if (probeResult == distanceLaserSensorModbus.ku8MBSuccess) {
    isDistanceLaserSensorInitialized = true;
    hasDistanceLaserSensorPresence = true;
    distanceLaserSensorInitRetries = 0;
    digitalWrite(LASER_SENSOR_ENABLE_PIN, distanceLaserSensorKeepEnabledAfterInit ? HIGH : LOW);
  } else {
    distanceLaserSensorInitRetries++;
    distanceLaserSensorProbeReadyMs = millis() + 2000;
  }
}

void handleEEPROMSettings(){

  //eepromManager.clearEEPROM(); //do this if EEPROM is messed up!

  //debuggingSettings
  //this should go fisrt because it contains the debugging flags that we want to apply to the rest of the init phase
  DebuggingSettings tmpDebuggingSettings = eepromManager.readDebuggingSettings();

  if(tmpDebuggingSettings.checkChar == NULL_CHAR){



  }
  
  if(tmpDebuggingSettings.checkChar == EEPROM_CHECK_CHAR){

      debuggingSettings = tmpDebuggingSettings;

      if(debuggingSettings.debuggingEEPROM) { 
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Debugging settings found in EEPROM, applying settings"); 
      }

  }else{

    if(debuggingSettings.debuggingEEPROM) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("No debugging settings found in EEPROM, using default settings"); 
    }

    eepromManager.saveDebuggingSettings(debuggingSettings);

  }


  //wifiSettings
  WifiSettings tmpWifiSettings;
  tmpWifiSettings = eepromManager.readWiFiSettings();
    
  if(tmpWifiSettings.checkChar == EEPROM_CHECK_CHAR){

    wifiSettings = tmpWifiSettings;

    // Guard against uninitialized hotspotEnabled byte from older EEPROM layouts:
    // any value other than 0 (false) is treated as enabled (true).
    if(wifiSettings.hotspotEnabled != false) wifiSettings.hotspotEnabled = true;

    initialESP32ExpectedResponses = wifiSettings.hotspotEnabled ? 6 : 5;

    if(debuggingSettings.debuggingEEPROM) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("WiFi settings found in EEPROM, applying settings"); 
    }
    
  }else{

    if(debuggingSettings.debuggingEEPROM) { 
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("No WiFi settings found in EEPROM, using default settings"); 
    }
    
    eepromManager.saveWiFiSettings(wifiSettings);

  }

  //JsonSettings
  //we can add more settings here in the future if needed
  JsonSettings tmpJsonSettings;
  tmpJsonSettings = eepromManager.readJsonSettings();
  
  if(tmpJsonSettings.checkChar == EEPROM_CHECK_CHAR){

      jsonSettings = tmpJsonSettings;

      if(debuggingSettings.debuggingEEPROM) { 
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("JSON settings found in EEPROM, applying settings"); 
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) { 
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No JSON settings found in EEPROM, using default settings"); 
      }

      eepromManager.saveJsonSettings(jsonSettings);

    }

  MachineNameSettings tmpMachineNameSettings;
  tmpMachineNameSettings = eepromManager.readMachineNameSettings();

  if(tmpMachineNameSettings.checkChar == EEPROM_CHECK_CHAR && tmpMachineNameSettings.machineName[0] != NULL_CHAR){

      machineNameSettings = tmpMachineNameSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Machine name found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No machine name found in EEPROM, using default settings");
      }

      eepromManager.saveMachineNameSettings(machineNameSettings);

    }

  ToolLengthReferenceSettings tmpToolLengthReferenceSettings;
  tmpToolLengthReferenceSettings = eepromManager.readToolLengthReferenceSettings();

  if(tmpToolLengthReferenceSettings.checkChar == EEPROM_CHECK_CHAR){

      toolLengthReferenceSettings = tmpToolLengthReferenceSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Tool length reference settings found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No tool length reference settings found in EEPROM, using default settings");
      }

      eepromManager.saveToolLengthReferenceSettings(toolLengthReferenceSettings);

    }

  LaserOffsetZeroSettings tmpLaserOffsetZeroSettings;
  tmpLaserOffsetZeroSettings = eepromManager.readLaserOffsetZeroSettings();

  if(tmpLaserOffsetZeroSettings.checkChar == EEPROM_CHECK_CHAR){

      laserOffsetZeroSettings = tmpLaserOffsetZeroSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Laser offset zero settings found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No laser offset zero settings found in EEPROM, using default settings");
      }

      eepromManager.saveLaserOffsetZeroSettings(laserOffsetZeroSettings);

    }

  BrushlessMotorSettings tmpBrushlessMotorSettings;
  tmpBrushlessMotorSettings = eepromManager.readBrushlessMotorSettings();

  if(tmpBrushlessMotorSettings.checkChar == EEPROM_CHECK_CHAR){

      brushlessMotorSettings = tmpBrushlessMotorSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Brushless motor settings found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No brushless motor settings found in EEPROM, using default settings");
      }

      eepromManager.saveBrushlessMotorSettings(brushlessMotorSettings);

    }

  // The pin was driven to the default during IO setup; re-apply now that the stored level is known.
  applyBrushlessMotorDirection();

  HousingSettings tmpHousingSettings;
  tmpHousingSettings = eepromManager.readHousingSettings();

  if(tmpHousingSettings.checkChar == EEPROM_CHECK_CHAR){

      housingSettings = tmpHousingSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Housing settings found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No housing settings found in EEPROM, using default settings");
      }

      eepromManager.saveHousingSettings(housingSettings);

    }

  ProbeHeightSettings tmpProbeHeightSettings;
  tmpProbeHeightSettings = eepromManager.readProbeHeightSettings();

  if(tmpProbeHeightSettings.checkChar == EEPROM_CHECK_CHAR){

      probeHeightSettings = tmpProbeHeightSettings;

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("Probe height settings found in EEPROM, applying settings");
      }

    }else{

      if(debuggingSettings.debuggingEEPROM) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln("No probe height settings found in EEPROM, using default settings");
      }

      eepromManager.saveProbeHeightSettings(probeHeightSettings);

    }


}

bool handleESP32Config() {

 

  //here we attempt to connect the ESP32C3 to a WiFi network
  //if connection fails we still assess the initial execution as successful, since the ESP32C3 can be used in access point mode
  /*
  
  bool connectToWiFi = false;
  startUpExection = true;

  while(startUpExection){

    if(!connectToWiFi){

      

    if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY){

      responseType = esp32c3Manager.getResponseType();
      arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
      esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);

      startUpExection = false; //we have successfully connected to the WiFi network, we can exit the startup execution phase

       switch (responseType)
    {
      case ESP32C3_RESPONSE_TYPE_OK:

        
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("OK response received from ESP32C3");}
        
        break;
      
      case ESP32C3_RESPONSE_TYPE_UNKNOWN:

        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Unknown response received from ESP32C3 ");}

      break;
      
      case ESP32C3_RESPONSE_TYPE_ERROR:

        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Error response received from ESP32C3 ");}

      break;

      default:
        break;

    }

  }
  
    delay(ESP32C3_CHECK_DELAY);

  }
 */
  return true;
 

}

void handleSerialClientWatchdog() {

  if(!isClientConnected || !hasSerialPingTimedOut())
    return;

  isClientConnected = false;
  serialLastApiPingMs = 0;

  // If the USB client disappears while a job is running, pause via grblHAL parking
  // (safety door 0x84) so the spindle stops and Z lifts — consistent with the API pause.
  // Guard on RUN: during a pause/resume the machine sits in HOLD (grbl reports Door) for
  // several seconds — long enough for a ping to lapse. Without this check the watchdog
  // would fire a second, spurious 0x84 there, re-parking mid-resume ([MSG:Check Door]).
  if(isJobRunning && !isJobPaused && currentMachineState == RUN){
    resetManualSpindleRampState();
    serialManager.CPUASerialWrite(static_cast<byte>(GRBL_SAFETY_DOOR));
    currentMachineState = HOLD;
    isJobPaused = true;
  }

  if (debuggingSettings.debuggingBase) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("Serial client watchdog timeout, setting serial client connection status to false");
  }

}

// Safety net for press-and-hold jogging. The app streams incremental "jog" steps while
// the button is held and sends "jogCancel" on release. If that release message is lost or
// the link drops mid-jog, this stops motion automatically once no jog step has been seen
// for JOG_KEEPALIVE_TIMEOUT_MS, by sending the GRBL real-time jog-cancel byte (0x85).
void handleJogWatchdog() {

  if(!isJogging)
    return;

  if(millis() - jogLastActivityMs < JOG_KEEPALIVE_TIMEOUT_MS)
    return;

  serialManager.CPUASerialWrite(GRBL_JOG_CANCEL); // 0x85 is a realtime command, no 'ok' response
  isJogging = false;

  if (debuggingSettings.debuggingBase) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("Jog keep-alive timeout, cancelling jog");
  }

}

// Commits the single long jog move that backs a press-and-hold (see the JOG_COMMAND case).
// The move runs all the way to the soft-limit in the requested direction(s), so GRBL
// accelerates a single time and holds feed for smooth continuous motion — no per-step
// deceleration/stutter — and it is stopped instantly on release by the GRBL_JOG_CANCEL
// (0x85) realtime byte (see the jogCancel command and handleJogWatchdog). Unlike normal
// moves, the jog line deliberately bypasses the macro buffer and the CPUB modal soft-limit
// tracker (evaluateAndApplySoftLimitsForOutgoingLine); the distance is already clamped to
// the soft-limit by the caller. Sent only when the serial channel is idle (waitForOk <= 0)
// so the $J= line and its 'ok' stay in sync with the rest of the command flow; isJogging and
// jogLastActivityMs arm the keep-alive watchdog that auto-cancels if keep-alive jog messages
// stop arriving mid-hold.
void sendJogStep(const char* jogCmd) {

  if(waitForOk <= 0) {
    serialManager.CPUASerialPrintln(jogCmd);
    ++waitForOk;
  }

  isJogging = true;
  jogLastActivityMs = millis();

}

void handleFileTransferTimeouts() {

  if(isFileTransferInProgress
    && fileTransferLastChunkActivityMs != 0
    && (millis() - fileTransferLastChunkActivityMs) >= FILE_TRANSFER_CHUNK_TIMEOUT_MS) {

    if(debuggingSettings.debuggingJson) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("Upload transfer chunk timeout, aborting transfer. Lines still expected (linesRemaining): ");
      serialManager.USBSerialPrintln(transferFileLines);
    }

    // Notify the client that started the upload so it learns of the abort explicitly,
    // instead of only inferring it from its own timeout. Includes the leftover line count,
    // which points straight at a filelines/line-count mismatch (e.g. blank lines).
    if(jsonSettings.enableJsonResponse){
      InputSource savedSource = currentInputSource;
      currentInputSource = fileTransferInputSource;
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_TRANSFER_TIMEOUT_STR;
      responseJsonBuffer[JSON_KEY_LINES_REMAINING_STR] = transferFileLines;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
      currentInputSource = savedSource;
    }

    resetFileTransferState(true);
  }

  if(isFileDownloadInProgress
    && fileDownloadLastChunkActivityMs != 0
    && (millis() - fileDownloadLastChunkActivityMs) >= FILE_DOWNLOAD_CHUNK_TIMEOUT_MS) {

    if(debuggingSettings.debuggingJson) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Download transfer chunk timeout, aborting transfer");
    }

    resetFileDownloadState();
  }

}

void handleAutoHoming() {

  if(!autoHomingPending)
    return;

  if(millis() < autoHomingScheduledMs)
    return;

  autoHomingPending = false;
  scheduleOrSendCPUA(GRBL_HOMING);

}

void readFromUSB() {

  while(serialManager.availableFromUSB() > 0){

    int readUSB = serialManager.readFromUSB();

    if (readUSB != -1) {

      isClientConnected = true;
      serialLastApiPingMs = millis();

      bufferByteUSB = (byte)readUSB;
      bufferCharUSB = (char)bufferByteUSB;

      if (debuggingSettings.debuggingBase) { 
        
        serialManager.USBSerialPrint(FROM_CPUB); 
        serialManager.USBSerialPrint(" received from USB: "); 
        serialManager.USBSerialPrintln(bufferByteUSB); 

        }

        USBLineArray[USBLinePos] = bufferCharUSB;
        ++USBLinePos;

        if (bufferCharUSB == NEWLINE) {
          handleUSBLine();
          resetUSBLine();
        }

  }

  } 

}

void readFromCPUA(){

  while(serialManager.availableFromCPUA() > 0){

  int readCPUA = serialManager.readFromCPUA();

  if (readCPUA != -1) {


    bufferByteCPUA = (byte)readCPUA;
    bufferCharCPUA = (char)bufferByteCPUA;

    if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrint(FROM_CPUA); }
    if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln(bufferByteCPUA); }


    if (grblUtil.checkGRBLSpecialChar(bufferCharCPUA) && grblUtil.checkGBRLSpecialByte(bufferByteCPUA)) {

      if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln("special byte from CPU A"); }

      serialManager.USBSerialWrite(bufferByteCPUA);
      resetCPUALine();

    } else {

      // While a job is streaming, '\r' also ends the line (stored as '\n' so the console passthrough
      // keeps its newline): if noise eats the '\n' of "ok\r\n", the ok would otherwise be glued to the
      // next status report and lost, leaving waitForOk stuck and the job hung. The lone "\n" that
      // follows a normal "\r" is then an empty line and is ignored.
      if (isJobRunning && bufferCharCPUA == RETURN)
        bufferCharCPUA = NEWLINE;

      CPUALineArray[CPUALinePos] = bufferCharCPUA;
      ++CPUALinePos;

      // Never write past the line buffer: a line that long (noise, lost line endings) is handled
      // as it is, keeping the last byte for the string terminator.
      if (bufferCharCPUA == NEWLINE || CPUALinePos >= CPUA_LINE_BUFFER_CHARACTERS - 1) {

        handleCPUALine();
        resetCPUALine();
      }
    }
  }
  }
}

void handleSyncActions() {

  //here check sync actions, remember that if a cancel command arrives for GRBL, the sync actions must be cleared
  syncActionIndex = arrayUtil.firstSyncAction(syncActions);

  if (syncActionIndex >= 0) {

    //trackingPosition = true;
    // don't ask for the machine status if it is disabled
    
    //askGRBLStatus(syncActions[syncActionIndex].pollingInterval);
    
    //if a position has been determined thanks to position tracking
    //check if the position is in range

    if (abs(currentMachinePositionX - syncActions[syncActionIndex].positionX) <= syncActions[syncActionIndex].positionRange)
      if (abs(currentMachinePositionY - syncActions[syncActionIndex].positionY) <= syncActions[syncActionIndex].positionRange)
        if (abs(currentMachinePositionZ - syncActions[syncActionIndex].positionZ) <= syncActions[syncActionIndex].positionRange) {

          arrayUtil.clearSyncAction(syncActions[syncActionIndex]);
          resetCurrentPostions();

        }

    //if position is in range, execute action, eliminate the action
    //after the action is fulfilled deactivate the position tracking

    //}
  }

}

void handleMacros(){

  if(isJobRunning)
    return; //if a job is running we do not execute macros, since they are meant to be executed outside of a job context, if we execute them during a job it could cause problems with the job execution and with the state tracking of the machine, since the macros can contain movements that can change the position of the machine without the knowledge of the job execution logic  

  if(isJobPaused)
    return; //if the job is paused we do not execute macros, since they are meant to be executed outside of a job context, if we execute them during a paused job it could cause problems with the state tracking of the machine, since the macros can contain movements that can change the position of the machine without the knowledge of the job execution logic, and also it could cause problems with the resuming of the job execution, since when we resume a paused job we assume that the machine is in the same state as when it was paused, if we execute macros during the pause it could change the state of the machine and make it different from when it was paused, causing problems when resuming the job execution   

  if(!isMacroRunning)
    return;

  if(waitForOk > 0)
    return;

    //every once in a while according to the timer we should ask for the status to GRBL
  //when we do this we skip the next lines sending for the next loop cycle(s)
  if(timerUtil.statusTimer()){

    serialManager.CPUASerialWrite(GRBL_STATUS_QUERY); // '?' is a realtime command, no 'ok' response
    return;

  }

  //here we deal with writing the macro lines that are present in the buffer, ready to be written to teensy
  bufferMacroLineIndex = arrayUtil.firstMacroBufferLine(bufferMacroLines);

  if (bufferMacroLineIndex >= 0) {

    // Homing commands are system commands that cannot be queued behind in-progress motion.
    // Wait until the machine is idle before sending them to avoid GRBL error:8 (cycle active).
    if (isHomingCommandLine(bufferMacroLines[bufferMacroLineIndex]) && currentMachineState != IDLE)
      return;

    // Spindle-start gate: a macro must not start the spindle with the lid open. The macro's
    // remaining lines assume it is running (the next move would cut with a dead tool), so the
    // whole macro is aborted here, exactly like a soft-limit rejection below.
    if (isSpindleStartBlocked() && lineCommandsSpindleOn(bufferMacroLines[bufferMacroLineIndex])) {
      {
        char dbg[128];
        snprintf(dbg, sizeof(dbg), "Macro aborted, spindle start blocked (lid open or job paused) at line: %s", bufferMacroLines[bufferMacroLineIndex]);
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln(dbg);
      }
      arrayUtil.clear2DCharArray(bufferMacroLines);
      if (isProbeZ0MacroRunning)
        pushProbeZ0CompletedEvent(false);
      if (isProbeToolLengthMacroRunning)
        pushProbeToolLengthCompletedEvent(false);
      isRotaryYLockBypassed = false;
      isMacroRunning = false;
      return;
    }

    if(!evaluateAndApplySoftLimitsForOutgoingLine(bufferMacroLines[bufferMacroLineIndex])) {
      // A soft-limit rejection here silently kills the whole macro with no motion and no error to the
      // app (the success ack was already sent when the macro was built). Log the offending line so a
      // dropped move — e.g. the move-to-laser-offset head move computed out of range after the XY0
      // probe's reset — is visible instead of looking like "the head just didn't move".
      {
        char dbg[112];
        snprintf(dbg, sizeof(dbg), "Macro aborted by soft limits at line: %s", bufferMacroLines[bufferMacroLineIndex]);
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln(dbg);
      }
      arrayUtil.clear2DCharArray(bufferMacroLines);
      if (isProbeZ0MacroRunning)
        pushProbeZ0CompletedEvent(false);
      if (isProbeToolLengthMacroRunning)
        pushProbeToolLengthCompletedEvent(false);
      isRotaryYLockBypassed = false;
      isMacroRunning = false;
      return;
    }

    serialManager.CPUASerialPrintln(bufferMacroLines[bufferMacroLineIndex]);
    ++waitForOk;

    if(debuggingSettings.debuggingMacroBuffer) { serialManager.USBSerialPrint("Sent macro line: "); }
    if(debuggingSettings.debuggingMacroBuffer) { serialManager.USBSerialPrintln(bufferMacroLines[bufferMacroLineIndex]); }

    arrayUtil.clearCharArray(bufferMacroLines[bufferMacroLineIndex], MACRO_LINE_BUFFER_CHARS); //clear the line we just sent 

  }else{

    if (isLaserEdgeProbingActive || isLaserMaterialBoundsProbingActive) return; // laser probing manages its own lifecycle

    if (isProbeZ0MacroRunning)
      pushProbeZ0CompletedEvent(true);
    if (isProbeToolLengthMacroRunning)
      pushProbeToolLengthCompletedEvent(probeToolLengthZCaptured);
    isRotaryYLockBypassed = false;
    isMacroRunning = false; //if there are no more lines to execute we can set the macro running flag to false, so we don't enter this function anymore until a new macro is loaded in the buffer

    if(debuggingSettings.debuggingMacroBuffer) { serialManager.USBSerialPrintln("No more lines in macro buffer, stopping macro execution"); }

  }
    

}

void resetManualSpindleRampState() {

  isManualSpindleRampActive = false;
  manualSpindleRampLastStepMs = 0;
  manualSpindleRampIntervalMs = 0;
  manualSpindleRampTargetRPM = 0;
  manualSpindleRampNextRPM = 0;
  manualSpindleRampLastCommandedRPM = 0;
  manualSpindleRampAwaitingDrain = false;
  manualSpindleRampDrainStartedMs = 0;
  manualSpindleRampSettlePending = false;

}

// Clears the modal spindle memory (M3 on-flag, memorized S, last commanded RPM)
// and drops any line held for a ramp. Called on M5/M30, GRBL reset, job stop and
// job start, so no spindle state ever leaks from one context into the next.
// keepMemorisedRpm: M5/M30 turn the spindle OFF but must NOT forget the speed the file asked for.
// GRBL keeps its own modal S, so a later bare "M3" spins straight back up to it -- and with the value
// forgotten CPUB would see "M3 with no S", decline to ramp and forward the line, producing exactly
// the abrupt full-speed start the ramp exists to prevent. Job start and GRBL reset still clear it, so
// nothing leaks from a previous file or the console into a new job.
void resetSpindleModalState(bool keepMemorisedRpm) {

  isSpindleOnCommanded = false;
  if (!keepMemorisedRpm)
    lastRequestedSpindleRPM = 0;
  commandedSpindleRPM = 0;
  isSpindleLineHeld = false;
  heldSpindleLineIsJob = false;
  arrayUtil.clearCharArray(heldSpindleLineBuffer, LINE_BUFFER_CHARACTERS);

}

bool buildMobileServerIpFromBleName(const char* serverNameSuffix, char* ipBuffer, size_t ipBufferSize) {

  if (serverNameSuffix == nullptr || ipBuffer == nullptr || ipBufferSize == 0)
    return false;

  arrayUtil.clearCharArray(ipBuffer, ipBufferSize);
  strncpy(ipBuffer, serverNameSuffix, ipBufferSize - 1);
  ipBuffer[ipBufferSize - 1] = NULL_CHAR;

  for (size_t index = 0; ipBuffer[index] != NULL_CHAR; ++index) {
    if (ipBuffer[index] == '_')
      ipBuffer[index] = '.';
  }

  return ipBuffer[0] != NULL_CHAR;

}

bool isValidIPv4Address(const char* ipAddress) {

  if (ipAddress == nullptr || ipAddress[0] == NULL_CHAR)
    return false;

  int dotCount = 0;
  const char* segmentStart = ipAddress;
  const char* cursor = ipAddress;

  while (true) {
    if (*cursor == '.' || *cursor == NULL_CHAR) {
      if (cursor == segmentStart)
        return false;

      long octet = 0;
      for (const char* octetCursor = segmentStart; octetCursor < cursor; ++octetCursor) {
        if (!isdigit(static_cast<unsigned char>(*octetCursor)))
          return false;

        octet = (octet * 10) + (*octetCursor - '0');
        if (octet > 255)
          return false;
      }

      if (*cursor == NULL_CHAR)
        break;

      ++dotCount;
      if (dotCount > 3)
        return false;

      segmentStart = cursor + 1;
    }

    ++cursor;
  }

  return dotCount == 3;

}

bool esp32ResponseReportsAlreadyEstablished(const char* responseLine) {

  if (responseLine == nullptr || responseLine[0] == NULL_CHAR)
    return false;

  char normalizedResponse[ESP32C3_RESPONSE_BUFFER_SIZE];
  arrayUtil.clearCharArray(normalizedResponse, sizeof(normalizedResponse));
  strncpy(normalizedResponse, responseLine, sizeof(normalizedResponse) - 1);
  normalizedResponse[sizeof(normalizedResponse) - 1] = NULL_CHAR;

  for (size_t index = 0; normalizedResponse[index] != NULL_CHAR; ++index)
    normalizedResponse[index] = static_cast<char>(tolower(static_cast<unsigned char>(normalizedResponse[index])));

  return strstr(normalizedResponse, "already established") != nullptr
    || strstr(normalizedResponse, "already connected") != nullptr
    || strstr(normalizedResponse, "link is connected") != nullptr
    || strstr(normalizedResponse, "has connected") != nullptr;

}

long getExecutableFileLineCount(const char* filename, long totalLines) {

  if (filename == nullptr || totalLines <= 0)
    return totalLines;

  char firstLineBuffer[256];
  arrayUtil.clearCharArray(firstLineBuffer, sizeof(firstLineBuffer));

  if (sdCardManager.readFirstLine(filename, firstLineBuffer, sizeof(firstLineBuffer))
    && GCodeBoundsUtil::isMetadataLine(firstLineBuffer)) {
    return totalLines - 1;
  }

  return totalLines;

}

void scheduleForcedWebSocketClose(int linkId) {

  pendingForcedWebSocketCloseLinkId = linkId;
  pendingForcedWebSocketCloseStartMs = 0;

}

bool tryParseGCodeWordLongValue(const char* lineBuffer, char word, long* value) {

  if (lineBuffer == nullptr || value == nullptr)
    return false;

  const char uppercaseWord = static_cast<char>(toupper(static_cast<unsigned char>(word)));

  for (int index = 0; lineBuffer[index] != NULL_CHAR; ++index) {
    const char currentChar = lineBuffer[index];

    if (currentChar == NEWLINE || currentChar == RETURN || currentChar == ';')
      break;

    if (currentChar == '(') {
      while (lineBuffer[index] != NULL_CHAR && lineBuffer[index] != ')')
        ++index;
      continue;
    }

    if (toupper(static_cast<unsigned char>(currentChar)) != uppercaseWord)
      continue;

    char* endPtr = nullptr;
    long parsedValue = strtol(lineBuffer + index + 1, &endPtr, 10);

    if (endPtr == lineBuffer + index + 1)
      continue;

    *value = parsedValue;
    return true;
  }

  return false;

}

bool tryParseGCodeWordFloatValue(const char* lineBuffer, char word, float* value) {

  if (lineBuffer == nullptr || value == nullptr)
    return false;

  const char uppercaseWord = static_cast<char>(toupper(static_cast<unsigned char>(word)));

  for (int index = 0; lineBuffer[index] != NULL_CHAR; ++index) {
    const char currentChar = lineBuffer[index];

    if (currentChar == NEWLINE || currentChar == RETURN || currentChar == ';')
      break;

    if (currentChar == '(') {
      while (lineBuffer[index] != NULL_CHAR && lineBuffer[index] != ')')
        ++index;
      continue;
    }

    if (toupper(static_cast<unsigned char>(currentChar)) != uppercaseWord)
      continue;

    char* endPtr = nullptr;
    float parsedValue = strtof(lineBuffer + index + 1, &endPtr);

    if (endPtr == lineBuffer + index + 1)
      continue;

    *value = parsedValue;
    return true;
  }

  return false;

}

bool isHomingCommandLine(const char* lineBuffer) {

  if (lineBuffer == nullptr)
    return false;

  int index = 0;
  while (lineBuffer[index] == ' ' || lineBuffer[index] == '\t')
    ++index;

  if (lineBuffer[index] != '$')
    return false;

  ++index;
  if (toupper(static_cast<unsigned char>(lineBuffer[index])) != 'H')
    return false;

  ++index;
  char axisSpecifier = toupper(static_cast<unsigned char>(lineBuffer[index]));
  if (axisSpecifier == 'X' || axisSpecifier == 'Y' || axisSpecifier == 'Z')
    ++index;

  char terminator = lineBuffer[index];
  return terminator == NULL_CHAR || terminator == NEWLINE || terminator == RETURN || terminator == ' ' || terminator == '\t';

}

bool isRotaryBlockedHomingCommandLine(const char* lineBuffer) {

  if (!isHomingCommandLine(lineBuffer))
    return false;

  int index = 0;
  while (lineBuffer[index] == ' ' || lineBuffer[index] == '\t')
    ++index;

  // Skip "$H"
  index += 2;

  const char axisSpecifier = toupper(static_cast<unsigned char>(lineBuffer[index]));
  return axisSpecifier != 'Z';

}

bool isWithinSoftLimitRange(float x, float y, float z) {

  return x >= SOFT_LIMIT_MIN_X_MM && x <= SOFT_LIMIT_MAX_X_MM
    && y >= SOFT_LIMIT_MIN_Y_MM && y <= SOFT_LIMIT_MAX_Y_MM
    && z >= SOFT_LIMIT_MIN_Z_MM && z <= SOFT_LIMIT_MAX_Z_MM;

}

// Pre-flight check for raster scans (height map, point cloud). The scan rectangle is
// specified in job/work coordinates with corners at (originX, originY) and
// (originX + sizeX, originY - sizeY): scans sweep +X and -Y, matching the machine
// Y envelope that runs from 0 at home down to negative values. All four corners are
// converted to machine coordinates via the active WCO and checked against the
// soft-limit range together with the current machine Z (Z is held constant during
// these scans).
bool isScanRectWithinSoftLimitRange(float originX, float originY, float sizeX, float sizeY) {

  const float maxX = originX + sizeX;
  const float minY = originY - sizeY;
  const float corners[4][2] = {
    { originX, originY },
    { maxX,    originY },
    { originX, minY    },
    { maxX,    minY    },
  };
  for (uint8_t i = 0; i < 4; i++) {
    const float machineX = corners[i][0] + wcoX;
    const float machineY = corners[i][1] + wcoY;
    if (!isWithinSoftLimitRange(machineX, machineY, currentMachinePositionZ))
      return false;
  }
  return true;

}

bool evaluateAndApplySoftLimitsForOutgoingLine(const char* lineBuffer) {

  outgoingLineRejectReason = OUTGOING_LINE_REJECT_NONE;

  if (lineBuffer == nullptr || lineBuffer[0] == NULL_CHAR)
    return true;

  if (isRotaryAxisConnected && !isRotaryYLockBypassed && isRotaryBlockedHomingCommandLine(lineBuffer)) {
    if (debuggingSettings.debuggingJob || debuggingSettings.debuggingJson) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("Rotary Y-lock blocked homing command: ");
      serialManager.USBSerialPrintln(lineBuffer);
    }
    outgoingLineRejectReason = OUTGOING_LINE_REJECT_ROTARY_Y_LOCK;
    return false;
  }

  bool sawAnyG = false;
  bool sawG0 = false;
  bool sawG1 = false;
  bool sawG2 = false;
  bool sawG3 = false;
  bool sawG90 = false;
  bool sawG91 = false;
  bool sawG20 = false;
  bool sawG21 = false;
  bool sawG53 = false;
  bool sawNonLinearG = false;

  for (int index = 0; lineBuffer[index] != NULL_CHAR; ++index) {
    const char currentChar = lineBuffer[index];

    if (currentChar == NEWLINE || currentChar == RETURN || currentChar == ';')
      break;

    if (currentChar == '(') {
      while (lineBuffer[index] != NULL_CHAR && lineBuffer[index] != ')')
        ++index;
      continue;
    }

    if (toupper(static_cast<unsigned char>(currentChar)) != 'G')
      continue;

    char* endPtr = nullptr;
    long gCode = strtol(lineBuffer + index + 1, &endPtr, 10);
    if (endPtr == lineBuffer + index + 1)
      continue;

    sawAnyG = true;
    if (gCode == 0) sawG0 = true;
    else if (gCode == 1) sawG1 = true;
    else if (gCode == 2) sawG2 = true;
    else if (gCode == 3) sawG3 = true;
    else if (gCode == 90) sawG90 = true;
    else if (gCode == 91) sawG91 = true;
    else if (gCode == 20) sawG20 = true;
    else if (gCode == 21) sawG21 = true;
    else if (gCode == 53) sawG53 = true;
    else sawNonLinearG = true;

    index = static_cast<int>(endPtr - lineBuffer) - 1;
  }

  float valueX = 0.0f;
  float valueY = 0.0f;
  float valueZ = 0.0f;
  const bool hasX = tryParseGCodeWordFloatValue(lineBuffer, 'X', &valueX);
  const bool hasY = tryParseGCodeWordFloatValue(lineBuffer, 'Y', &valueY);
  const bool hasZ = tryParseGCodeWordFloatValue(lineBuffer, 'Z', &valueZ);
  const bool hasAxis = hasX || hasY || hasZ;

  bool absoluteMode = outboundAbsoluteMode;
  if (sawG90) absoluteMode = true;
  if (sawG91) absoluteMode = false;

  bool metricUnits = outboundMetricUnits;
  if (sawG20) metricUnits = false;
  if (sawG21) metricUnits = true;

  bool isLinearMotion = false;
  if (sawG0 || sawG1) {
    isLinearMotion = hasAxis;
  } else if (!sawAnyG && hasAxis && (outboundLastMotionMode == 1 || outboundLastMotionMode == 2)) {
    isLinearMotion = true;
  }

  bool isArcMotion = false;
  if (sawG2 || sawG3) {
    isArcMotion = true;
  } else if (!sawAnyG && hasAxis && (outboundLastMotionMode == 3 || outboundLastMotionMode == 4)) {
    isArcMotion = true;
  }

  if (isRotaryAxisConnected && !isRotaryYLockBypassed && isArcMotion) {
    if (debuggingSettings.debuggingJob || debuggingSettings.debuggingJson) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("Rotary Y-lock blocked arc command: ");
      serialManager.USBSerialPrintln(lineBuffer);
    }
    outgoingLineRejectReason = OUTGOING_LINE_REJECT_ROTARY_Y_LOCK;
    return false;
  }

  if (isLinearMotion) {
    const float unitFactor = metricUnits ? 1.0f : 25.4f;
    const float baseMachineX = currentMachinePositionX;
    const float baseMachineY = currentMachinePositionY;
    const float baseMachineZ = currentMachinePositionZ;

    float targetX = baseMachineX;
    float targetY = baseMachineY;
    float targetZ = baseMachineZ;

    if (hasX) {
      const float convertedX = valueX * unitFactor;
      if (sawG53)
        targetX = convertedX;
      else
        targetX = absoluteMode ? (convertedX + wcoX) : (baseMachineX + convertedX);
    }

    if (hasY) {
      const float convertedY = valueY * unitFactor;
      if (sawG53)
        targetY = convertedY;
      else
        targetY = absoluteMode ? (convertedY + wcoY) : (baseMachineY + convertedY);
    }

    if (isRotaryAxisConnected && !isRotaryYLockBypassed && hasY) {
      const float yDelta = targetY - baseMachineY;
      if (yDelta > 0.0001f || yDelta < -0.0001f) {
        if (debuggingSettings.debuggingJob || debuggingSettings.debuggingJson) {
          serialManager.USBSerialPrint(FROM_CPUB);
          serialManager.USBSerialPrint("Rotary Y-lock blocked command: ");
          serialManager.USBSerialPrintln(lineBuffer);
        }
        outgoingLineRejectReason = OUTGOING_LINE_REJECT_ROTARY_Y_LOCK;
        return false;
      }
    }

    if (hasZ) {
      const float convertedZ = valueZ * unitFactor;
      if (sawG53)
        targetZ = convertedZ;
      else
        targetZ = absoluteMode ? (convertedZ + wcoZ) : (baseMachineZ + convertedZ);
    }

    if (!isWithinSoftLimitRange(targetX, targetY, targetZ)) {
      if (debuggingSettings.debuggingJob || debuggingSettings.debuggingJson) {
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrint("Soft-limit blocked command: ");
        serialManager.USBSerialPrintln(lineBuffer);
      }
      outgoingLineRejectReason = OUTGOING_LINE_REJECT_SOFT_LIMIT;
      return false;
    }
  }

  if (sawG90)
    outboundAbsoluteMode = true;
  if (sawG91)
    outboundAbsoluteMode = false;
  if (sawG20)
    outboundMetricUnits = false;
  if (sawG21)
    outboundMetricUnits = true;

  if (sawG0)
    outboundLastMotionMode = 1;
  else if (sawG1)
    outboundLastMotionMode = 2;
  else if (sawG2)
    outboundLastMotionMode = 3;
  else if (sawG3)
    outboundLastMotionMode = 4;
  else if (sawAnyG && sawNonLinearG)
    outboundLastMotionMode = 0;

  return true;

}

const char* getOutgoingLineRejectErrorJsonValue() {

  if (outgoingLineRejectReason == OUTGOING_LINE_REJECT_ROTARY_Y_LOCK)
    return JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;

  return JSON_VALUE_ERROR_SOFT_LIMIT_EXCEEDED_STR;

}

void stopJobWithReset() {

  if (isLaserEdgeProbingActive) {
    pushLaserEdgeProbingCompletedEvent(false);
  }

  if (isLaserMaterialBoundsProbingActive) {
    pushLaserMaterialBoundsCompletedEvent(false);
  }

  // If pause/hold toggled spindle stop (0x9E), untoggle it before reset so the
  // next job does not inherit a stopped spindle output state.
  if (pauseResumeSpindleRestorePending)
    serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_STOP_TOGGLE));

  // A stop ABANDONS the job, so drop the virtual-door latch and push the release BEFORE the soft
  // reset. The latch exists to survive grblHAL stopping the spindle during parking, but it must
  // not survive a stop: grblHAL re-evaluates the door signal as it restarts, and with the door
  // still asserted it comes back up in STATE_SAFETY_DOOR ([MSG:Check Door]) instead of Idle — the
  // job would never stop. Releasing first means grbl restarts with the door closed.
  // Note: with allowMotionWhenDoorOpen == false and the lid still open, the policy immediately
  // re-asserts on the next pass — correct, since motion is not permitted in that configuration.
  virtualDoorLatched = false;
  handleVirtualDoorSignal();

  serialManager.CPUASerialWrite(GRBL_STOP_RESET);

  isJobRunning = false;
  isDryRunActive = false;
  pendingDryRunFeedRate = false;
  isJobPaused = false;
  isResuming = false;
  pauseResumeSpindleRestorePending = false;
  pendingPauseSpindleStop = false;
  pauseHoldRequestedMs = 0;
  isResumeSpindleOverrideRampActive = false;
  resumeMotionAfterOverrideRampPending = false;
  resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
  // GRBL soft-reset returns all real-time overrides to 100%; sync local cache.
  spindleOverride = GRBL_OVERRIDE_DEFAULT;
  pauseResumeOriginalSpindleOverride = GRBL_OVERRIDE_DEFAULT;
  // Same for the resume feed ramp: clear it and sync the feed override cache to 100%.
  isResumeFeedRampActive = false;
  resumeFeedRampStage = RESUME_FEED_IDLE;
  feedRateOverride = GRBL_OVERRIDE_DEFAULT;
  pauseResumeOriginalFeedOverride = GRBL_OVERRIDE_DEFAULT;
  resumeCompletionPending = false;
  isPauseFeedDropActive = false;
  isMacroRunning = false;
  isProbeZ0MacroRunning = false;
  isProbeToolLengthMacroRunning = false;
  probeToolLengthZCaptured = false;
  isRotaryYLockBypassed = false;
  isLaserEdgeProbingActive = false;
  laserEdgeProbeSuppressAlarmAbort = false;
  isLaserMaterialBoundsProbingActive = false;
  laserMaterialBoundsProbeSuppressAlarmAbort = false;
  waitForOk = 0;
  jobStartMs = 0;
  jobLineIndex = 0;
  // Keep the height map on stop: the file stays open (rewound below), so the map remains valid and
  // bound to it. Only clear the "used" flag so compensation re-applies when this same file is run again.
  heightMapUsedForCurrentFile = false;
  jobLineSeg.active = false;
  jobArcSeg.active  = false;
  resetManualSpindleRampState();
  resetSpindleModalState(false);

  // Rewind the currently opened execution file so startJob restarts from line 1.
  if (openedReadFileName[0] != NULL_CHAR) {
    sdCardManager.closeFile();
    if (!sdCardManager.openGCodeFiletoRead(openedReadFileName)) {
      arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
      openedReadFileTotalLines = 0;
    }
  }

  arrayUtil.clear2DCharArray(bufferMacroLines);

}

// The lowest spindle override percent that still leaves the spindle at or above
// SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM.
//
// GRBL scales the PROGRAMMED S word by the override (spindle_set_rpm: `rpm *= 0.01f * override`),
// so the floor is a percentage of whatever the job asked for and moves with it.
//
// Read from commandedSpindleRPM -- what was last actually sent to GRBL, which is the same number
// GRBL scales. Deliberately NOT currentSpindleRPM: that is GRBL's already-overridden report coming
// back in the status line, so using it would compound the override with itself, and it lags during
// a job (status polling is throttled while streaming).
int minimumSpindleOverridePercent() {

  const long programmedRPM = commandedSpindleRPM;

  // Spindle off: nothing is turning, so there is nothing to protect.
  if (programmedRPM <= 0)
    return GRBL_OVERRIDE_MIN;

  // Rounded UP. A truncating divide picks the percent just BELOW the floor, which is the one step
  // this whole function exists to refuse: at S9000 the exact answer is 33.3%, and 33% is 2970 rpm.
  long percent = ((long)SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM * 100L + programmedRPM - 1) / programmedRPM;

  if (percent < GRBL_OVERRIDE_MIN)
    percent = GRBL_OVERRIDE_MIN;

  // Capped at 100%: a spindle programmed at or under the floor works out to 100% or more here, and
  // what that has to mean is "no reduction allowed", never "speed it up". The override cannot make
  // a slow spindle faster -- 100% of a programmed S2500 is still 2500 rpm -- it can only be stopped
  // from making it slower, which is what this cap does.
  if (percent > GRBL_OVERRIDE_DEFAULT)
    percent = GRBL_OVERRIDE_DEFAULT;

  return (int)percent;

}

// Where a spindle-override step lands, or -1 when it is refused for taking the spindle below the
// floor. `delta` is signed: -GRBL_OVERRIDE_COARSE_STEP for a coarse decrease, +1 for a fine
// increase. Increases are never refused -- the floor is a floor, not a range.
int spindleOverrideAfterStep(int currentPercent, int delta, int floorPercent) {

  int next = currentPercent + delta;

  if (next > GRBL_OVERRIDE_MAX)
    next = GRBL_OVERRIDE_MAX;
  if (next < GRBL_OVERRIDE_MIN)
    next = GRBL_OVERRIDE_MIN;

  if (delta < 0 && next < floorPercent)
    return -1;

  return next;

}

// The refusal, in one place: both decrease actions report it identically, and it has to say more
// than "failure". An override that simply stops moving with no reason given reads as a dead button,
// so the reply carries the percent it is being held at and the RPM floor doing the holding.
// Build and schedule a work-coordinate move, absolute (G90) or relative (G91).
//
// `axisWords` is the axis part only: "X10.5000", "X1.0000 Y2.0000 Z3.0000", "A90.0000".
//
// RELATIVE is what a jog step wants. An absolute target has to be worked out from a position the
// app read some milliseconds ago, and the machine may have moved since: tap a second step before
// the status catches up and the target is computed from where the head WAS, so the move comes out
// short by whatever the first step still had to travel. That is how a 1 mm step arrives as 0.37 mm.
// A delta has no such window -- GRBL adds it to where the machine actually is, and two taps in a
// row queue as two whole steps.
//
// The modal state is put straight back to G90 afterwards. G91 is sticky in GRBL and everything else
// here -- the job reader, the macros, the other move commands -- assumes absolute. It is a separate
// line rather than a suffix on the same one so that the move is soft-limit checked as the relative
// move it is (evaluateAndApplySoftLimitsForOutgoingLine resolves the delta against the machine
// position; a trailing G90 on the same line would make it read the delta as a target).
bool scheduleWorkMove(const char* axisWords, float feedRate, bool relative) {

  char moveCmd[MACRO_LINE_BUFFER_CHARS];
  snprintf(moveCmd, sizeof(moveCmd), "%s G1 %s F%.4f",
           relative ? "G91" : "G90", axisWords, feedRate);

  const bool accepted = scheduleOrSendCPUA(moveCmd);

  // Only after an accepted one: a rejected line never went out, so GRBL is still in G90 and there
  // is nothing to restore.
  if (relative && accepted)
    scheduleOrSendCPUA("G90");

  return accepted;

}

void sendSpindleOverrideFloorRefusal() {

  if(debuggingSettings.debuggingSpindle){

    char dbg[176];
    snprintf(dbg, sizeof(dbg),
             "Spindle override held at %d%%: a further step would take the programmed %ld rpm below the %d rpm override floor",
             spindleOverride, commandedSpindleRPM, (int)SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM);
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln(dbg);

  }

  if(!jsonSettings.enableJsonResponse)
    return;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
  responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_SPINDLE_RPM_BELOW_MINIMUM_STR;
  // The unchanged value, so a client that optimistically stepped its own display can put it back.
  responseJsonBuffer[JSON_KEY_SPINDLE_OVERRIDE_STR] = spindleOverride;
  responseJsonBuffer[JSON_KEY_MINIMUM_SPINDLE_RPM_STR] = SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

}

void sendManualSpindleRampCommand(long spindleRPM) {

  char spindleCommand[32];
  arrayUtil.clearCharArray(spindleCommand, sizeof(spindleCommand));
  snprintf(spindleCommand, sizeof(spindleCommand), "M3 S%ld", spindleRPM);

  serialManager.CPUASerialPrintln(spindleCommand);
  ++waitForOk;

  manualSpindleRampLastStepMs = millis();
  manualSpindleRampLastCommandedRPM = spindleRPM;
  commandedSpindleRPM = spindleRPM;

  if (debuggingSettings.debuggingSpindle) {
    serialManager.USBSerialPrint("Manual spindle ramp step sent: ");
    serialManager.USBSerialPrintln(spindleRPM);
  }

}

bool startManualSpindleRamp(long targetRPM) {

  if (MANUAL_SPINDLE_RAMP_RPM_INCREMENT <= 0)
    return false;

  // Base the ramp on what we last COMMANDED, never on the status-report RPM:
  // status polling is throttled during a job, so currentSpindleRPM can be stale
  // (e.g. still showing the old speed after an M5) and a base derived from it
  // would make the ramp start with exactly the jump it is meant to prevent.
  long startRPM = commandedSpindleRPM > 0 ? commandedSpindleRPM : 0;

  if (isManualSpindleRampActive && manualSpindleRampLastCommandedRPM > startRPM)
    startRPM = manualSpindleRampLastCommandedRPM;

  if (targetRPM <= startRPM)
    return false;

  const unsigned long rpmDelta = static_cast<unsigned long>(targetRPM - startRPM);
  const unsigned long steps = (rpmDelta + MANUAL_SPINDLE_RAMP_RPM_INCREMENT - 1) / MANUAL_SPINDLE_RAMP_RPM_INCREMENT;

  if (steps == 0)
    return false;

  manualSpindleRampIntervalMs = (MANUAL_SPINDLE_RAMP_TOTAL_TIME_MS + steps - 1) / steps;
  if (manualSpindleRampIntervalMs == 0)
    manualSpindleRampIntervalMs = 1;

  manualSpindleRampTargetRPM = targetRPM;
  manualSpindleRampNextRPM = startRPM + MANUAL_SPINDLE_RAMP_RPM_INCREMENT;
  if (manualSpindleRampNextRPM > manualSpindleRampTargetRPM)
    manualSpindleRampNextRPM = manualSpindleRampTargetRPM;

  manualSpindleRampLastCommandedRPM = startRPM;
  manualSpindleRampLastStepMs = millis();
  isManualSpindleRampActive = true;

  if (debuggingSettings.debuggingSpindle) {
    serialManager.USBSerialPrint("Starting manual spindle ramp from ");
    serialManager.USBSerialPrint(startRPM);
    serialManager.USBSerialPrint(" to ");
    serialManager.USBSerialPrint(targetRPM);
    serialManager.USBSerialPrint(" rpm in steps of ");
    serialManager.USBSerialPrint(MANUAL_SPINDLE_RAMP_RPM_INCREMENT);
    serialManager.USBSerialPrint(" every ");
    serialManager.USBSerialPrint(manualSpindleRampIntervalMs);
    serialManager.USBSerialPrintln(" ms");
  }

  return true;

}

// Core pause action shared by the pauseJob API command and the door-sensor safety
// pause. Sends GRBL feed-hold, arms the deferred spindle stop if the spindle is
// running, and marks the job paused. Does not emit any JSON response — the caller
// is responsible for that (the door-sensor path has no requesting client).
void pauseJobInternal(bool rampFeedFirst) {

  // ── PAUSE offloaded to grblHAL parking (safety door 0x84). ─────────────────────────
  // Send CMD_SAFETY_DOOR (0x84): grblHAL stops the spindle and raises Z to the park height;
  // on resume ('~') grblHAL restarts the spindle (with a spin-up dwell) and plunges Z back.
  // The old CPUB pause sequence (feed-drop + deferred 0x9E) is kept below, disabled (#if 0).
  (void)rampFeedFirst;
  resetManualSpindleRampState();  // stop any CPUB-driven spindle ramp so it can't fight grbl
  serialManager.CPUASerialWrite(static_cast<byte>(GRBL_SAFETY_DOOR));
  currentMachineState = HOLD;     // snappy UI; grbl reports Door, masked to Hold on parse
  isJobPaused = true;
  isResuming = false;             // a new pause supersedes any in-progress resume

#if 0  // ─── OLD CPUB pause procedure (disabled; kept for revert) ───
  // If a resume spindle "recycle" is still running, abort it cleanly. The spindle
  // is already running at this point (it was restarted at full when the resume
  // began), so we just cancel the override cycle and fall through to the normal
  // pause below, which stops the spindle via the deferred 0x9E. The override is
  // somewhere between floor and target mid-cycle; restore the captured target so
  // the deferred pause snapshots the right value.
  const bool wasRestoringSpindle = isResumeSpindleOverrideRampActive;
  if (wasRestoringSpindle) {
    spindleOverride = resumeSpindleOverrideRampTargetPercent;
    isResumeSpindleOverrideRampActive = false;
    resumeMotionAfterOverrideRampPending = false;
    resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
  }

  // Likewise abort any in-progress resume FEED ramp; restore the local feed-override
  // counter to the value it is ramping toward so the pause snapshot below is sane.
  if (isResumeFeedRampActive) {
    feedRateOverride = resumeFeedRampTargetPercent;
    isResumeFeedRampActive = false;
    resumeFeedRampStage = RESUME_FEED_IDLE;
  }
  resumeCompletionPending = false;
  isPauseFeedDropActive = false;  // cancel any prior pause feed-drop (double pause)

  long spindleReferenceRPM = currentSpindleRPM;
  if (commandedSpindleRPM > spindleReferenceRPM)
    spindleReferenceRPM = commandedSpindleRPM;

  // Force the spindle-stop path when aborting a restore: the spindle is (or was
  // just) running but the cached RPM may still read stale/zero.
  const bool willStopSpindle = wasRestoringSpindle || (spindleReferenceRPM >= MINIMUM_SPINDLE_RPM);

  resetManualSpindleRampState();

  // Capture the pre-pause overrides so resume can restore them.
  pauseResumeOriginalSpindleOverride = spindleOverride;
  pauseResumeOriginalFeedOverride = feedRateOverride;

  if (rampFeedFirst && feedRateOverride > RESUME_FEED_START_PERCENT) {
    // Gentle (API) pause: ramp the FEED override DOWN to the resume floor WHILE STILL MOVING,
    // then hold. grblHAL honors feed-override changes during motion and the value persists
    // through the hold, so motion later RESUMES at this low feed — eliminating the full-speed
    // window on resume. handlePauseFeedDrop() sends '!' and arms the spindle stop at the floor.
    pauseWillStopSpindle = willStopSpindle;
    isPauseFeedDropActive = true;
    pauseFeedDropLastStepMs = millis() - RESUME_FEED_DROP_STEP_MS;  // first step ASAP
  } else {
    // Immediate hold: door-sensor safety pause (must not keep cutting), or feed already low.
    serialManager.CPUASerialWrite(GRBL_HOLD);
    if (willStopSpindle) {
      // Defer the 0x9E spindle-stop toggle until GRBL actually reaches HOLD; GRBL-HAL ignores
      // 0x9E while motion is still decelerating, so sending it right after '!' can be dropped.
      pendingPauseSpindleStop = true;
      pauseHoldRequestedMs = millis();
    }
  }

  currentMachineState = HOLD;
  isJobPaused = true;
#endif  // ─── end OLD CPUB pause procedure ───

}

// Gentle-pause feed-drop: paces the feed override down to the resume floor while the machine
// is still moving (grblHAL honors this only out of HOLD), then issues the actual feed hold and
// arms the deferred spindle stop. Because the low feed override persists through the hold, the
// job later resumes at low feed with no full-speed window.
void handlePauseFeedDrop() {

  if (!isPauseFeedDropActive)
    return;

  if ((millis() - pauseFeedDropLastStepMs) < RESUME_FEED_DROP_STEP_MS)
    return;

  if (feedRateOverride > RESUME_FEED_START_PERCENT) {
    serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_FEED_DECREASE_COARSE));
    feedRateOverride -= GRBL_OVERRIDE_COARSE_STEP;
    if (feedRateOverride < RESUME_FEED_START_PERCENT)
      feedRateOverride = RESUME_FEED_START_PERCENT;
    pauseFeedDropLastStepMs = millis();
    return;
  }

  // Feed override is at the floor (and persists through the hold) — now stop motion.
  serialManager.CPUASerialWrite(GRBL_HOLD);
  isPauseFeedDropActive = false;

  if (pauseWillStopSpindle) {
    pendingPauseSpindleStop = true;
    pauseHoldRequestedMs = millis();
  }

  if (debuggingSettings.debuggingSpindle)
    serialManager.USBSerialPrintln("Pause: feed at floor, motion held");

}

// What the machine state should LOOK like to the frontend.
//
// A spindle ramp mid-job deliberately stops the machine: it drains GRBL's planner and streams nothing
// while it paces its steps, so GRBL correctly reports Idle. The job has not stopped though -- it is
// waiting for the spindle -- and reporting that raw shows a running job sitting idle mid-cut, which
// invites the operator to act on it.
//
// Presentation only. currentMachineState itself is left alone, because the ramp's own drain wait, the
// resume logic and the height map all key off the REAL state; rewriting it globally would make the
// ramp wait on a value it had just faked.
MachineState getReportedMachineState() {

  // Any momentary Idle during a running job is reported as Run, not just the spindle ramp's. With
  // one-line-at-a-time flow control GRBL routinely drains between lines, so a raw Idle flickers
  // through the whole job -- and the ramp's own drain wait makes it a multi-second stretch. A job
  // that is genuinely finished clears isJobRunning, and a paused one sets isJobPaused, so neither is
  // masked by this.
  if (currentMachineState == IDLE && isJobRunning && !isJobPaused)
    return RUN;

  return currentMachineState;

}

void handleManualSpindleRamp() {

  if (!isManualSpindleRampActive)
    return;

  // Lid opened (or the job was paused) mid-ramp: abandon the remaining steps instead of
  // keeping "M3 S..." flowing to GRBL. The pause path stops the spindle itself.
  if (isSpindleStartBlocked()) {

    resetManualSpindleRampState();

    if (debuggingSettings.debuggingSpindle) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Spindle ramp aborted (lid open or job paused)");
    }

    return;
  }

  if (waitForOk > 0)
    return;

  // Job-triggered ramp: let GRBL finish what it already accepted, so the machine is stopped before
  // the spindle starts climbing and motion only resumes once the ramp has finished.
  if (manualSpindleRampAwaitingDrain) {

    const bool drainTimedOut =
      (millis() - manualSpindleRampDrainStartedMs) >= SPINDLE_RAMP_JOB_DRAIN_TIMEOUT_MS;

    if (!drainTimedOut && currentMachineState != IDLE)
      return;

    manualSpindleRampAwaitingDrain = false;

    if (debuggingSettings.debuggingSpindle) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln(drainTimedOut
        ? "Spindle ramp: gave up waiting for the planner to drain, ramping anyway"
        : "Spindle ramp: motion stopped, ramping now");
    }

  }

  if ((millis() - manualSpindleRampLastStepMs) < manualSpindleRampIntervalMs)
    return;

  // The final speed was sent and has now had SPINDLE_RAMP_FINAL_SETTLE_MS to take effect.
  if (manualSpindleRampSettlePending) {

    if (debuggingSettings.debuggingSpindle) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Spindle ramp complete and settled, releasing motion");
    }

    resetManualSpindleRampState();
    return;
  }

  const long spindleRPM = manualSpindleRampNextRPM;
  sendManualSpindleRampCommand(spindleRPM);

  if (spindleRPM >= manualSpindleRampTargetRPM) {
    // Target SENT, not yet REACHED: GRBL acks an "M3 S..." on acceptance, so releasing the job here
    // would restart motion with the spindle still climbing. Hold one more interval.
    manualSpindleRampSettlePending = true;
    manualSpindleRampIntervalMs = SPINDLE_RAMP_FINAL_SETTLE_MS;
    return;
  }

  manualSpindleRampNextRPM += MANUAL_SPINDLE_RAMP_RPM_INCREMENT;
  if (manualSpindleRampNextRPM > manualSpindleRampTargetRPM)
    manualSpindleRampNextRPM = manualSpindleRampTargetRPM;

}

void handlePendingPauseSpindleStop() {

  if (!pendingPauseSpindleStop)
    return;

  if (currentMachineState != HOLD)
    return;

  // Only fire the 0x9E once a fresh GRBL status report (received AFTER the
  // pause was requested) confirms HOLD. PAUSE_JOB sets currentMachineState
  // to HOLD artificially for UI snappiness, so we must wait for a real
  // status update; otherwise we'd send 0x9E while motion is still
  // decelerating and GRBL would silently drop it.
  if (lastGRBLStatusReceivedMs <= pauseHoldRequestedMs)
    return;

  serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_STOP_TOGGLE));

  pendingPauseSpindleStop = false;
  pauseHoldRequestedMs = 0;
  pauseResumeSpindleRestorePending = true;

}

// Starts the resume spindle "recycle". The caller resumes motion ('~') and
// restarts the spindle at full (untoggle 0x9E) FIRST — GRBL-HAL applies spindle
// overrides only out of HOLD — then this runs as a paced, single real-time-byte-
// per-step state machine driven by handleResumeSpindleOverrideRamp:
//   1. RESUME_RESTORE_DROP — pace the override DOWN to the floor
//                            (RESUME_SPINDLE_RESTORE_FLOOR_PERCENT, capped at target).
//   2. RESUME_RESTORE_RAMP — pace the override back UP to targetPercent, so the
//                            override ends explicitly driven to 100%.
// (RESUME_RESTORE_RESTART is unused now — the spindle is restarted by the caller.)
// Real-time override bytes are NEVER sent back-to-back: GRBL-HAL coalesces identical
// override requests that arrive within one realtime-processing pass, so a tight burst
// silently collapses into a single step and the local counter drifts. Hence the paced
// RESUME_SPINDLE_OVERRIDE_STEP_INTERVAL_MS between every step.
bool startResumeSpindleOverrideRamp(int targetPercent) {

  if (targetPercent < GRBL_OVERRIDE_MIN)
    targetPercent = GRBL_OVERRIDE_MIN;
  if (targetPercent > GRBL_OVERRIDE_MAX)
    targetPercent = GRBL_OVERRIDE_MAX;

  resumeSpindleOverrideRampTargetPercent = targetPercent;
  resumeSpindleOverrideRampIntervalMs = RESUME_SPINDLE_DROP_STEP_INTERVAL_MS;
  resumeSpindleOverrideRampLastStepMs = millis();
  resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
  isResumeSpindleOverrideRampActive = true;

  if (debuggingSettings.debuggingSpindle) {
    int floorPercent = RESUME_SPINDLE_RESTORE_FLOOR_PERCENT;
    if (floorPercent > targetPercent)
      floorPercent = targetPercent;
    serialManager.USBSerialPrint("Resume spindle recycle: full, dip to ");
    serialManager.USBSerialPrint(floorPercent);
    serialManager.USBSerialPrint("%%, ramp back to ");
    serialManager.USBSerialPrint(targetPercent);
    serialManager.USBSerialPrint("%% at ");
    serialManager.USBSerialPrint((int)RESUME_SPINDLE_OVERRIDE_STEP_INTERVAL_MS);
    serialManager.USBSerialPrintln(" ms/step");
  }

  return true;

}

// Coordinates the single resumeCompleted event across the two parallel resume machines
// (spindle recycle + feed ramp). Fires exactly once, only after BOTH have gone idle.
void maybeCompleteResume() {
  if (resumeCompletionPending && !isResumeSpindleOverrideRampActive && !isResumeFeedRampActive) {
    resumeCompletionPending = false;
    pushResumeCompletedEvent();
  }
}

// Starts the resume feed (motion) ramp. Called right AFTER motion resumes ('~'): grblHAL
// ignores feed-override changes during HOLD and re-accelerates from zero on resume, so we
// drop the feed override to RESUME_FEED_START_PERCENT immediately (fast) and then ramp it
// back up to the captured target over RESUME_FEED_RAMP_TOTAL_MS while the spindle spins up.
void startResumeFeedRamp(int targetPercent) {

  if (targetPercent < GRBL_OVERRIDE_MIN)
    targetPercent = GRBL_OVERRIDE_MIN;
  if (targetPercent > GRBL_OVERRIDE_MAX)
    targetPercent = GRBL_OVERRIDE_MAX;

  resumeFeedRampTargetPercent = targetPercent;
  resumeFeedRampStage = RESUME_FEED_DROP;
  resumeFeedRampStepIntervalMs = RESUME_FEED_DROP_STEP_MS;
  // Fire the first drop on the very next loop so the feed comes down as soon as possible.
  resumeFeedRampLastStepMs = millis() - RESUME_FEED_DROP_STEP_MS;
  isResumeFeedRampActive = true;

  if (debuggingSettings.debuggingSpindle) {
    serialManager.USBSerialPrint("Resume feed ramp: drop to ");
    serialManager.USBSerialPrint(RESUME_FEED_START_PERCENT);
    serialManager.USBSerialPrint("%% then ramp back to ");
    serialManager.USBSerialPrint(targetPercent);
    serialManager.USBSerialPrintln("%%");
  }

}

void handleResumeFeedRamp() {

  if (!isResumeFeedRampActive)
    return;

  if ((millis() - resumeFeedRampLastStepMs) < resumeFeedRampStepIntervalMs)
    return;

  switch (resumeFeedRampStage) {

    case RESUME_FEED_DROP:
      // Fast paced -10% down to the start floor, minimizing the time motion spends near
      // full feed while the spindle is still spinning up.
      if (feedRateOverride > RESUME_FEED_START_PERCENT) {
        serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_FEED_DECREASE_COARSE));
        feedRateOverride -= GRBL_OVERRIDE_COARSE_STEP;
        if (feedRateOverride < RESUME_FEED_START_PERCENT)
          feedRateOverride = RESUME_FEED_START_PERCENT;
        resumeFeedRampLastStepMs = millis();
      } else {
        // At the floor — ramp back up toward the captured target, spreading the configured
        // total time across the remaining +10% steps.
        int feedSteps = (resumeFeedRampTargetPercent - feedRateOverride) / GRBL_OVERRIDE_COARSE_STEP;
        if (feedSteps < 1)
          feedSteps = 1;
        resumeFeedRampStepIntervalMs = RESUME_FEED_RAMP_TOTAL_MS / feedSteps;
        resumeFeedRampStage = RESUME_FEED_RAMPUP;
        resumeFeedRampLastStepMs = millis();
        if (debuggingSettings.debuggingSpindle) {
          serialManager.USBSerialPrint("Resume feed ramp: at floor ");
          serialManager.USBSerialPrint(feedRateOverride);
          serialManager.USBSerialPrint("%%, ramping to ");
          serialManager.USBSerialPrint(resumeFeedRampTargetPercent);
          serialManager.USBSerialPrint("%% over ");
          serialManager.USBSerialPrint((int)RESUME_FEED_RAMP_TOTAL_MS);
          serialManager.USBSerialPrintln(" ms");
        }
      }
      break;

    case RESUME_FEED_RAMPUP:
      if (feedRateOverride + GRBL_OVERRIDE_COARSE_STEP <= resumeFeedRampTargetPercent) {
        serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_FEED_INCREASE_COARSE));
        feedRateOverride += GRBL_OVERRIDE_COARSE_STEP;
        resumeFeedRampLastStepMs = millis();
      } else {
        // Within one coarse step of target: land exactly. If the target is 100%, the feed
        // reset byte (0x90) snaps there regardless of any coalesced/dropped step, so the
        // job never continues at reduced feed. Otherwise nudge the final coarse step.
        if (resumeFeedRampTargetPercent == GRBL_OVERRIDE_DEFAULT) {
          serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_FEED_RESET));
          feedRateOverride = GRBL_OVERRIDE_DEFAULT;
        } else if (feedRateOverride < resumeFeedRampTargetPercent) {
          serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_FEED_INCREASE_COARSE));
          feedRateOverride = resumeFeedRampTargetPercent;
        }
        isResumeFeedRampActive = false;
        resumeFeedRampStage = RESUME_FEED_IDLE;
        if (debuggingSettings.debuggingSpindle)
          serialManager.USBSerialPrintln("Resume feed ramp complete");
        maybeCompleteResume();
      }
      break;

  }

}

void handleResumeSpindleOverrideRamp() {

  if (!isResumeSpindleOverrideRampActive)
    return;

  if ((millis() - resumeSpindleOverrideRampLastStepMs) < resumeSpindleOverrideRampIntervalMs)
    return;

  switch (resumeSpindleRestoreStage) {

    case RESUME_RESTORE_DROP: {
      // Spindle is already running at full (restarted by the RESUME_JOB handler).
      // Paced -10% down to the floor (capped at target), then ramp back up.
      int floorPercent = RESUME_SPINDLE_RESTORE_FLOOR_PERCENT;
      if (floorPercent > resumeSpindleOverrideRampTargetPercent)
        floorPercent = resumeSpindleOverrideRampTargetPercent;
      if (floorPercent < GRBL_OVERRIDE_MIN)
        floorPercent = GRBL_OVERRIDE_MIN;
      if (spindleOverride > floorPercent) {
        serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_DECREASE_COARSE));
        spindleOverride -= GRBL_OVERRIDE_COARSE_STEP;
        if (spindleOverride < floorPercent)
          spindleOverride = floorPercent;
        resumeSpindleOverrideRampLastStepMs = millis();
      } else {
        resumeSpindleRestoreStage = RESUME_RESTORE_RAMP;
        resumeSpindleOverrideRampIntervalMs = RESUME_SPINDLE_OVERRIDE_STEP_INTERVAL_MS;
        resumeSpindleOverrideRampLastStepMs = millis();
        if (debuggingSettings.debuggingSpindle) {
          serialManager.USBSerialPrint("Resume spindle recycle: at floor ");
          serialManager.USBSerialPrint(spindleOverride);
          serialManager.USBSerialPrint("%%, ramping back to ");
          serialManager.USBSerialPrint(resumeSpindleOverrideRampTargetPercent);
          serialManager.USBSerialPrintln("%%");
        }
      }
      break;
    }

    case RESUME_RESTORE_RAMP:
      // Paced +10% up to target, now that the machine is running.
      if (spindleOverride < resumeSpindleOverrideRampTargetPercent) {
        serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_INCREASE_COARSE));
        spindleOverride += GRBL_OVERRIDE_COARSE_STEP;
        if (spindleOverride > resumeSpindleOverrideRampTargetPercent)
          spindleOverride = resumeSpindleOverrideRampTargetPercent;
        resumeSpindleOverrideRampLastStepMs = millis();
      }
      if (spindleOverride >= resumeSpindleOverrideRampTargetPercent) {
        // Local counter reached target, but a coalesced/dropped override byte may have
        // left GRBL short. Do NOT finish yet: hand off to the closed-loop CONFIRM stage.
        resumeSpindleRestoreStage = RESUME_RESTORE_CONFIRM;
        resumeConfirmEnteredMs = millis();
        resumeConfirmLastActionMs = millis();
        resumeConfirmReachedMs = 0;
        resumeSpindleOverrideRampLastStepMs = millis();
        if (debuggingSettings.debuggingSpindle)
          serialManager.USBSerialPrintln("Resume spindle ramp reached target; confirming vs grbl Ov:");
      }
      break;

    case RESUME_RESTORE_CONFIRM: {
      // Verify GRBL actually reports the spindle override at target and hold a settle
      // time before completing. This closes the loop the open-loop ramp cannot: if a
      // +10% (or -10%) override byte was coalesced/dropped, GRBL ends off target while
      // the local counter says "done"; here we read GRBL's "Ov:" and nudge it back.

      // Overall safety timeout so CONFIRM can never hang (e.g. GRBL stops emitting Ov:).
      if ((millis() - resumeConfirmEnteredMs) >= RESUME_SPINDLE_CONFIRM_TIMEOUT_MS) {
        spindleOverride = resumeSpindleOverrideRampTargetPercent;
        isResumeSpindleOverrideRampActive = false;
        resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
        if (debuggingSettings.debuggingSpindle)
          serialManager.USBSerialPrintln("Resume spindle confirm timed out; completing");
        maybeCompleteResume();
        break;
      }

      // Only act on a GRBL Ov: reading that post-dates our last action, so a stale or
      // missing Ov: field cannot trigger an extra nudge (which would overshoot).
      if (lastReportedSpindleOverrideMs <= resumeConfirmLastActionMs) {
        resumeSpindleOverrideRampLastStepMs = millis();
        break;
      }

      int overrideDiff = lastReportedSpindleOverride - resumeSpindleOverrideRampTargetPercent;
      int overrideDiffAbs = overrideDiff < 0 ? -overrideDiff : overrideDiff;

      if (lastReportedSpindleOverride >= 0 && overrideDiffAbs >= GRBL_OVERRIDE_COARSE_STEP) {
        // GRBL is off target by a full coarse step or more — nudge back toward target.
        byte overrideByte = (overrideDiff < 0)
                              ? static_cast<byte>(GRBL_OVERRIDE_SPINDLE_INCREASE_COARSE)
                              : static_cast<byte>(GRBL_OVERRIDE_SPINDLE_DECREASE_COARSE);
        serialManager.CPUASerialWrite(overrideByte);
        resumeConfirmReachedMs = 0;             // not settled yet
        resumeConfirmLastActionMs = millis();
        resumeSpindleOverrideRampLastStepMs = millis();
        if (debuggingSettings.debuggingSpindle) {
          serialManager.USBSerialPrint("Resume spindle confirm: grbl Ov: ");
          serialManager.USBSerialPrint(lastReportedSpindleOverride);
          serialManager.USBSerialPrint("%% vs target ");
          serialManager.USBSerialPrint(resumeSpindleOverrideRampTargetPercent);
          serialManager.USBSerialPrintln("%%, nudging");
        }
        break;
      }

      // GRBL is within a coarse step of target: start/continue the settle timer.
      if (resumeConfirmReachedMs == 0)
        resumeConfirmReachedMs = millis();

      if ((millis() - resumeConfirmReachedMs) >= RESUME_SPINDLE_CONFIRM_SETTLE_MS) {
        spindleOverride = resumeSpindleOverrideRampTargetPercent;
        isResumeSpindleOverrideRampActive = false;
        resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
        if (debuggingSettings.debuggingSpindle)
          serialManager.USBSerialPrintln("Resume spindle restore confirmed at target");
        maybeCompleteResume();
      } else {
        resumeSpindleOverrideRampLastStepMs = millis();
      }
      break;
    }

  }

}

// True when the line consists ONLY of spindle words (M3 and/or S, plus optional
// N line numbers, whitespace and comments). Such a line can be consumed outright —
// the ramp's own "M3 S..." steps replace it. Any other content (motion, feed,
// other M codes) means the line must be forwarded, or held and released after the
// ramp so nothing is lost.
bool isSpindleOnlyLine(const char* lineBuffer) {

  if (lineBuffer == nullptr)
    return false;

  bool hasSpindleWord = false;

  for (int index = 0; lineBuffer[index] != NULL_CHAR; ) {

    const char currentChar = lineBuffer[index];

    if (currentChar == NEWLINE || currentChar == RETURN || currentChar == ';')
      break;

    if (currentChar == ' ' || currentChar == '\t') {
      ++index;
      continue;
    }

    if (currentChar == '(') {
      while (lineBuffer[index] != NULL_CHAR && lineBuffer[index] != ')')
        ++index;
      if (lineBuffer[index] == ')')
        ++index;
      continue;
    }

    const char upper = static_cast<char>(toupper(static_cast<unsigned char>(currentChar)));

    if (upper != 'M' && upper != 'S' && upper != 'N')
      return false;

    char* endPtr = nullptr;
    const long wordValue = strtol(lineBuffer + index + 1, &endPtr, 10);

    if (endPtr == lineBuffer + index + 1)
      return false; // word letter with no number: not a plain spindle line

    if (upper == 'M' && wordValue != 3)
      return false; // any M other than M3 must be forwarded

    if (upper == 'M' || upper == 'S')
      hasSpindleWord = true;

    index = static_cast<int>(endPtr - lineBuffer);

  }

  return hasSpindleWord;

}

// True when the line would make the spindle turn: an explicit spindle-on M-code (M3/M4) or
// an S>0 while the modal spindle is already on (GRBL applies a bare S immediately in that
// case; with the spindle off it only memorizes the speed). Scans EVERY word on the line —
// unlike tryParseGCodeWordLongValue, which stops at the first M word and would miss the M3
// in "M8 M3 S9000" — and skips ( ) and ; comments. This is the safety gate's parser, so it
// errs on the side of catching the command.
bool lineCommandsSpindleOn(const char* lineBuffer) {

  if (lineBuffer == nullptr)
    return false;

  bool hasSpindleOnMCode = false;
  bool hasPositiveSWord  = false;

  for (int index = 0; lineBuffer[index] != NULL_CHAR; ) {

    const char currentChar = lineBuffer[index];

    if (currentChar == NEWLINE || currentChar == RETURN || currentChar == ';')
      break;

    if (currentChar == '(') {
      while (lineBuffer[index] != NULL_CHAR && lineBuffer[index] != ')')
        ++index;
      if (lineBuffer[index] == ')')
        ++index;
      continue;
    }

    const char upper = static_cast<char>(toupper(static_cast<unsigned char>(currentChar)));

    if (upper != 'M' && upper != 'S') {
      ++index;
      continue;
    }

    char* endPtr = nullptr;
    const long wordValue = strtol(lineBuffer + index + 1, &endPtr, 10);

    if (endPtr == lineBuffer + index + 1) {   // word letter with no number: not a spindle word
      ++index;
      continue;
    }

    if (upper == 'M' && (wordValue == 3 || wordValue == 4))
      hasSpindleOnMCode = true;

    if (upper == 'S' && wordValue > 0)
      hasPositiveSWord = true;

    index = static_cast<int>(endPtr - lineBuffer);

  }

  return hasSpindleOnMCode || (hasPositiveSWord && isSpindleOnCommanded);

}

// Modal spindle-ramp interception. Mirrors GRBL's modal words so the gentle
// "M3 S..." ramp engages no matter how the CAM splits the commands:
//   - S with spindle off  -> memorize S, forward the line untouched
//   - M3 (with/without S) -> ramp to the memorized/attached S
//   - S with spindle on   -> ramp up to the new S (down or small bumps < one
//                            increment are forwarded directly, no ramp)
//   - M5/M30              -> clear ALL modal spindle state (S does not survive M5)
// Lines carrying more than spindle words are held and released after the ramp.
// Returns true when the line was consumed (or held) and must NOT be forwarded.
bool handleSpindleRampCommand(const char* lineBuffer, bool allowDuringJob) {

  if (lineBuffer == nullptr || isMacroRunning)
    return false;

  // Spindle-start gate (lid open with the housing installed, or job paused). The callers
  // check this first and decide what to do with the line; this is the last-resort guard so
  // no path can ever start a ramp or leak an M3 to GRBL. Reported as consumed, never
  // forwarded. M5/M30 fall through below: turning the spindle OFF is always allowed.
  if (isSpindleStartBlocked() && lineCommandsSpindleOn(lineBuffer)) {

    if (debuggingSettings.debuggingSpindle) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("Spindle-on line blocked (lid open or job paused): ");
      serialManager.USBSerialPrintln(lineBuffer);
    }

    return true;
  }

  if (!allowDuringJob && isJobRunning)
    return false;

  long mCode = -1;
  const bool hasMCode = tryParseGCodeWordLongValue(lineBuffer, 'M', &mCode);

  if (hasMCode && (mCode == 5 || mCode == 30)) {
    resetSpindleModalState(true);   // keep the memorised S so a later bare M3 still ramps to it
    resetManualSpindleRampState();
    return false;
  }

  const bool hasM3 = hasMCode && (mCode == 3);

  long sValue = -1;
  const bool hasSWord = tryParseGCodeWordLongValue(lineBuffer, 'S', &sValue) && sValue >= 0;

  if (!hasM3 && !hasSWord)
    return false;

  if (hasSWord)
    lastRequestedSpindleRPM = sValue; // modal S memory

  if (hasM3)
    isSpindleOnCommanded = true;

  // Spindle not commanded on: the S is memorized for the next M3, the line
  // itself passes through untouched (harmless with the spindle off).
  if (!isSpindleOnCommanded)
    return false;

  const long targetRPM = hasSWord ? sValue : lastRequestedSpindleRPM;

  // M3 with no S ever seen behaves like M3 S0: nothing to ramp, forward as-is.
  if (targetRPM <= 0)
    return false;

  long baseRPM = commandedSpindleRPM;
  if (isManualSpindleRampActive && manualSpindleRampLastCommandedRPM > baseRPM)
    baseRPM = manualSpindleRampLastCommandedRPM;

  // Ramping down (or same speed): applied instantly, no booster stress. Abort
  // any in-flight ramp and let the line through directly.
  if (targetRPM <= baseRPM) {
    if (isManualSpindleRampActive)
      resetManualSpindleRampState();
    commandedSpindleRPM = targetRPM;
    return false;
  }

  // Small upward bump below one ramp increment: not worth a ramp (and, mid-job,
  // not worth a motion dwell) — forward directly.
  if (!isManualSpindleRampActive && (targetRPM - baseRPM) < MANUAL_SPINDLE_RAMP_RPM_INCREMENT) {
    commandedSpindleRPM = targetRPM;
    return false;
  }

  // An M3 read from the job pauses motion first. handleJob already stops streaming while a ramp is
  // active, but lines GRBL has already ACCEPTED keep executing -- an "ok" means accepted, not done --
  // so without the drain wait the machine carries on moving while the spindle climbs.
  const bool rampFromJob = allowDuringJob && isJobRunning && !isJobPaused;

  if (!startManualSpindleRamp(targetRPM)) {
    // Defensive: ramp refused (should not happen after the checks above) —
    // forward the line directly so the command is never lost.
    commandedSpindleRPM = targetRPM;
    return false;
  }

  if (rampFromJob) {
    manualSpindleRampAwaitingDrain = true;
    manualSpindleRampDrainStartedMs = millis();
  }

  // Line carries more than spindle words (e.g. "G1 X10 S9000"): hold it and
  // release it once the ramp completes, so the motion waits for the spindle.
  if (!isSpindleOnlyLine(lineBuffer)) {
    arrayUtil.clearCharArray(heldSpindleLineBuffer, LINE_BUFFER_CHARACTERS);
    strncpy(heldSpindleLineBuffer, lineBuffer, LINE_BUFFER_CHARACTERS - 1);
    heldSpindleLineIsJob = allowDuringJob && isJobRunning;
    isSpindleLineHeld = true;

    if (debuggingSettings.debuggingSpindle) {
      serialManager.USBSerialPrint("Line held until spindle ramp completes: ");
      serialManager.USBSerialPrintln(heldSpindleLineBuffer);
    }
  }

  return true;

}

// Releases a CONSOLE line that was held while the spindle ramped (job-held lines
// are released inside handleJob so they go through the full job pipeline). Sent
// through the same soft-limit check as any other passthrough line.
void handleHeldSpindleLineRelease() {

  if (!isSpindleLineHeld || heldSpindleLineIsJob)
    return;

  if (isManualSpindleRampActive)
    return;

  if (waitForOk > 0)
    return;

  // The gate closed while the line waited (lid opened mid-ramp, or the job was paused): the
  // held line still carries the spindle words that started the ramp, so drop it instead of
  // releasing it to GRBL.
  if (isSpindleStartBlocked() && lineCommandsSpindleOn(heldSpindleLineBuffer)) {

    isSpindleLineHeld = false;

    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrint("Held spindle line dropped (lid open or job paused): ");
    serialManager.USBSerialPrintln(heldSpindleLineBuffer);

    arrayUtil.clearCharArray(heldSpindleLineBuffer, LINE_BUFFER_CHARACTERS);
    return;

  }

  isSpindleLineHeld = false;

  if (!evaluateAndApplySoftLimitsForOutgoingLine(heldSpindleLineBuffer))
    return;

  ++waitForOk;
  writeLineCPUA(heldSpindleLineBuffer);

  if (debuggingSettings.debuggingSpindle) {
    serialManager.USBSerialPrint("Held line released after spindle ramp: ");
    serialManager.USBSerialPrintln(heldSpindleLineBuffer);
  }

}

void handleRawPassthroughLine(const char* lineBuffer) {

  if (!enableCPUAPassthrough || lineBuffer == nullptr)
    return;

  // While a file upload is active, no raw G-code passthrough is expected. Any non-JSON line
  // arriving here during a transfer is a fragment of a chunk message that failed to parse —
  // never forward it to GRBL (it would be rejected as malformed G-code, e.g. error:1).
  if (isFileTransferInProgress)
    return;

  // A line starting with '{' is a JSON message that failed to deserialize
  // (e.g. an oversized fc chunk). Never forward JSON payloads to GRBL —
  // they may contain GCode inside JSON string values that would execute live.
  if (lineBuffer[0] == '{')
    return;

  // Spindle-start gate: never forward a line that would make the spindle turn while the
  // housing is installed and the lid is open, or while the job is paused. The whole line is
  // dropped rather than stripped — a console line mixing motion with M3 is unsafe as a unit.
  if (isSpindleStartBlocked() && lineCommandsSpindleOn(lineBuffer)) {

    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrint("Spindle-on line dropped (lid open or job paused): ");
    serialManager.USBSerialPrintln(lineBuffer);
    return;

  }

  if (handleSpindleRampCommand(lineBuffer, false))
    return;

  if (!evaluateAndApplySoftLimitsForOutgoingLine(lineBuffer))
    return;

  if(waitForOk <= 0){

    ++waitForOk;
    writeLineCPUA(lineBuffer);

  }

}

void writeLineCPUA(const char* lineBuffer) {

  if(lineBuffer == nullptr)
    return;

  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrint("CPUA passthrough line: "); }
  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln(lineBuffer); }

  for (int index = 0; index < LINE_BUFFER_CHARACTERS; index++) {

    serialManager.CPUASerialWrite(lineBuffer[index]);

    if (lineBuffer[index] == NEWLINE || lineBuffer[index] == NULL_CHAR)
      break;
  }
}

void askGRBLStatus() {

  // A job-triggered spindle ramp waits for GRBL to report Idle before it starts stepping, and during
  // a job status is not otherwise requested at all -- so without this exception that wait could only
  // ever end on its timeout. Nothing is being streamed while it waits, so the poll costs nothing.
  if(isJobRunning && !isJobPaused && !manualSpindleRampAwaitingDrain)
    return; //if a job is running (not paused) we do not ask for the status

  // A ramp is no reason to stop polling either. '?' cannot disturb the ramp's own line sends (it is
  // realtime and takes no line slot), and the ramp is one of the things that NEEDS the reports: it
  // holds until GRBL reports Idle before it starts stepping. Gating it here froze the app's status
  // for the whole spin-up -- seconds, with the kick and its settling time.

  //in pause we still ask for the status
  
  if(!timerUtil.statusTimer())
    return;

  serialManager.CPUASerialWrite(GRBL_STATUS_QUERY); // '?' is a realtime command, no 'ok' response

}

void handleESP32Connection(){

  //here we must try to have writing and reading in a non blocking way, since this is called at every loop
  //we also use a timer to not clog the comms with the ESP32C3
  if(!timerUtil.ESP32C3CheckTimer())
   return;

  //buffer variables
  int responseType;
  char cmd [ESP32C3_COMMAND_BUFFER_SIZE];
  char tempResponse[ESP32C3_RESPONSE_BUFFER_SIZE];

   ///////////INITIAL CONFIG///////////////////////////////////////////////////////////////////////////////////////////////////////////
  if(!initialESP32C3Config){

      if(!atRequested && initialESP32ResponsesReceived == 0){

      atRequested = true;
      esp32c3Manager.sendATCommand(ESP32C3_AT);

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(ESP32C3_AT);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}
      
    }

    // Step 1: close WebSocket link 1 if open from a previous session.
    // ERROR response here is expected (link not open) and is treated as success.
    if(!wsLink1CloseRequested && initialESP32ResponsesReceived == 1){

      wsLink1CloseRequested = true;
      esp32c3Manager.sendATCommand(ESP32C3_CLOSE_WEBSOCKET_LINK_1);

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(ESP32C3_CLOSE_WEBSOCKET_LINK_1 " sent to ESP32C3 (cleanup)");}

    }
    
    if(!accessPointModeRequested && initialESP32ResponsesReceived == 2){

      accessPointModeRequested = true;
      const char* wifiModeCmd = wifiSettings.hotspotEnabled ? ESP32C3_WIFI_MODE_AP_AND_STATION : ESP32C3_WIFI_MODE_STATION;
      esp32c3Manager.sendATCommand(wifiModeCmd);

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(wifiModeCmd);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}

    }

    if(!startAccessPoint && initialESP32ResponsesReceived == 3){

      startAccessPoint = true;

      if(wifiSettings.hotspotEnabled){

        // Hotspot enabled: configure the access point
        arrayUtil.clearCharArray(cmd, ESP32C3_COMMAND_BUFFER_SIZE);

        strcat(cmd, ESP32C3_SET_AP);
        strcat(cmd, "=\"");
        strcat(cmd, wifiSettings.apSsid);
        strcat(cmd, "\",\"");
        strcat(cmd, wifiSettings.apPassword);
        strcat(cmd, "\",");
        strcat(cmd, ESP32C3_AP_CHANNEL);
        strcat(cmd, ",");
        strcat(cmd, ESP32C3_AP_ENCRYPTION);

        esp32c3Manager.sendATCommand(cmd);

        if(debuggingSettings.debuggingESP32){printWiFiCommandMasked(cmd);}

      }else{

        // Hotspot disabled: skip AP setup, go straight to mobile WebSocket config
        esp32c3Manager.sendATCommand(ESP32C3_CONFIGURE_MOBILE_WEBSOCKET);

        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(ESP32C3_CONFIGURE_MOBILE_WEBSOCKET);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}

      }

    }

    if(!mobileWebsocketConfigured && initialESP32ResponsesReceived == 4){

      mobileWebsocketConfigured = true;

      if(wifiSettings.hotspotEnabled){

        // Hotspot enabled: mobile WebSocket config at the normal step 4
        esp32c3Manager.sendATCommand(ESP32C3_CONFIGURE_MOBILE_WEBSOCKET);

        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Mobile WebSocket config command: ");}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(ESP32C3_CONFIGURE_MOBILE_WEBSOCKET);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}

      }else{

        // Hotspot disabled: mobile WebSocket already configured at step 3, now connect to client WiFi
        arrayUtil.clearCharArray(cmd, ESP32C3_COMMAND_BUFFER_SIZE);

        strcat(cmd, ESP32C3_CONNECT_WIFI);
        strcat(cmd, "=\"");  
        strcat(cmd, wifiSettings.clientSsid);
        strcat(cmd, "\",\"");
        strcat(cmd, wifiSettings.clientPassword);
        strcat(cmd, "\"");

        esp32c3Manager.sendATCommand(cmd);
        clientWiFiConnectionAttempted = true;

        if(debuggingSettings.debuggingESP32){printWiFiCommandMasked(cmd);}

      }

    }

    if(!clientWiFiConnectionAttempted && initialESP32ResponsesReceived == 5){

      arrayUtil.clearCharArray(cmd, ESP32C3_COMMAND_BUFFER_SIZE);

      strcat(cmd, ESP32C3_CONNECT_WIFI);
      strcat(cmd, "=\"");  
      strcat(cmd, wifiSettings.clientSsid);
      strcat(cmd, "\",\"");
      strcat(cmd, wifiSettings.clientPassword);
      strcat(cmd, "\"");
      
      esp32c3Manager.sendATCommand(cmd);
      clientWiFiConnectionAttempted = true;

      if(debuggingSettings.debuggingESP32){printWiFiCommandMasked(cmd);}

    }

    if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY){

      responseType = esp32c3Manager.getResponseType();
      arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
      esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);

      switch (responseType)
      {

      case ESP32C3_RESPONSE_TYPE_OK:

        ++initialESP32ResponsesReceived;

        //if we just completed the WiFi connection step, mark WiFi as connected
        if(initialESP32ResponsesReceived == initialESP32ExpectedResponses)
          isClientWiFiConnected = true;
      
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("OK response received from ESP32C3");}

        break;
      
      case ESP32C3_RESPONSE_TYPE_UNKNOWN:
        resetESP32InitialConfiguration();
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("unknown response received from ESP32C3");}
        break;

      case ESP32C3_RESPONSE_TYPE_ERROR:

        // Step 1 is WSCLOSE cleanup: ERROR simply means the link was not open — treat as success and proceed
        if(initialESP32ResponsesReceived == 1) {
          ++initialESP32ResponsesReceived;
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("WSCLOSE not open (expected), proceeding with init...");}
        //if the error is about the client wifi connection, we retry the connection
        } else if(clientWiFiConnectionAttempted && initialESP32ResponsesReceived == initialESP32ExpectedResponses - 1) {
          clientWiFiConnectionAttempted = false; //reset to retry WiFi connection on the next loop
          if(!wifiSettings.hotspotEnabled)
            mobileWebsocketConfigured = false; // also reset the step flag so CWJAP is re-sent at step 6
        } else
          resetESP32InitialConfiguration();
        
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("error response received from ESP32C3, retrying WiFi connection...");}
        break;

      default:
        break;
      }
    
    }

    if(initialESP32ResponsesReceived == initialESP32ExpectedResponses){

      initialESP32C3Config = true; //we have received all the expected responses from the ESP32, we can exit the startup execution phase

      // Discard any bytes accumulated in the pending buffer during the init sequence
      // (e.g. the "WIFI DISCONNECT" intermediate line that AT+CWJAP always emits when
      // leaving the previous AP association). If left in the buffer they would be
      // processed by checkForWebSocketMessage() as a live disconnect event, resetting
      // isClientWiFiConnected and re-triggering the init loop indefinitely.
      esp32c3Manager.clearPendingBuffer();
      
       if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
       if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("ESP32C3 initial configuration completed");}

    }
      
    
  }

  //here return in case the initial configuration is not complete
  if(!initialESP32C3Config)
   return;

  if(handleForcedWebSocketClose())
    return;

  if(isMobileWebSocketConnected && hasWebSocketPingTimedOut(mobileWebSocketLastApiPingMs)) {
    pendingForcedWebSocketCloseLinkId = 1;
    pendingForcedWebSocketCloseStartMs = 0;
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Mobile websocketPing timeout, forcing close...");}
    return;
  }

  const bool hasActiveWebSocketConnection = isMobileWebSocketConnected;
  const bool isAnyWebSocketConnecting = isMobileWebSocketConnecting;
  const bool mobileWebSocketReadyToConnect = isClientWiFiConnected
    && bleScanComplete
    && mobileServerName[0] != NULL_CHAR;

  if(hasActiveWebSocketConnection)
    return;


  /////////////BLE SCAN (runs once after WiFi connects, before WebSocket; re-runs whenever the no-connection retry flow resumes)////////////////////////////////////////////////////////////////////////////////////////////////////////////
  if(!hasActiveWebSocketConnection && !isAnyWebSocketConnecting && !bleScanComplete && isClientWiFiConnected && millis() >= mobileWebSocketNextRetryMs) {

    // Timeout guard: if the scan session has been stuck for too long, reset and retry
    if(bleDeInitRequested && millis() - bleScanSessionStartMs > BLE_SCAN_SESSION_TIMEOUT_MS) {
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE scan session timed out, will retry...");}
      isBleActive = false;
      resetMobileBleScanState();
      return;
    }

    isBleActive = true; // block websocket comms while BLE scan is in progress

    if(!bleDeInitRequested) {
      bleScanSessionStartMs = millis();
      bleDeInitRequested = true;
      esp32c3Manager.sendATCommand(ESP32C3_BLE_DEINIT);
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE deinit sent to ESP32C3");}
    }

    if(bleDeInitRequested && !bleDeInitComplete) {
      if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY) {
        arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        bleDeInitComplete = true; // proceed regardless of OK or ERROR
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE deinit complete, sending init...");}
      }
      return;
    }

    if(bleDeInitComplete && !bleInitRequested) {
      bleInitRequested = true;
      esp32c3Manager.sendATCommand(ESP32C3_BLE_INIT);
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE init sent to ESP32C3");}
    }

    if(bleInitRequested && !bleInitComplete) {
      if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY) {
        responseType = esp32c3Manager.getResponseType();
        arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        if(responseType == ESP32C3_RESPONSE_TYPE_OK) {
          bleInitComplete = true;
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE initialized, starting scan...");}
        } else {
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE init failed, will retry...");}
          isBleActive = false;
          resetMobileBleScanState();
        }
      }
      return;
    }

    if(bleInitComplete && !bleScanParamsRequested) {
      bleScanParamsRequested = true;
      esp32c3Manager.sendATCommand(ESP32C3_BLE_SCAN_PARAMS);
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(ESP32C3_BLE_SCAN_PARAMS);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}
    }

    if(bleScanParamsRequested && !bleScanParamsComplete) {
      if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY) {
        responseType = esp32c3Manager.getResponseType();
        arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
        if(responseType == ESP32C3_RESPONSE_TYPE_OK) {
          bleScanParamsComplete = true;
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE scan parameters configured");}
        } else {
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE scan parameter setup failed, will retry...");}
          isBleActive = false;
          resetMobileBleScanState();
        }
      }
      return;
    }

    if(bleScanParamsComplete && !bleScanRequested) {
      char bleScanCmd[ESP32C3_COMMAND_BUFFER_SIZE];
      arrayUtil.clearCharArray(bleScanCmd, ESP32C3_COMMAND_BUFFER_SIZE);
      strcat(bleScanCmd, ESP32C3_BLE_SCAN_START);
      strcat(bleScanCmd, ESP32C3_BLE_SCAN_DURATION_S);
      strcat(bleScanCmd, ESP32C3_BLE_SCAN_FILTER_NAME);
      strcat(bleScanCmd, DEFAULT_ESP32C3_BLE_SCAN_NAME_FILTER);
      strcat(bleScanCmd, ESP32C3_BLE_SCAN_DUPLICATE_CHECK);
      bleScanRequested = true;
      esp32c3Manager.sendATCommand(bleScanCmd);
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(bleScanCmd);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(" sent to ESP32C3");}
    }

    if(bleScanRequested && !bleScanComplete) {
      if(esp32c3Manager.checkForBLEScanResponseAndCaptureName(debuggingSettings.debuggingESP32, mobileServerName, ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE, DEFAULT_ESP32C3_BLE_SCAN_NAME_FILTER) == ESP32C3_RESPONSE_READY) {
        bleScanComplete = true;
        isBleActive = false;
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
        if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("BLE scan complete");}
        if(mobileServerName[0] == NULL_CHAR) {
          // No mobile server found in this scan — reset scan state to retry on next iterations
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("No mobile server found in BLE scan, retrying...");}
          resetMobileBleScanState();
        } else {
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Mobile server name found: ");}
          if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(mobileServerName);}
        }
      }
      return;
    }

    return; // still waiting for WiFi or scan step to progress
  }


  /////////////MOBILE WEBSOCKET CONNECTION (mobileWebsocketConnection)////////////////////////////////////////////////////////////////////////////////////////////////////////////
  //if we are not connected to the mobile websocket server and we are not trying to connect, we try to connect here
  //we require a valid mobile server name from the BLE scan before attempting the connection
  if(!hasActiveWebSocketConnection && !isAnyWebSocketConnecting && mobileWebSocketReadyToConnect){

    char mobileCmd [ESP32C3_COMMAND_BUFFER_SIZE];
    arrayUtil.clearCharArray(mobileCmd, ESP32C3_COMMAND_BUFFER_SIZE);

    // replace underscores with dots to convert the BLE server name to an IP address
    char mobileServerIp[ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE];
    arrayUtil.clearCharArray(mobileServerIp, ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE);

    if(!buildMobileServerIpFromBleName(mobileServerName, mobileServerIp, sizeof(mobileServerIp)) || !isValidIPv4Address(mobileServerIp)) {
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Invalid mobile server name/IP from BLE scan: suffix=");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(mobileServerName);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(" parsedIp=");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(mobileServerIp);}

      resetMobileBleScanState();
      return;
    }

    strcat(mobileCmd, ESP32C3_OPEN_MOBILE_WEBSOCKET);
    strcat(mobileCmd, ",\"");
    strcat(mobileCmd, DEFAULT_ESP32C3_WEBSOCKET_SERVER_PREFIX);
    strcat(mobileCmd, mobileServerIp);
    strcat(mobileCmd, DEFAULT_ESP32C3_MOBILE_WEBSOCKET_SERVER_SUFFIX);
    strcat(mobileCmd, "\"");

    //attempt here to connect to mobile websocket
    if(esp32c3Manager.sendATCommand(mobileCmd)) {
      isMobileWebSocketConnecting = true;
      mobileWebSocketConnectingStartMs = millis();

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Mobile WebSocket open command: ");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(mobileCmd);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Connecting to Mobile WebSocket: ");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(DEFAULT_ESP32C3_WEBSOCKET_SERVER_PREFIX);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(mobileServerIp);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(DEFAULT_ESP32C3_MOBILE_WEBSOCKET_SERVER_SUFFIX);}
    }

  }

  //here we check if there is a response from the ESP32C3 about mobile websocket connection
  if(!isMobileWebSocketConnected && isMobileWebSocketConnecting){

    if(millis() - mobileWebSocketConnectingStartMs > MOBILE_WEBSOCKET_CONNECTING_TIMEOUT_MS){
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Mobile WebSocket connection attempt timed out, re-scanning BLE...");}
      isMobileWebSocketConnecting = false;
      resetMobileBleScanState();
    }else if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY){

      responseType = esp32c3Manager.getResponseType();
      arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
      esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);

    switch (responseType)
    {
    case ESP32C3_RESPONSE_TYPE_OK:

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Mobile WebSocket connection established");}

      isMobileWebSocketConnected = true;
      isMobileWebSocketConnecting = false;
      mobileWebSocketLastApiPingMs = millis();

      break;

    case ESP32C3_RESPONSE_TYPE_ERROR:
    {
      char lastEsp32IntermediateResponse[ESP32C3_RESPONSE_BUFFER_SIZE];
      char parsedMobileServerIp[ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE];
      arrayUtil.clearCharArray(lastEsp32IntermediateResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
      arrayUtil.clearCharArray(parsedMobileServerIp, ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE);
      bool hasIntermediateResponse = esp32c3Manager.getLastIntermediateResponse(lastEsp32IntermediateResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
      bool mobileLinkAlreadyEstablished = hasIntermediateResponse && esp32ResponseReportsAlreadyEstablished(lastEsp32IntermediateResponse);
      buildMobileServerIpFromBleName(mobileServerName, parsedMobileServerIp, sizeof(parsedMobileServerIp));

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Error while establishing Mobile WebSocket connection, re-scanning BLE: ");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(tempResponse);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Mobile WSOPEN target suffix=");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(mobileServerName);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(" parsedIp=");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(parsedMobileServerIp);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(" url=");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(DEFAULT_ESP32C3_WEBSOCKET_SERVER_PREFIX);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(parsedMobileServerIp);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(DEFAULT_ESP32C3_MOBILE_WEBSOCKET_SERVER_SUFFIX);}
      if(debuggingSettings.debuggingESP32 && hasIntermediateResponse){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32 && hasIntermediateResponse){serialManager.USBSerialPrint("Last ESP32 line before WSOPEN ERROR: ");}
      if(debuggingSettings.debuggingESP32 && hasIntermediateResponse){serialManager.USBSerialPrintln(lastEsp32IntermediateResponse);}
      if(debuggingSettings.debuggingESP32 && !hasIntermediateResponse){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32 && !hasIntermediateResponse){serialManager.USBSerialPrintln("No intermediate ESP32 line was captured before WSOPEN ERROR");}
      if(debuggingSettings.debuggingESP32 && mobileLinkAlreadyEstablished){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32 && mobileLinkAlreadyEstablished){serialManager.USBSerialPrintln("ESP32 reports mobile link 1 is still established; scheduling forced WSCLOSE=1 before retry");}
      if(debuggingSettings.debuggingESP32 && !mobileLinkAlreadyEstablished){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32 && !mobileLinkAlreadyEstablished){serialManager.USBSerialPrintln("Mobile WSOPEN failed; forcing WSCLOSE=1 to recover any stale ESP32 link state before retry");}

      isMobileWebSocketConnected = false;
      isMobileWebSocketConnecting = false;

      // Always force-close link 1 on WSOPEN error so stale ESP32 link state
      // cannot block future reconnect attempts.
      scheduleForcedWebSocketClose(1);

      break;
    }

    default:
      break;
    }

    }

  }


}

void handleESP32Comms(){

  // Do not consume serial bytes while a BLE scan is in progress (shares the same serial port)
  if(isBleActive)
    return;

  // Always drain the serial buffer and process spontaneous ESP32 events (WiFi disconnect,
  // WebSocket close) even when mobile is not connected — otherwise the pending buffer fills
  // up and WiFi-disconnect events are never detected during the BLE scan pursuit phase.
  esp32c3Manager.setWebSocketRxDebug(debuggingSettings.debuggingESP32 && isFileTransferInProgress); // diagnostic: log WS frame reassembly only during an upload
  int wsMessageResult = esp32c3Manager.checkForWebSocketMessage();
  const bool bufferedWebSocketEvent = esp32c3Manager.wasLastWebSocketMessageBuffered();

  if(wsMessageResult == ESP32C3_MOBILE_WEBSOCKET_MESSAGE_RECEIVED && isMobileWebSocketConnected){

    // Mobile websocket message (link_id=1)
    arrayUtil.clearCharArray(mobileWebSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE);

    if(esp32c3Manager.getWebSocketMessagePayload(mobileWebSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE)){

      handleMobileWebsocketMessage();

    }else{

      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Error while parsing Mobile WebSocket message payload from ESP32C3");}

    }

  }else if(wsMessageResult == ESP32C3_MOBILE_WEBSOCKET_DISCONNECTED && isMobileWebSocketConnected){

    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(bufferedWebSocketEvent ? "Buffered WS_CLOSED:1 observed while AT command was pending" : "Live WS_CLOSED:1 observed from ESP32C3");}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("");}

    isMobileWebSocketConnected = false;
    isMobileWebSocketConnecting = false;
    mobileWebSocketLastApiPingMs = 0;
    esp32c3Manager.clearPendingBuffer(); // drop any partially-reassembled message so it can't carry into the next connection
    restartWebSocketConnectionPursuitIfIdle();

    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Mobile WebSocket connection lost, resuming mobile connection attempts");}

  }else if(wsMessageResult == ESP32C3_WIFI_DISCONNECTED){

    resetESP32WiFiConnection();

    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("WiFi disconnected, will retry connection");}

  }

}

void handleMobileWebsocketMessage(){

  mobileWebSocketLastApiPingMs = millis();

  mobileWebSocketMessageBufferPos = 0;

  while(mobileWebSocketMessageBuffer[mobileWebSocketMessageBufferPos] != NULL_CHAR && mobileWebSocketMessageBufferPos < ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE){

    bufferCharUSB = mobileWebSocketMessageBuffer[mobileWebSocketMessageBufferPos];
    ++mobileWebSocketMessageBufferPos;

    if (bufferCharUSB == NEWLINE) {

      handleMobileWebsocketLine();
      resetMobileWebSocketMessageBuffer();

    }

  }

}

// ─── Height map Z compensation ─────────────────────────────────────────────

// (hmPhysToScanIdx / hmMedian now live next to the height map scan state machine, which fills the map.)

// Post-scan spike removal: any cell that deviates from its 8-neighbour median by more than
// HEIGHT_MAP_OUTLIER_THRESHOLD_MM is a spurious spike (reflective copper, trace edge, bad read) and
// is replaced with that median. This stops the compensation from lifting the tool off the work at
// those cells while leaving the real (smooth) surface untouched. In-place, single pass — fine for
// isolated spikes, which is the case on a flat PCB. Disabled when the threshold is 0.
void filterHeightMapOutliers() {
  if (HEIGHT_MAP_OUTLIER_THRESHOLD_MM <= 0.0f) return;
  if (heightMapGridPointsX < 3 || heightMapGridPointsY < 3) return;

  int replaced = 0;
  for (int row = 0; row < (int)heightMapGridPointsY; row++) {
    for (int col = 0; col < (int)heightMapGridPointsX; col++) {
      float neigh[8];
      int n = 0;
      for (int dr = -1; dr <= 1; dr++) {
        for (int dc = -1; dc <= 1; dc++) {
          if (dr == 0 && dc == 0) continue;
          int r = row + dr, c = col + dc;
          if (r < 0 || r >= (int)heightMapGridPointsY || c < 0 || c >= (int)heightMapGridPointsX) continue;
          neigh[n++] = heightMapRelativeMm[hmPhysToScanIdx((uint16_t)r, (uint16_t)c)];
        }
      }
      if (n < 3) continue;
      float med = hmMedian(neigh, n);
      uint16_t idx = hmPhysToScanIdx((uint16_t)row, (uint16_t)col);
      if (fabsf(heightMapRelativeMm[idx] - med) > HEIGHT_MAP_OUTLIER_THRESHOLD_MM) {
        heightMapRelativeMm[idx] = med;
        replaced++;
      }
    }
  }

  char dbg[64];
  snprintf(dbg, sizeof(dbg), "HeightMap outlier filter: replaced %d cell(s)", replaced);
  serialManager.USBSerialPrint(FROM_CPUB);
  serialManager.USBSerialPrintln(dbg);
}

float interpolateHeightMapZ(float workX, float workY) {
  if (heightMapGridPointsX == 0 || heightMapGridPointsY == 0) return 0.0f;
  float colF = (workX - heightMapOriginX) / heightMapSpacingXmm;
  float rowF = (heightMapOriginY - workY) / heightMapSpacingYmm;
  if (colF < 0.0f) colF = 0.0f;
  if (colF > (float)(heightMapGridPointsX - 1)) colF = (float)(heightMapGridPointsX - 1);
  if (rowF < 0.0f) rowF = 0.0f;
  if (rowF > (float)(heightMapGridPointsY - 1)) rowF = (float)(heightMapGridPointsY - 1);
  uint16_t col0 = (uint16_t)colF;
  uint16_t row0 = (uint16_t)rowF;
  uint16_t col1 = (col0 + 1 < heightMapGridPointsX) ? col0 + 1 : col0;
  uint16_t row1 = (row0 + 1 < heightMapGridPointsY) ? row0 + 1 : row0;
  float tx = colF - (float)col0;
  float ty = rowF - (float)row0;
  float z00 = heightMapRelativeMm[hmPhysToScanIdx(row0, col0)];
  float z10 = heightMapRelativeMm[hmPhysToScanIdx(row0, col1)];
  float z01 = heightMapRelativeMm[hmPhysToScanIdx(row1, col0)];
  float z11 = heightMapRelativeMm[hmPhysToScanIdx(row1, col1)];
  float z0 = z00 + tx * (z10 - z00);
  float z1 = z01 + tx * (z11 - z01);
  return z0 + ty * (z1 - z0);
}

static void rewriteZWordInLine(char* buf, size_t bufSize, float zValue) {
  char newZ[24];
  int newZLen = snprintf(newZ, sizeof(newZ), "Z%.4f", zValue);
  // Replace existing Z word if found
  for (int i = 0; buf[i] != '\0'; i++) {
    if (buf[i] == ';' || buf[i] == '\n' || buf[i] == '\r') break;
    if (buf[i] == '(') { while (buf[i] != '\0' && buf[i] != ')') i++; continue; }
    if (toupper((unsigned char)buf[i]) != 'Z') continue;
    // Confirm the Z is followed by a valid number
    int numStart = i + 1;
    if (buf[numStart] == '-' || buf[numStart] == '+') numStart++;
    if (buf[numStart] != '.' && (buf[numStart] < '0' || buf[numStart] > '9')) continue;
    int numEnd = i + 1;
    if (buf[numEnd] == '-' || buf[numEnd] == '+') numEnd++;
    while (buf[numEnd] == '.' || (buf[numEnd] >= '0' && buf[numEnd] <= '9')) numEnd++;
    int tailLen = (int)strlen(buf + numEnd);
    if (i + newZLen + tailLen + 1 > (int)bufSize) return;
    memmove(buf + i + newZLen, buf + numEnd, tailLen + 1);
    memcpy(buf + i, newZ, newZLen);
    return;
  }
  // Z not found — inject before first line terminator or at string end
  int insertAt = (int)strlen(buf);
  for (int i = 0; buf[i] != '\0'; i++) {
    if (buf[i] == '\n' || buf[i] == '\r' || buf[i] == ';') { insertAt = i; break; }
  }
  int tailLen = (int)strlen(buf + insertAt);
  if (insertAt + 1 + newZLen + tailLen + 1 > (int)bufSize) return;
  memmove(buf + insertAt + 1 + newZLen, buf + insertAt, tailLen + 1);
  buf[insertAt] = ' ';
  memcpy(buf + insertAt + 1, newZ, newZLen);
}

void applyHeightMapZToLine(char* line, size_t bufSize) {
  // Only supported in absolute mode; skip G91 (relative) moves
  if (!outboundAbsoluteMode) return;
  // Determine if this line is G0 or G1 (explicit or modal)
  bool sawG0 = false, sawG1 = false, sawG2 = false, sawG3 = false, sawAnyG = false;
  for (int i = 0; line[i] != '\0'; i++) {
    if (line[i] == ';' || line[i] == '\n' || line[i] == '\r') break;
    if (line[i] == '(') { while (line[i] != '\0' && line[i] != ')') i++; continue; }
    if (toupper((unsigned char)line[i]) != 'G') continue;
    char* ep;
    long g = strtol(line + i + 1, &ep, 10);
    if (ep == line + i + 1) continue;
    sawAnyG = true;
    if (g == 0) sawG0 = true;
    else if (g == 1) sawG1 = true;
    else if (g == 2) sawG2 = true;
    else if (g == 3) sawG3 = true;
    i = (int)(ep - line) - 1;
  }
  bool isG0 = sawG0 || (!sawAnyG && outboundLastMotionMode == 1);
  bool isG1 = sawG1 || (!sawAnyG && outboundLastMotionMode == 2);
  bool isArc = sawG2 || sawG3
    || (!sawAnyG && (outboundLastMotionMode == 3 || outboundLastMotionMode == 4));
  if (!isG0 && !isG1 && !isArc) return;
  float parsedX = 0.0f, parsedY = 0.0f, parsedZ = 0.0f;
  bool hasX = tryParseGCodeWordFloatValue(line, 'X', &parsedX);
  bool hasY = tryParseGCodeWordFloatValue(line, 'Y', &parsedY);
  bool hasZ = tryParseGCodeWordFloatValue(line, 'Z', &parsedZ);
  // Update tracked work position
  float targetX = outboundCurrentWorkX;
  float targetY = outboundCurrentWorkY;
  if (hasX) { outboundCurrentWorkX = parsedX; targetX = parsedX; }
  if (hasY) { outboundCurrentWorkY = parsedY; targetY = parsedY; }
  if (hasZ) outboundCurrentWorkZ = parsedZ;
  // Arcs are deliberately left uncompensated: they hold whatever Z the last compensated move left
  // them at. Their endpoint still has to be TRACKED though -- returning before the parsing above
  // would leave outboundCurrentWork* stale, and every following modal line would then compute its
  // offset at the wrong XY and inject a stale Z.
  if (isArc) return;
  // For G0: only compensate when Z is explicitly in the line
  if (isG0 && !hasZ) return;
  float offset = interpolateHeightMapZ(targetX, targetY);
  rewriteZWordInLine(line, bufSize, outboundCurrentWorkZ + offset);
}

// Emit the next G1 sub-move for a segmented linear G1 command.
static void emitNextLinearSegment() {
  float remaining = jobLineSeg.totalLen - jobLineSeg.sentLen;
  bool isLast = (remaining <= jobLineSeg.stepLen * 1.001f);

  float targetX, targetY, targetZProg;
  if (isLast) {
    targetX     = jobLineSeg.x1;
    targetY     = jobLineSeg.y1;
    targetZProg = jobLineSeg.z1;
  } else {
    float t = (jobLineSeg.sentLen + jobLineSeg.stepLen) / jobLineSeg.totalLen;
    targetX     = jobLineSeg.x0 + t * (jobLineSeg.x1 - jobLineSeg.x0);
    targetY     = jobLineSeg.y0 + t * (jobLineSeg.y1 - jobLineSeg.y0);
    targetZProg = jobLineSeg.z0 + t * (jobLineSeg.z1 - jobLineSeg.z0);
  }

  float offset = interpolateHeightMapZ(targetX, targetY);
  char sub[80];
  snprintf(sub, sizeof(sub), "G1 X%.4f Y%.4f Z%.4f F%.1f",
           targetX, targetY, targetZProg + offset, jobLineSeg.feedRate);

  if (!evaluateAndApplySoftLimitsForOutgoingLine(sub)) {
    jobLineSeg.active = false;
    stopJobWithReset();
    return;
  }
  serialManager.CPUASerialPrintln(sub);
  ++waitForOk;

  if (isLast) {
    outboundCurrentWorkX = jobLineSeg.x1;
    outboundCurrentWorkY = jobLineSeg.y1;
    outboundCurrentWorkZ = jobLineSeg.z1;
    jobLineSeg.active = false;
    ++jobLineIndex;
  } else {
    jobLineSeg.sentLen += jobLineSeg.stepLen;
  }
}

// Emit the next G1 sub-move for a segmented G2/G3 arc command.
static void emitNextArcSegment() {
  float remaining = jobArcSeg.totalAngle - jobArcSeg.angleCompleted;
  bool isLast = (remaining <= jobArcSeg.stepAngle * 1.001f);

  float targetX, targetY, t;
  if (isLast) {
    targetX = jobArcSeg.x1;
    targetY = jobArcSeg.y1;
    t       = 1.0f;
  } else {
    float nextAngle = jobArcSeg.clockwise
      ? jobArcSeg.currentAngle - jobArcSeg.stepAngle
      : jobArcSeg.currentAngle + jobArcSeg.stepAngle;
    targetX = jobArcSeg.cx + jobArcSeg.radius * cosf(nextAngle);
    targetY = jobArcSeg.cy + jobArcSeg.radius * sinf(nextAngle);
    t = (jobArcSeg.angleCompleted + jobArcSeg.stepAngle) / jobArcSeg.totalAngle;
    if (t > 1.0f) t = 1.0f;
  }

  float targetZProg = jobArcSeg.z0 + t * (jobArcSeg.z1 - jobArcSeg.z0);
  float offset = interpolateHeightMapZ(targetX, targetY);
  char sub[80];
  snprintf(sub, sizeof(sub), "G1 X%.4f Y%.4f Z%.4f F%.1f",
           targetX, targetY, targetZProg + offset, jobArcSeg.feedRate);

  if (!evaluateAndApplySoftLimitsForOutgoingLine(sub)) {
    jobArcSeg.active = false;
    stopJobWithReset();
    return;
  }
  serialManager.CPUASerialPrintln(sub);
  ++waitForOk;

  if (isLast) {
    outboundCurrentWorkX = jobArcSeg.x1;
    outboundCurrentWorkY = jobArcSeg.y1;
    outboundCurrentWorkZ = jobArcSeg.z1;
    jobArcSeg.active = false;
    ++jobLineIndex;
  } else {
    jobArcSeg.angleCompleted += jobArcSeg.stepAngle;
    if (jobArcSeg.clockwise)
      jobArcSeg.currentAngle -= jobArcSeg.stepAngle;
    else
      jobArcSeg.currentAngle += jobArcSeg.stepAngle;
  }
}

// Check whether the given line starts a long G1 or a G2/G3 arc, and if so
// activate the corresponding segmentation state for height-map Z compensation.
// Returns true when segmentation starts; caller must NOT also send the original
// line or call applyHeightMapZToLine.
bool tryStartSegmentation(char* line) {
  if (!outboundAbsoluteMode) return false;

  // First pass: detect G-words and apply inline modal state changes
  bool sawG1 = false, sawG2 = false, sawG3 = false, sawAnyMotionG = false;
  for (int i = 0; line[i] != '\0'; i++) {
    if (line[i] == ';' || line[i] == '\n' || line[i] == '\r') break;
    if (line[i] == '(') { while (line[i] != '\0' && line[i] != ')') i++; continue; }
    if (toupper((unsigned char)line[i]) != 'G') continue;
    char* ep;
    long g = strtol(line + i + 1, &ep, 10);
    if (ep == line + i + 1) continue;
    if      (g == 1)  { sawG1 = true; sawAnyMotionG = true; }
    else if (g == 2)  { sawG2 = true; sawAnyMotionG = true; }
    else if (g == 3)  { sawG3 = true; sawAnyMotionG = true; }
    else if (g == 90) { outboundAbsoluteMode = true; }
    else if (g == 91) { outboundAbsoluteMode = false; }
    else if (g == 21) { outboundMetricUnits  = true; }
    else if (g == 20) { outboundMetricUnits  = false; }
    i = (int)(ep - line) - 1;
  }

  if (!outboundAbsoluteMode) return false;
  if (!outboundMetricUnits)  return false; // segmentation emits mm coords; skip imperial files

  bool isG1 = sawG1 || (!sawAnyMotionG && outboundLastMotionMode == 2);
  bool isG2 = sawG2;
  bool isG3 = sawG3;
  if (!isG1 && !isG2 && !isG3) return false;

  // Parse position and arc words
  float parsedX = 0.0f, parsedY = 0.0f, parsedZ = 0.0f;
  float parsedI = 0.0f, parsedJ = 0.0f, parsedF = 0.0f;
  bool hasX = tryParseGCodeWordFloatValue(line, 'X', &parsedX);
  bool hasY = tryParseGCodeWordFloatValue(line, 'Y', &parsedY);
  bool hasZ = tryParseGCodeWordFloatValue(line, 'Z', &parsedZ);
  bool hasI = tryParseGCodeWordFloatValue(line, 'I', &parsedI);
  bool hasJ = tryParseGCodeWordFloatValue(line, 'J', &parsedJ);
  bool hasF = tryParseGCodeWordFloatValue(line, 'F', &parsedF);

  if (hasF) outboundCurrentFeedRate = parsedF;
  float feedRate = outboundCurrentFeedRate;

  float x0 = outboundCurrentWorkX;
  float y0 = outboundCurrentWorkY;
  float z0 = outboundCurrentWorkZ;
  float x1 = hasX ? parsedX : x0;
  float y1 = hasY ? parsedY : y0;
  float z1 = hasZ ? parsedZ : z0;

  if (isG1) {
    float dx = x1 - x0, dy = y1 - y0;
    float len = sqrtf(dx*dx + dy*dy);
    float minSpacing = heightMapSpacingXmm < heightMapSpacingYmm
      ? heightMapSpacingXmm : heightMapSpacingYmm;
    // Only segment if the move spans more than one grid cell
    if (minSpacing <= 0.0f || len <= minSpacing) return false;

    jobLineSeg.active   = true;
    jobLineSeg.x0       = x0;  jobLineSeg.y0    = y0;  jobLineSeg.z0    = z0;
    jobLineSeg.x1       = x1;  jobLineSeg.y1    = y1;  jobLineSeg.z1    = z1;
    jobLineSeg.totalLen = len;
    jobLineSeg.sentLen  = 0.0f;
    jobLineSeg.stepLen  = minSpacing * 0.5f;
    jobLineSeg.feedRate = feedRate;
    outboundLastMotionMode = 2;
    return true;
  }

  // Arcs are not height-map compensated at all. Segmenting them was the only way they ever could be
  // (applyHeightMapZToLine covers G0/G1 only), so declining here means an arc is forwarded untouched
  // and simply keeps the Z the preceding compensated move left it at. applyHeightMapZToLine still
  // tracks the arc's endpoint so the following lines compensate from the right position.
  // The emitNextArcSegment machinery below is left in place, unreachable, to make this reversible.
  if (isG2 || isG3)
    return false;

#if 0  // ─── arc segmentation, disabled: arcs are not compensated ───
  if (isG2 || isG3) {
    if (!hasI && !hasJ) return false;
    bool clockwise = isG2;
    float cx     = x0 + parsedI;
    float cy     = y0 + parsedJ;
    float radius = sqrtf(parsedI*parsedI + parsedJ*parsedJ);
    if (radius < 0.001f) return false;

    float startAngle = atan2f(y0 - cy, x0 - cx);
    float endAngle   = atan2f(y1 - cy, x1 - cx);
    float totalAngle;
    if (clockwise) {
      totalAngle = startAngle - endAngle;
      if (totalAngle <= 0.0f) totalAngle += 2.0f * 3.14159265358979323846f;
    } else {
      totalAngle = endAngle - startAngle;
      if (totalAngle <= 0.0f) totalAngle += 2.0f * 3.14159265358979323846f;
    }

    float minSpacing = heightMapSpacingXmm < heightMapSpacingYmm
      ? heightMapSpacingXmm : heightMapSpacingYmm;
    if (minSpacing <= 0.0f) minSpacing = 0.5f;
    float stepAngle = (minSpacing * 0.5f) / radius;
    if (stepAngle > totalAngle) stepAngle = totalAngle;

    jobArcSeg.active         = true;
    jobArcSeg.cx             = cx;         jobArcSeg.cy            = cy;
    jobArcSeg.radius         = radius;
    jobArcSeg.currentAngle   = startAngle;
    jobArcSeg.totalAngle     = totalAngle;
    jobArcSeg.angleCompleted = 0.0f;
    jobArcSeg.stepAngle      = stepAngle;
    jobArcSeg.z0             = z0;          jobArcSeg.z1           = z1;
    jobArcSeg.feedRate       = feedRate;
    jobArcSeg.x1             = x1;          jobArcSeg.y1           = y1;
    jobArcSeg.clockwise      = clockwise;
    outboundLastMotionMode = clockwise ? 3 : 4;
    return true;
  }
#endif  // ─── end arc segmentation ───

  return false;
}

// ─────────────────────────────────────────────────────────────────────────────

void handleJob(){

  if(openedReadFileName[0] == NULL_CHAR)
    return;

  if(!isJobRunning)
    return;

  if (isJobPaused)
    return;

  // Status first, ahead of every gate below.
  //
  // '?' is a REALTIME command: GRBL answers it from its input handler, it never enters the line
  // buffer and it produces no "ok". There is nothing to wait for and nothing it can disturb, which
  // is why it must not sit behind `waitForOk` -- as it did.
  //
  // Behind that gate the poll could only run in the gap between an "ok" and the next line, and an
  // "ok" is not always prompt: GRBL withholds it for anything that has to synchronize with the end
  // of motion (M3/M5, a G4 dwell, a tool change) and for as long as a HOLD lasts. So the status
  // froze for exactly as long as the machine was doing the most interesting things, and the app sat
  // on a stale state and position throughout. Nothing else covers it -- askGRBLStatus() bails out
  // while a job runs, so this is the only status poll during a job.
  //
  // The trailing NEWLINE and the ++waitForOk that went with it are gone too. That newline was an
  // empty line sent only so GRBL's "ok" for it would balance the counter (protocol.c: "Empty line.
  // For syncing purposes."); with the poll out of the counter's way neither is needed, and the line
  // slot it used to spend goes back to the job.
  if(timerUtil.statusTimer()){

    serialManager.CPUASerialWrite(GRBL_STATUS_QUERY); // realtime: no newline, no 'ok' to wait for
    return;

  }

  if (isManualSpindleRampActive)
    return;
  
  if(waitForOk > 0)
    return;

  // Drain any pending height-map segmented sub-moves before reading the next SD line
  if (jobLineSeg.active) { emitNextLinearSegment(); return; }
  if (jobArcSeg.active)  { emitNextArcSegment();    return; }

  // Ensure dry-run feed rate is applied before forwarding the first dry-run move.
  if(isDryRunActive && pendingDryRunFeedRate) {
    char dryRunFeedCmd[32];
    snprintf(dryRunFeedCmd, sizeof(dryRunFeedCmd), "G1 F%d", DRY_RUN_FEED_RATE_MM_PER_MIN);
    serialManager.CPUASerialPrintln(dryRunFeedCmd);
    ++waitForOk;
    pendingDryRunFeedRate = false;
    return;
  }
 
  // Do NOT pre-fill grblHAL's planner while it is running the parking resume. resumeJob clears
  // isJobPaused immediately, and grbl does not execute queued lines until the restore finishes,
  // so streaming here looked free. It is not: it leaves SECONDS of job moves sitting in the
  // planner during the restore. If the lid is opened in that window and the safety-door request
  // does not stop grbl (state_await_resume can swallow it — no physical door input is wired to
  // CPUA), grbl runs those queued moves as ordinary cycle motion with the spindle already
  // de-energized — cutting into the material with a dead tool. Waiting until the machine
  // actually reaches Run keeps the planner empty, so there is nothing to run away with. This
  // check sits AFTER the status query above on purpose: that poll is what clears isResuming, so
  // moving it earlier would deadlock the job here.
  if (isResuming)
    return;

  //here we read the file line by line and send it to GRBL
  arrayUtil.clearCharArray(gcodeLineBuffer, LINE_BUFFER_CHARACTERS);

  if (isSpindleLineHeld && heldSpindleLineIsJob) {

    // Release the motion line that was held while the spindle ramped. It re-enters
    // the normal pipeline below exactly like a freshly read SD line (the ramp check
    // now resolves to a direct forward since the RPM already matches, then height
    // map and soft limits apply as usual).
    strncpy(gcodeLineBuffer, heldSpindleLineBuffer, LINE_BUFFER_CHARACTERS - 1);
    isSpindleLineHeld = false;

  } else if(!sdCardManager.readGCodeLine(gcodeLineBuffer, LINE_BUFFER_CHARACTERS)) {

    isJobRunning = false;
    isDryRunActive = false;
    pendingDryRunFeedRate = false;
    isJobPaused = false;
    jobStartMs = 0;

    // rewind file so the same file can be started again without re-opening
    sdCardManager.closeFile();
    sdCardManager.openGCodeFiletoRead(openedReadFileName);
    jobLineIndex = 0;
    // Keep the height map: the same file stays open, so the map stays valid for the next run. Only
    // clear the "used" flag so compensation re-applies if this file is run again.
    heightMapUsedForCurrentFile = false;
    jobLineSeg.active = false;
    jobArcSeg.active  = false;

    if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln("Reached end of opened SD card file, stopping job."); }
    return;

  }

  if(gcodeLineBuffer[0] != NULL_CHAR){

    if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln("Reading line from SD card.."); }

    if (isDryRunActive) {

      // Dry run: bypass spindle ramp entirely
      if (grblUtil.shouldSkipLineForDryRun(gcodeLineBuffer)) {

        ++jobLineIndex;
        if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrint("Dry run: line skipped: "); }
        if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln(gcodeLineBuffer); }
        return;

      }

      char dryRunLineBuffer[LINE_BUFFER_CHARACTERS];
      arrayUtil.clearCharArray(dryRunLineBuffer, LINE_BUFFER_CHARACTERS);
      grblUtil.sanitizeLineForDryRun(gcodeLineBuffer, dryRunLineBuffer, LINE_BUFFER_CHARACTERS);

      if(!evaluateAndApplySoftLimitsForOutgoingLine(dryRunLineBuffer)) {
        stopJobWithReset();
        return;
      }

      serialManager.CPUASerialPrintln(dryRunLineBuffer);
      ++waitForOk;
      ++jobLineIndex;

      if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrint("Dry run: line sent: "); }
      if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln(dryRunLineBuffer); }

      return;

    }

    // Spindle-start gate. A job can only reach this point with the lid open when the housing
    // preference allows motion with the lid open and the spindle was off (otherwise the door
    // interlock already paused). The file now wants the spindle ON: hold the line and pause
    // instead of forwarding it. Continuing with a dead spindle would drive the tool into the
    // material, so pausing — not skipping — is the safe answer. Closing the lid and resuming
    // releases the held line and the spindle starts normally; resuming with the lid still
    // open lands back here and pauses again.
    if (isDoorInterlockActive() && lineCommandsSpindleOn(gcodeLineBuffer)) {

      arrayUtil.clearCharArray(heldSpindleLineBuffer, LINE_BUFFER_CHARACTERS);
      strncpy(heldSpindleLineBuffer, gcodeLineBuffer, LINE_BUFFER_CHARACTERS - 1);
      heldSpindleLineIsJob = true;
      isSpindleLineHeld = true;

      pauseJobInternal(true);

      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrint("Job paused, spindle start requested with the lid open: ");
      serialManager.USBSerialPrintln(gcodeLineBuffer);

      return;

    }

    if (handleSpindleRampCommand(gcodeLineBuffer, true)) {

      // A held line is NOT counted here: it will be re-processed and counted
      // when it is released after the ramp completes.
      if (!(isSpindleLineHeld && heldSpindleLineIsJob))
        ++jobLineIndex;

      if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrint("G-code line ramped instead of forwarded directly: "); }
      if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln(gcodeLineBuffer); }

      return;
    }

    if (heightMapCompensationEnabled && heightMapReady) {
      if (tryStartSegmentation(gcodeLineBuffer)) {
        if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrint("G-code line queued for segmentation: "); }
        if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln(gcodeLineBuffer); }
        return;
      }
      applyHeightMapZToLine(gcodeLineBuffer, LINE_BUFFER_CHARACTERS);
    }

    if(!evaluateAndApplySoftLimitsForOutgoingLine(gcodeLineBuffer)) {
      stopJobWithReset();
      return;
    }
    
    //here send the gcode line to GRBL
    serialManager.CPUASerialPrintln(gcodeLineBuffer);
    ++waitForOk;
    ++jobLineIndex;

    if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrint("G-code line sent: "); }
    if(debuggingSettings.debuggingJob) { serialManager.USBSerialPrintln(gcodeLineBuffer); }     

  }

}

float getOpenedFileJobProgressPercent() {

  if(openedReadFileName[0] == NULL_CHAR || openedReadFileTotalLines == 0)
    return 0.0f;

  float progressPercent = ((float)jobLineIndex * 100.0f) / (float)openedReadFileTotalLines;

  if(progressPercent < 0.0f)
    progressPercent = 0.0f;

  if(progressPercent > 100.0f)
    progressPercent = 100.0f;

  return roundf(progressPercent * 100.0f) / 100.0f;

}

void resetFileTransferState(bool deleteTemporaryFile) {

  sdCardManager.closeProcessingFile();
  sdCardManager.closeWriteFile();

  if(deleteTemporaryFile && transferFileName[0] != NULL_CHAR)
    sdCardManager.deleteFile(transferFileName);

  transferFileLines = 0;
  isFileTransferInProgress = false;
  fileTransferHasExistingMetadata = false;
  fileTransferMetadataChecked = false;
  fileTransferLastChunkActivityMs = 0;
  transferFileYear = 0;
  transferFileMonth = 0;
  transferFileDay = 0;
  transferFileHour = 0;
  transferFileMinute = 0;
  transferFileSecond = 0;
  gcodeBoundsUtil.reset();
  arrayUtil.clearCharArray(transferFileName, SD_MAX_FILE_NAME_LENGTH);

}

void buildTransferMetadataLine(char* buffer, size_t bufferSize) {

  if (gcodeBoundsUtil.hasBounds()) {
    snprintf(
      buffer,
      bufferSize,
      ";MINICNC_BOUNDS EXACT=%d UNITS=MM MINX=%.3f MAXX=%.3f SIZEX=%.3f MINY=%.3f MAXY=%.3f SIZEY=%.3f MINZ=%.3f MAXZ=%.3f SIZEZ=%.3f DURATION_SEC=%lu",
      gcodeBoundsUtil.isExact() ? 1 : 0,
      gcodeBoundsUtil.getMinX(),
      gcodeBoundsUtil.getMaxX(),
      gcodeBoundsUtil.getMaxX() - gcodeBoundsUtil.getMinX(),
      gcodeBoundsUtil.getMinY(),
      gcodeBoundsUtil.getMaxY(),
      gcodeBoundsUtil.getMaxY() - gcodeBoundsUtil.getMinY(),
      gcodeBoundsUtil.getMinZ(),
      gcodeBoundsUtil.getMaxZ(),
      gcodeBoundsUtil.getMaxZ() - gcodeBoundsUtil.getMinZ(),
      gcodeBoundsUtil.getEstimatedDurationSecondsRounded()
    );
  } else {
    snprintf(
      buffer,
      bufferSize,
      ";MINICNC_BOUNDS EXACT=0 UNITS=MM EMPTY=1 DURATION_SEC=%lu",
      gcodeBoundsUtil.getEstimatedDurationSecondsRounded()
    );
  }

}

bool finalizeTransferredFile() {

  if (transferFileName[0] == NULL_CHAR) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_NOT_STARTED;
    return false;
  }

  if (gcodeBoundsUtil.hasBounds()) {
    const float sizeX = gcodeBoundsUtil.getMaxX() - gcodeBoundsUtil.getMinX();
    const float sizeY = gcodeBoundsUtil.getMaxY() - gcodeBoundsUtil.getMinY();
    const float sizeZ = gcodeBoundsUtil.getMaxZ() - gcodeBoundsUtil.getMinZ();

    if (sizeX > MAX_WORK_DIMENSION_XY_MM || sizeY > MAX_WORK_DIMENSION_XY_MM || sizeZ > MAX_WORK_DIMENSION_Z_MM) {
      fileTransferFailureReason = FILE_TRANSFER_FAILURE_DIMENSIONS_EXCEEDED;
      // Kept for the failure response: resetFileTransferState clears the bounds util, so the
      // measured size has to be saved before it goes away.
      fileTransferFailureSizeX = sizeX;
      fileTransferFailureSizeY = sizeY;
      fileTransferFailureSizeZ = sizeZ;
      resetFileTransferState(true);
      return false;
    }
  }

  char finalFileName[SD_MAX_FILE_NAME_LENGTH];
  arrayUtil.clearCharArray(finalFileName, SD_MAX_FILE_NAME_LENGTH);
  strcat(finalFileName, transferFileName);
  arrayUtil.removeAllSubstrings(finalFileName, TMP_FILENAME);

  if (!sdCardManager.openProcessingFileToRead(transferFileName)) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_SD_READ;
    return false;
  }

  // openTextFileForWriting uses O_CREAT | O_TRUNC — creates or overwrites atomically
  if (!sdCardManager.openTextFileForWriting(finalFileName)) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_FILE_CREATION;
    sdCardManager.closeProcessingFile();
    return false;
  }

  if (transferFileYear != 0)
    sdCardManager.setWriteFileTimestamp(transferFileYear, transferFileMonth, transferFileDay, transferFileHour, transferFileMinute, transferFileSecond);

  FileHashUtil fileHashUtil;
  if (!fileHashUtil.start()) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_HASH;
    sdCardManager.closeProcessingFile();
    sdCardManager.closeWriteFile();
    sdCardManager.deleteFile(finalFileName);
    return false;
  }

  if (!fileTransferHasExistingMetadata) {
    char metadataLine[256];
    buildTransferMetadataLine(metadataLine, sizeof(metadataLine));

    // Evaluated separately so the reported cause distinguishes an SD write failure (card
    // full / IO error) from a hashing failure, rather than lumping both together.
    const bool metadataWritten = sdCardManager.writeTextLine(metadataLine);

    if (!metadataWritten || !fileHashUtil.updateLine(metadataLine)) {
      fileTransferFailureReason = metadataWritten
        ? FILE_TRANSFER_FAILURE_HASH
        : FILE_TRANSFER_FAILURE_SD_WRITE;
      fileHashUtil.abort();
      sdCardManager.closeProcessingFile();
      sdCardManager.closeWriteFile();
      sdCardManager.deleteFile(finalFileName);
      return false;
    }
  }

  char processingLineBuffer[LINE_BUFFER_CHARACTERS];
  while (sdCardManager.readProcessingLine(processingLineBuffer, sizeof(processingLineBuffer))) {
    const bool lineWritten = sdCardManager.writeTextLine(processingLineBuffer);

    if (!lineWritten || !fileHashUtil.updateLine(processingLineBuffer)) {
      fileTransferFailureReason = lineWritten
        ? FILE_TRANSFER_FAILURE_HASH
        : FILE_TRANSFER_FAILURE_SD_WRITE;
      fileHashUtil.abort();
      sdCardManager.closeProcessingFile();
      sdCardManager.closeWriteFile();
      sdCardManager.deleteFile(finalFileName);
      return false;
    }
  }

  sdCardManager.closeProcessingFile();
  sdCardManager.closeWriteFile();

  char fileHashHex[(SHA256_RESULT_BYTES * 2) + 1];
  arrayUtil.clearCharArray(fileHashHex, sizeof(fileHashHex));
  if (!fileHashUtil.finishHex(fileHashHex, sizeof(fileHashHex))) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_HASH;
    sdCardManager.deleteFile(finalFileName);
    return false;
  }

  char hashFilename[SD_MAX_FILE_NAME_LENGTH];
  arrayUtil.clearCharArray(hashFilename, SD_MAX_FILE_NAME_LENGTH);
  if (!prepareHashSidecarForOverwrite(finalFileName, hashFilename, sizeof(hashFilename))) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_HASH;
    sdCardManager.deleteFile(finalFileName);
    return false;
  }

  if (!sdCardManager.openTextFileForWriting(hashFilename)) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_FILE_CREATION;
    sdCardManager.deleteFile(finalFileName);
    return false;
  }

  if (transferFileYear != 0)
    sdCardManager.setWriteFileTimestamp(transferFileYear, transferFileMonth, transferFileDay, transferFileHour, transferFileMinute, transferFileSecond);

  if (!sdCardManager.writeText(fileHashHex)) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_SD_WRITE;
    sdCardManager.closeWriteFile();
    sdCardManager.deleteFile(hashFilename);
    sdCardManager.deleteFile(finalFileName);
    return false;
  }

  sdCardManager.closeWriteFile();

  // The final file and hash are complete — keep them even if the TMP cleanup fails.
  // A leftover TMP file (wrong extension, filtered from listings) is preferable to
  // silently destroying a successfully uploaded file.
  sdCardManager.deleteFile(transferFileName);

  arrayUtil.clearCharArray(transferFileName, SD_MAX_FILE_NAME_LENGTH);
  transferFileYear = 0;
  transferFileMonth = 0;
  transferFileDay = 0;
  transferFileHour = 0;
  transferFileMinute = 0;
  transferFileSecond = 0;
  gcodeBoundsUtil.reset();
  return true;

}

bool processTransferChunkPayload(const char* chunkPayload) {

  if (transferFileLines == 0 || !sdCardManager.isWriteFileOpen()) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_NOT_STARTED;
    return false;
  }

  if (chunkPayload == nullptr) {
    fileTransferFailureReason = FILE_TRANSFER_FAILURE_INVALID_CHUNK;
    return false;
  }

  fileTransferLastChunkActivityMs = millis();

  arrayUtil.clearCharArray(fileChunkLines, FILE_CHUNK_LINES_BUFFER);
  snprintf(fileChunkLines, sizeof(fileChunkLines), "%s", chunkPayload);

  char* tempLine = strtok(fileChunkLines, LINE_CHUNK_SEPARATOR);
  while (tempLine != NULL) {

    if (!fileTransferMetadataChecked) {
      fileTransferMetadataChecked = true;
      if (GCodeBoundsUtil::isMetadataLine(tempLine))
        fileTransferHasExistingMetadata = true;
    }

    if (!fileTransferHasExistingMetadata)
      gcodeBoundsUtil.processLine(tempLine);

    // An SD write failure mid-upload (card full, IO error) is its own cause — previously it
    // returned with no reason set and surfaced to the client as a rename failure.
    if (!sdCardManager.writeTextLine(tempLine)) {
      fileTransferFailureReason = FILE_TRANSFER_FAILURE_SD_WRITE;
      return false;
    }

    --transferFileLines;

    if (transferFileLines == 0)
      break;

    tempLine = strtok(NULL, LINE_CHUNK_SEPARATOR);
  }

  if (transferFileLines != 0)
    return true;

  sdCardManager.closeWriteFile();
  isFileTransferInProgress = false;
  fileTransferLastChunkActivityMs = 0;

  bool finalized = finalizeTransferredFile();
  if (!finalized) {
    if (fileTransferFailureReason == FILE_TRANSFER_FAILURE_NONE)
      fileTransferFailureReason = FILE_TRANSFER_FAILURE_FINALIZE;
    resetFileTransferState(true);
  }

  return finalized;

}

void resetFileDownloadState() {

  sdCardManager.closeDownloadFile();
  isFileDownloadInProgress = false;
  fileDownloadLastChunkActivityMs = 0;
  arrayUtil.clearCharArray(downloadFileName, SD_MAX_FILE_NAME_LENGTH);
  arrayUtil.clearCharArray(downloadFileChunkBuffer, DOWNLOAD_FILE_CHUNK_BUFFER);
  downloadFileCurrentLine = 0;
  downloadFileTotalLines = 0;
  downloadChunkSendFailures = 0;

}

bool sendJsonResponse() {

  if (currentInputSource == INPUT_SOURCE_MOBILE_WEBSOCKET)
    return esp32c3Manager.sendWebSocketMessage(jsonResponseCharBuffer, 1);
  else {
    serialManager.USBSerialPrintln(jsonResponseCharBuffer);
    return true;
  }

}

void pushHomingCompletedEvent(bool success) {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = homingInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_HOMING_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;
  if (success)
    homedSincePowerOn = true;
  isHoming = false;

}

// Pushed when the paced resume spindle-restore ramp finishes, so the front-end
// can clear the transient "resuming" state it shows after a RESUME_JOB ack.
void pushResumeCompletedEvent() {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = resumeInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_RESUME_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;

}

void pushProbeZ0CompletedEvent(bool success) {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = probeZ0MacroInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_PROBE_Z0_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;
  isProbeZ0MacroRunning = false;

}

void pushProbeToolLengthCompletedEvent(bool success) {

  if (!jsonSettings.enableJsonResponse)
    return;

  InputSource savedSource = currentInputSource;
  currentInputSource = probeToolLengthMacroInputSource;

  clearResponseJsonBuffer();
  responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_PROBE_TOOL_LENGTH_COMPLETED_STR;
  responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
  if (success)
    responseJsonBuffer[JSON_KEY_TOOL_LENGTH_STR] = toolLength;
  serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  sendJsonResponse();

  currentInputSource = savedSource;
  isProbeToolLengthMacroRunning = false;
  probeToolLengthZCaptured = false;

}

void pushLaserEdgeProbingCompletedEvent(bool success) {

  InputSource savedSource = currentInputSource;

  if (jsonSettings.enableJsonResponse) {
    currentInputSource = laserEdgeProbeInputSource;

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_LASER_EDGE_PROBING_COMPLETED_STR;
    responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
    if (success) {
      responseJsonBuffer[JSON_KEY_LASER_EDGE_X_MIN_STR] = laserEdgeProbeXNeg;
      responseJsonBuffer[JSON_KEY_LASER_EDGE_Y_MIN_STR] = laserEdgeProbeYNeg;
    }
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

    currentInputSource = savedSource;
  }

  isLaserEdgeProbingActive = false;
  isMacroRunning = false;
  laserEdgeProbeSuppressAlarmAbort = false;
  laserEdgeProbeWaitingForInitialUnlock = false;
  laserEdgeProbeState = LASER_EDGE_PROBE_IDLE;
  laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
  laserEdgeProbeCurrentDir = PROBE_DIR_XNEG;
  laserEdgeProbeFirstDir = PROBE_DIR_XNEG;
  laserEdgeProbeSecondDir = PROBE_DIR_YNEG;
  laserEdgeProbeXOffset = 0.0f;
  laserEdgeProbeYOffset = 0.0f;
  laserEdgeProbeXNeg = 0.0f;
  laserEdgeProbeYNeg = 0.0f;
  laserEdgeProbeTraveled = 0.0f;
  laserEdgeProbeRoughEdge = 0.0f;
  laserEdgeProbeBaselineSampleSum = 0.0f;
  laserEdgeProbeReturnTargetX = 0.0f;
  laserEdgeProbeReturnTargetY = 0.0f;
  laserEdgeProbeSetOriginStep = 0;
  laserEdgeProbeBaselineSampleCount = 0;
  laserEdgeProbeReturnMotionObserved = false;
  laserEdgeProbeRecoveryNeedsReset = false;

}

void pushLaserMaterialBoundsCompletedEvent(bool success) {

  InputSource savedSource = currentInputSource;

  if (jsonSettings.enableJsonResponse) {
    currentInputSource = laserMaterialBoundsProbeInputSource;

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_TYPE_STR] = JSON_VALUE_EVENT_LASER_MATERIAL_BOUNDS_COMPLETED_STR;
    responseJsonBuffer[JSON_KEY_RESULT_STR] = success ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
    if (success) {
      lastDetectedMaterialCenterX = laserMaterialBoundsProbeCenterX;
      lastDetectedMaterialCenterY = laserMaterialBoundsProbeCenterY;
      hasLastDetectedMaterialCenter = true;
      responseJsonBuffer[JSON_KEY_LASER_EDGE_X_MIN_STR] = laserMaterialBoundsProbeXMin;
      responseJsonBuffer[JSON_KEY_LASER_EDGE_X_MAX_STR] = laserMaterialBoundsProbeXMax;
      responseJsonBuffer[JSON_KEY_LASER_EDGE_Y_MIN_STR] = laserMaterialBoundsProbeYMin;
      responseJsonBuffer[JSON_KEY_LASER_EDGE_Y_MAX_STR] = laserMaterialBoundsProbeYMax;
      responseJsonBuffer[JSON_KEY_MATERIAL_CENTER_X_STR] = laserMaterialBoundsProbeCenterX;
      responseJsonBuffer[JSON_KEY_MATERIAL_CENTER_Y_STR] = laserMaterialBoundsProbeCenterY;
    } else {
      if (laserMaterialBoundsProbeAbortStage != nullptr)
        responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_STAGE_STR] = laserMaterialBoundsProbeAbortStage;
      if (laserMaterialBoundsProbeAbortDetail != nullptr)
        responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ABORT_DETAIL_STR] = laserMaterialBoundsProbeAbortDetail;
    }
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

    currentInputSource = savedSource;
  }

  isLaserMaterialBoundsProbingActive = false;
  isMacroRunning = false;
  laserMaterialBoundsProbeSuppressAlarmAbort = false;
  laserMaterialBoundsProbeWaitingForInitialUnlock = false;
  laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_IDLE;
  laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
  laserMaterialBoundsProbeCurrentDir = PROBE_DIR_XNEG;
  laserMaterialBoundsProbeBaselineMm = 0.0f;
  laserMaterialBoundsProbeStartX = 0.0f;
  laserMaterialBoundsProbeStartY = 0.0f;
  laserMaterialBoundsProbeXMin = 0.0f;
  laserMaterialBoundsProbeXMax = 0.0f;
  laserMaterialBoundsProbeYMin = 0.0f;
  laserMaterialBoundsProbeYMax = 0.0f;
  laserMaterialBoundsProbeRoughEdge = 0.0f;
  laserMaterialBoundsProbeCoarseCurrentTarget = 0.0f;
  laserMaterialBoundsProbeTraveled = 0.0f;
  laserMaterialBoundsProbeCenterX = 0.0f;
  laserMaterialBoundsProbeCenterY = 0.0f;
  laserMaterialBoundsProbeReturnTargetX = 0.0f;
  laserMaterialBoundsProbeReturnTargetY = 0.0f;
  laserMaterialBoundsProbeBaselineSampleSum = 0.0f;
  laserMaterialBoundsProbeBaselineSampleCount = 0;
  laserMaterialBoundsProbeStepSentMs = 0;
  laserMaterialBoundsProbeReturnMotionObserved = false;
  laserMaterialBoundsProbeRecoveryNeedsReset = false;
  laserMaterialBoundsProbeAbortStage = nullptr;
  laserMaterialBoundsProbeAbortDetail = nullptr;

}

void abortLaserMaterialBoundsProbe(const char* stage, const char* detail) {

  laserMaterialBoundsProbeAbortStage = stage;
  laserMaterialBoundsProbeAbortDetail = detail;
  pushLaserMaterialBoundsCompletedEvent(false);

}

void handleLaserMaterialBoundsProbing() {

  if (!isLaserMaterialBoundsProbingActive)
    return;

  if (laserMaterialBoundsProbeWaitingForInitialUnlock) {
    if (currentMachineState == ALARM)
      return;
    if (currentMachineState != IDLE)
      return;
    laserMaterialBoundsProbeWaitingForInitialUnlock = false;
    laserMaterialBoundsProbeSuppressAlarmAbort = false;
  }

  if (currentMachineState == ALARM && !laserMaterialBoundsProbeSuppressAlarmAbort)
    return;

  char cmd[MACRO_LINE_BUFFER_CHARS];

  switch (laserMaterialBoundsProbeState) {

    case LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        float dirSign = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS || laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        bool isX = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS);
        float startAxis = isX ? laserMaterialBoundsProbeStartX : laserMaterialBoundsProbeStartY;
        laserMaterialBoundsProbeCoarseCurrentTarget = startAxis + dirSign * LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
        laserMaterialBoundsProbeTraveled = LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
        if (isX)
          snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", laserMaterialBoundsProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
        else
          snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", laserMaterialBoundsProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserMaterialBoundsProbeStepSentMs = millis();
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_MONITOR;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_MONITOR) {
        if (laserMaterialBoundsProbeTraveled <= LASER_EDGE_PROBE_COARSE_INCREMENT_MM &&
            millis() - laserMaterialBoundsProbeStepSentMs < 400) return;

        if (currentMachineState == IDLE) {
          abortLaserMaterialBoundsProbe("COARSE_SCAN", "no_edge_in_range");
          return;
        }

        if (currentMachineState != RUN) return;

        bool isX = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS);
        float dirSign = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS || laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;

        if (waitForOk == 0 && laserMaterialBoundsProbeTraveled < LASER_EDGE_PROBE_MAX_TRAVEL_MM) {
          float currentAxisPos = isX ? currentJobPositionX : currentJobPositionY;
          float distToTarget = fabsf(currentAxisPos - laserMaterialBoundsProbeCoarseCurrentTarget);
          if (distToTarget <= LASER_EDGE_PROBE_COARSE_TRIGGER_RANGE_MM) {
            laserMaterialBoundsProbeCoarseCurrentTarget += dirSign * LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
            laserMaterialBoundsProbeTraveled += LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
            if (isX)
              snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", laserMaterialBoundsProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
            else
              snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", laserMaterialBoundsProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
            serialManager.CPUASerialPrintln(cmd);
            ++waitForOk;
          }
        }

        float distMm = 0.0f;
        bool outOfRange = false;
        bool readOk = readDistanceLaserMm(&distMm, &outOfRange);

        bool edgeDetected = false;
        if (!readOk && outOfRange) {
          edgeDetected = true;
        } else if (readOk) {
          edgeDetected = fabsf(distMm - laserMaterialBoundsProbeBaselineMm) >= LASER_EDGE_PROBE_THRESHOLD_MM;
        }

        if (edgeDetected) {
          serialManager.CPUASerialWrite(GRBL_HOLD);
          laserMaterialBoundsProbeSuppressAlarmAbort = true;
          laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_COARSE_STOP;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_HOLD;
        }
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_COARSE_STOP: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_HOLD) {
        if (currentMachineState != HOLD && currentMachineState != IDLE) return;
        if (currentFeedRate > 0) return;
        switch (laserMaterialBoundsProbeCurrentDir) {
          case PROBE_DIR_XNEG:
          case PROBE_DIR_XPOS:
            laserMaterialBoundsProbeRoughEdge = currentJobPositionX;
            break;
          case PROBE_DIR_YNEG:
          case PROBE_DIR_YPOS:
            laserMaterialBoundsProbeRoughEdge = currentJobPositionY;
            break;
          default:
            abortLaserMaterialBoundsProbe("COARSE_STOP", "invalid_direction");
            return;
        }
        laserMaterialBoundsProbeStepSentMs = millis();
        laserMaterialBoundsProbeRecoveryNeedsReset = true;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        serialManager.CPUASerialWrite(GRBL_STOP_RESET);
        waitForOk = 0;
        laserMaterialBoundsProbeRecoveryNeedsReset = false;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_ALARM;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_ALARM) {
        if (currentMachineState == ALARM) {
          serialManager.CPUASerialPrintln(GRBL_UNLOCK);
          ++waitForOk;
          laserMaterialBoundsProbeStepSentMs = 0;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        if (currentMachineState == IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_IDLE) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          return;
        }
        if (laserMaterialBoundsProbeStepSentMs == 0) {
          laserMaterialBoundsProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserMaterialBoundsProbeStepSentMs = 0;
        laserMaterialBoundsProbeSuppressAlarmAbort = false;
        laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_RETURN_FINE;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_RETURN_FINE: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        float fineStart = 0.0f;
        switch (laserMaterialBoundsProbeCurrentDir) {
          case PROBE_DIR_XNEG:
            fineStart = laserMaterialBoundsProbeRoughEdge + LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart > laserMaterialBoundsProbeStartX) fineStart = laserMaterialBoundsProbeStartX;
            laserMaterialBoundsProbeReturnTargetX = fineStart;
            laserMaterialBoundsProbeReturnTargetY = currentJobPositionY;
            snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_XPOS:
            fineStart = laserMaterialBoundsProbeRoughEdge - LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart < laserMaterialBoundsProbeStartX) fineStart = laserMaterialBoundsProbeStartX;
            laserMaterialBoundsProbeReturnTargetX = fineStart;
            laserMaterialBoundsProbeReturnTargetY = currentJobPositionY;
            snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_YNEG:
            fineStart = laserMaterialBoundsProbeRoughEdge + LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart > laserMaterialBoundsProbeStartY) fineStart = laserMaterialBoundsProbeStartY;
            laserMaterialBoundsProbeReturnTargetX = currentJobPositionX;
            laserMaterialBoundsProbeReturnTargetY = fineStart;
            snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_YPOS:
            fineStart = laserMaterialBoundsProbeRoughEdge - LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart < laserMaterialBoundsProbeStartY) fineStart = laserMaterialBoundsProbeStartY;
            laserMaterialBoundsProbeReturnTargetX = currentJobPositionX;
            laserMaterialBoundsProbeReturnTargetY = fineStart;
            snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
        }
        laserMaterialBoundsProbeReturnMotionObserved = false;
        laserMaterialBoundsProbeStepSentMs = 0;
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (waitForOk > 0) return;
        bool isX = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS);
        float currentAxis = isX ? currentJobPositionX : currentJobPositionY;
        float targetAxis = isX ? laserMaterialBoundsProbeReturnTargetX : laserMaterialBoundsProbeReturnTargetY;
        if (!laserMaterialBoundsProbeReturnMotionObserved) {
          if (currentMachineState == RUN || fabsf(currentAxis - targetAxis) <= 0.25f) {
            laserMaterialBoundsProbeReturnMotionObserved = true;
          } else {
            return;
          }
        }
        if (currentMachineState != IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          return;
        }
        if (fabsf(currentAxis - targetAxis) > 0.25f) return;
        if (laserMaterialBoundsProbeStepSentMs == 0) {
          laserMaterialBoundsProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserMaterialBoundsProbeStepSentMs = 0;
        laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_FINE_SCAN;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_FINE_SCAN: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        float dirSign = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS || laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        bool isX = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS);
        float fineEnd = laserMaterialBoundsProbeRoughEdge + dirSign * LASER_EDGE_PROBE_FINE_BACKOFF_MM;
        if (isX)
          snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineEnd, LASER_EDGE_PROBE_FINE_FEED_RATE_MM_MIN);
        else
          snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineEnd, LASER_EDGE_PROBE_FINE_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserMaterialBoundsProbeStepSentMs = millis();
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_MONITOR;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_MONITOR) {
        if (millis() - laserMaterialBoundsProbeStepSentMs < 200) return;

        if (currentMachineState == IDLE) {
          abortLaserMaterialBoundsProbe("FINE_SCAN", "no_edge_in_scan_range");
          return;
        }

        if (currentMachineState != RUN) return;

        float distMm = 0.0f;
        bool outOfRange = false;
        bool readOk = readDistanceLaserMm(&distMm, &outOfRange);

        bool edgeDetected = false;
        if (!readOk && outOfRange) {
          edgeDetected = true;
        } else if (!readOk) {
          abortLaserMaterialBoundsProbe("FINE_SCAN", "sample_read_failed");
          return;
        } else {
          edgeDetected = fabsf(distMm - laserMaterialBoundsProbeBaselineMm) >= LASER_EDGE_PROBE_THRESHOLD_MM;
        }

        if (edgeDetected) {
          serialManager.CPUASerialWrite(GRBL_HOLD);
          laserMaterialBoundsProbeSuppressAlarmAbort = true;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_HOLD;
        }
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_HOLD) {
        if (currentMachineState != HOLD && currentMachineState != IDLE) return;
        if (currentFeedRate > 0) return;

        float dirSign = (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS || laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        float captured = 0.0f;
        switch (laserMaterialBoundsProbeCurrentDir) {
          case PROBE_DIR_XNEG:
            laserMaterialBoundsProbeXMin = currentJobPositionX - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserMaterialBoundsProbeXMin;
            break;
          case PROBE_DIR_XPOS:
            laserMaterialBoundsProbeXMax = currentJobPositionX - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserMaterialBoundsProbeXMax;
            break;
          case PROBE_DIR_YNEG:
            laserMaterialBoundsProbeYMin = currentJobPositionY - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserMaterialBoundsProbeYMin;
            break;
          case PROBE_DIR_YPOS:
            laserMaterialBoundsProbeYMax = currentJobPositionY - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserMaterialBoundsProbeYMax;
            break;
        }
        (void)captured;

        laserMaterialBoundsProbeStepSentMs = millis();
        laserMaterialBoundsProbeRecoveryNeedsReset = true;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        serialManager.CPUASerialWrite(GRBL_STOP_RESET);
        waitForOk = 0;
        laserMaterialBoundsProbeRecoveryNeedsReset = false;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_ALARM;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_ALARM) {
        if (currentMachineState == ALARM) {
          serialManager.CPUASerialPrintln(GRBL_UNLOCK);
          ++waitForOk;
          laserMaterialBoundsProbeStepSentMs = 0;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        if (currentMachineState == IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT_IDLE) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          return;
        }
        if (laserMaterialBoundsProbeStepSentMs == 0) {
          laserMaterialBoundsProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserMaterialBoundsProbeStepSentMs = 0;
        laserMaterialBoundsProbeSuppressAlarmAbort = false;
        laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_FINE_RETURN;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_FINE_RETURN: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        laserMaterialBoundsProbeReturnTargetX = laserMaterialBoundsProbeStartX;
        laserMaterialBoundsProbeReturnTargetY = laserMaterialBoundsProbeStartY;
        laserMaterialBoundsProbeReturnMotionObserved = false;
        snprintf(cmd, sizeof(cmd), "G1 X%.3f Y%.3f F%d",
                 laserMaterialBoundsProbeStartX, laserMaterialBoundsProbeStartY,
                 LASER_EDGE_PROBE_FINE_RETURN_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserMaterialBoundsProbeStepSentMs = 0;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_READ;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_READ) {
        if (waitForOk > 0) return;
        if (currentMachineState == RUN)
          laserMaterialBoundsProbeReturnMotionObserved = true;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (!laserMaterialBoundsProbeReturnMotionObserved) {
          if (currentMachineState == RUN ||
              (fabsf(currentJobPositionX - laserMaterialBoundsProbeReturnTargetX) <= 0.25f &&
               fabsf(currentJobPositionY - laserMaterialBoundsProbeReturnTargetY) <= 0.25f)) {
            laserMaterialBoundsProbeReturnMotionObserved = true;
            laserMaterialBoundsProbeStepSentMs = 0;
          } else {
            return;
          }
        }
        if (currentMachineState != IDLE) {
          laserMaterialBoundsProbeStepSentMs = 0;
          return;
        }
        if (fabsf(currentJobPositionX - laserMaterialBoundsProbeReturnTargetX) > 0.25f ||
            fabsf(currentJobPositionY - laserMaterialBoundsProbeReturnTargetY) > 0.25f) return;
        if (laserMaterialBoundsProbeStepSentMs == 0) {
          laserMaterialBoundsProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserMaterialBoundsProbeStepSentMs = 0;

        if (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XNEG) {
          laserMaterialBoundsProbeCurrentDir = PROBE_DIR_XPOS;
          laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN;
        } else if (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_XPOS) {
          laserMaterialBoundsProbeCurrentDir = PROBE_DIR_YNEG;
          laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_AXIS_BASELINE;
        } else if (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YNEG) {
          laserMaterialBoundsProbeCurrentDir = PROBE_DIR_YPOS;
          laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN;
        } else if (laserMaterialBoundsProbeCurrentDir == PROBE_DIR_YPOS) {
          laserMaterialBoundsProbeCenterX = (laserMaterialBoundsProbeXMin + laserMaterialBoundsProbeXMax) * 0.5f;
          laserMaterialBoundsProbeCenterY = (laserMaterialBoundsProbeYMin + laserMaterialBoundsProbeYMax) * 0.5f;
          laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_CENTER_MOVE;
        } else {
          abortLaserMaterialBoundsProbe("FINE_RETURN", "invalid_direction");
          return;
        }
        laserMaterialBoundsProbeTraveled = 0.0f;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_AXIS_BASELINE: {

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_SEND) {
        laserMaterialBoundsProbeBaselineSampleSum = 0.0f;
        laserMaterialBoundsProbeBaselineSampleCount = 0;
        laserMaterialBoundsProbeStepSentMs = millis();
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserMaterialBoundsProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserMaterialBoundsProbeStepSentMs < 75) return;

        float baselineMm = 0.0f;
        bool outOfRange = false;
        bool readOk = readDistanceLaserMm(&baselineMm, &outOfRange);

        if (!readOk) {
          abortLaserMaterialBoundsProbe("AXIS_BASELINE", outOfRange ? "baseline_out_of_range" : "baseline_read_failed");
          return;
        }

        laserMaterialBoundsProbeBaselineSampleSum += baselineMm;
        ++laserMaterialBoundsProbeBaselineSampleCount;

        if (laserMaterialBoundsProbeBaselineSampleCount < 3) {
          laserMaterialBoundsProbeStepSentMs = millis();
          return;
        }

        laserMaterialBoundsProbeBaselineMm = laserMaterialBoundsProbeBaselineSampleSum / laserMaterialBoundsProbeBaselineSampleCount;
        laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN;
        laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_CENTER_MOVE: {

      if (waitForOk > 0)
        return;
      if (currentMachineState != IDLE)
        return;

      serialManager.CPUASerialPrintln("G90");
      ++waitForOk;
      snprintf(cmd, sizeof(cmd), "G1 X%.3f Y%.3f F%d", laserMaterialBoundsProbeCenterX, laserMaterialBoundsProbeCenterY, LASER_EDGE_PROBE_FINE_RETURN_FEED_RATE_MM_MIN);
      serialManager.CPUASerialPrintln(cmd);
      ++waitForOk;
      laserMaterialBoundsProbeReturnTargetX = laserMaterialBoundsProbeCenterX;
      laserMaterialBoundsProbeReturnTargetY = laserMaterialBoundsProbeCenterY;
      laserMaterialBoundsProbeReturnMotionObserved = false;
      laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_WAIT_CENTER;
      laserMaterialBoundsProbeStepSentMs = millis();
      return;
    }

    case LASER_MATERIAL_BOUNDS_PROBE_WAIT_CENTER: {

      if (waitForOk > 0)
        return;

      if (!laserMaterialBoundsProbeReturnMotionObserved) {
        if (currentMachineState == RUN ||
            (fabsf(currentJobPositionX - laserMaterialBoundsProbeReturnTargetX) <= 0.25f &&
             fabsf(currentJobPositionY - laserMaterialBoundsProbeReturnTargetY) <= 0.25f)) {
          laserMaterialBoundsProbeReturnMotionObserved = true;
          laserMaterialBoundsProbeStepSentMs = 0;
        } else {
          return;
        }
      }

      if (currentMachineState != IDLE) {
        laserMaterialBoundsProbeStepSentMs = 0;
        return;
      }

      if (fabsf(currentJobPositionX - laserMaterialBoundsProbeReturnTargetX) > 0.25f ||
          fabsf(currentJobPositionY - laserMaterialBoundsProbeReturnTargetY) > 0.25f)
        return;

      if (laserMaterialBoundsProbeStepSentMs == 0) {
        laserMaterialBoundsProbeStepSentMs = millis();
        return;
      }

      if (millis() - laserMaterialBoundsProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS)
        return;

      pushLaserMaterialBoundsCompletedEvent(true);
      return;
    }

    default:
      break;
  }

}

void handleLaserEdgeProbing() {

  if (!isLaserEdgeProbingActive) return;

  if (laserEdgeProbeWaitingForInitialUnlock) {
    if (currentMachineState == ALARM)
      return;
    if (currentMachineState != IDLE)
      return;
    laserEdgeProbeWaitingForInitialUnlock = false;
    laserEdgeProbeSuppressAlarmAbort = false;
  }

  // When laserEdgeProbeSuppressAlarmAbort is set we intentionally caused an ALARM
  // as part of the coarse-stop sequence — keep running in that case.
  if (currentMachineState == ALARM && !laserEdgeProbeSuppressAlarmAbort) return;

  char cmd[MACRO_LINE_BUFFER_CHARS];

  switch (laserEdgeProbeState) {

    // ── COARSE SCAN ────────────────────────────────────────────────────────────
    // Stream absolute G90 increments (5 mm each at 200 mm/min).  The next increment
    // is queued as soon as the machine is within TRIGGER_RANGE of the current target
    // and all previous ok's have been received.  On edge detection a feed hold ('!')
    // is sent; the rough edge is captured from the HOLD position in COARSE_STOP.
    case LASER_EDGE_PROBE_COARSE_SCAN: {

      // ── SEND: set G90 absolute mode ─────────────────────────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      // ── SEND_MOTION: send the first absolute increment ───────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        float dirSign = (laserEdgeProbeCurrentDir == PROBE_DIR_XPOS || laserEdgeProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        bool isX = (laserEdgeProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserEdgeProbeCurrentDir == PROBE_DIR_XPOS);
        float startAxis = isX ? laserEdgeProbeStartX : laserEdgeProbeStartY;
        laserEdgeProbeCoarseCurrentTarget = startAxis + dirSign * LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
        laserEdgeProbeTraveled = LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
        if (isX)
          snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", laserEdgeProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
        else
          snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", laserEdgeProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeStepSentMs = millis();
        laserEdgeProbeSubState = LASER_EDGE_SUB_MONITOR;
        return;
      }

      // ── MONITOR: streaming loop + edge detection ─────────────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_MONITOR) {
        // Guard: give the machine at least 400 ms to start on the first increment
        if (laserEdgeProbeTraveled <= LASER_EDGE_PROBE_COARSE_INCREMENT_MM &&
            millis() - laserEdgeProbeStepSentMs < 400) return;

        // All queued increments finished without finding an edge → fail
        if (currentMachineState == IDLE) {
          pushLaserEdgeProbingCompletedEvent(false);
          return;
        }

        if (currentMachineState != RUN) return; // wait until machine is moving

        bool isX = (laserEdgeProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserEdgeProbeCurrentDir == PROBE_DIR_XPOS);
        float dirSign = (laserEdgeProbeCurrentDir == PROBE_DIR_XPOS || laserEdgeProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;

        // ── Stream next increment when close to current target and buffer is free ──
        if (waitForOk == 0 &&
            laserEdgeProbeTraveled < LASER_EDGE_PROBE_MAX_TRAVEL_MM) {
          float currentAxisPos = isX ? currentJobPositionX : currentJobPositionY;
          float distToTarget = fabsf(currentAxisPos - laserEdgeProbeCoarseCurrentTarget);
          if (distToTarget <= LASER_EDGE_PROBE_COARSE_TRIGGER_RANGE_MM) {
            laserEdgeProbeCoarseCurrentTarget += dirSign * LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
            laserEdgeProbeTraveled            += LASER_EDGE_PROBE_COARSE_INCREMENT_MM;
            if (isX)
              snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", laserEdgeProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
            else
              snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", laserEdgeProbeCoarseCurrentTarget, LASER_EDGE_PROBE_COARSE_FEED_RATE_MM_MIN);
            serialManager.CPUASerialPrintln(cmd);
            ++waitForOk;
          }
        }

        // ── Read laser and detect edge ───────────────────────────────────────────
        float distMm  = 0.0f;
        bool outOfRange = false;
        bool readOk = readDistanceLaserMm(&distMm, &outOfRange);

        bool edgeDetected = false;
        if (!readOk && outOfRange) {
          edgeDetected = true; // laser sees past the block edge
        } else if (readOk) {
          edgeDetected = fabsf(distMm - laserEdgeProbeBaselineMm) >= LASER_EDGE_PROBE_THRESHOLD_MM;
        }
        // readOk==false && !outOfRange → sensor comm error, skip sample

        if (edgeDetected) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[80];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] COARSE edge dir=%d dist=%.3f",
                     (int)laserEdgeProbeCurrentDir, distMm);
            serialManager.USBSerialPrintln(dbg);
          }
          // Rough edge position will be captured from the HOLD status in COARSE_STOP
          serialManager.CPUASerialWrite(GRBL_HOLD);
          laserEdgeProbeSuppressAlarmAbort = true;
          laserEdgeProbeState    = LASER_EDGE_PROBE_COARSE_STOP;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_HOLD;
        }
        return;
      }

      break;
    }

    // ── COARSE STOP ────────────────────────────────────────────────────────────
    // Feed hold was sent on edge detection.  Wait for HOLD, capture rough edge
    // from the stopped position, then soft-reset and unlock before continuing.
    case LASER_EDGE_PROBE_COARSE_STOP: {

      // Wait for machine to fully stop → record rough edge position.
      // After feed-hold GRBL may report HOLD while decelerating, or it may
      // already be back in IDLE by the next poll. Accept either stopped state
      // once the feed reaches zero, then always force the reset/unlock recovery
      // path so coarse recovery does not depend on which status sample arrived
      // first.
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_HOLD) {
        if (currentMachineState != HOLD && currentMachineState != IDLE) return;
        if (currentFeedRate > 0) return;
        switch (laserEdgeProbeCurrentDir) {
          case PROBE_DIR_XNEG:
          case PROBE_DIR_XPOS: laserEdgeProbeRoughEdge = currentJobPositionX; break;
          case PROBE_DIR_YNEG:
          case PROBE_DIR_YPOS: laserEdgeProbeRoughEdge = currentJobPositionY; break;
          default:
            pushLaserEdgeProbingCompletedEvent(false);
            return;
        }
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[120];
          snprintf(dbg, sizeof(dbg),
                   "[LaserProbe] COARSE HOLD state=%d feed=%ld rough=%.3f dir=%d",
                   (int)currentMachineState, currentFeedRate,
                   laserEdgeProbeRoughEdge, (int)laserEdgeProbeCurrentDir);
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeStepSentMs = millis();
        laserEdgeProbeRecoveryNeedsReset = true;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[100];
          snprintf(dbg, sizeof(dbg),
                   "[LaserProbe] COARSE RESET dir=%d after %lu ms",
                   (int)laserEdgeProbeCurrentDir,
                   (unsigned long)(millis() - laserEdgeProbeStepSentMs));
          serialManager.USBSerialPrintln(dbg);
        }
        serialManager.CPUASerialWrite(GRBL_STOP_RESET);
        waitForOk = 0;
        laserEdgeProbeRecoveryNeedsReset = false;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_ALARM;
        return;
      }

      // After reset some controllers report ALARM and require $X, while others
      // return directly to IDLE. Accept either recovery path.
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_ALARM) {
        if (currentMachineState == ALARM) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[80];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] COARSE ALARM dir=%d sending $X",
                     (int)laserEdgeProbeCurrentDir);
            serialManager.USBSerialPrintln(dbg);
          }
          serialManager.CPUASerialPrintln(GRBL_UNLOCK);
          ++waitForOk;
          laserEdgeProbeStepSentMs = 0;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        if (currentMachineState == IDLE) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[80];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] COARSE RESET dir=%d already IDLE",
                     (int)laserEdgeProbeCurrentDir);
            serialManager.USBSerialPrintln(dbg);
          }
          laserEdgeProbeStepSentMs = 0;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        return;
      }

      // Wait for IDLE → clear alarm suppression flag, proceed to fine scan
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_IDLE) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) {
          laserEdgeProbeStepSentMs = 0;
          return;
        }
        if (laserEdgeProbeStepSentMs == 0) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserEdgeProbeStepSentMs = 0;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[80];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] COARSE IDLE dir=%d -> RETURN_FINE",
                   (int)laserEdgeProbeCurrentDir);
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeSuppressAlarmAbort = false;
        laserEdgeProbeState    = LASER_EDGE_PROBE_RETURN_FINE;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── RETURN TO FINE-SCAN START ──────────────────────────────────────────────
    // Move BACKOFF mm to the safe side of the rough edge (absolute G90 move).
    // All positions are in work (job) coordinates.
    case LASER_EDGE_PROBE_RETURN_FINE: {

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        // Set absolute mode as a separate command
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        // Approach from the side opposite to the scan direction.
        // Clamp fineStart so it never overshoots startX/Y: the start position is
        // guaranteed to be over the workpiece surface (the baseline was valid there),
        // so starting the fine scan from beyond startX/Y would put the laser off the
        // workpiece and cause an immediate false out-of-range detection.
        float fineStart = 0.0f;
        switch (laserEdgeProbeCurrentDir) {
          case PROBE_DIR_XNEG:
            fineStart = laserEdgeProbeRoughEdge + LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart > laserEdgeProbeStartX) fineStart = laserEdgeProbeStartX;
            laserEdgeProbeReturnTargetX = fineStart;
            laserEdgeProbeReturnTargetY = currentJobPositionY;
            snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_XPOS:
            fineStart = laserEdgeProbeRoughEdge - LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart < laserEdgeProbeStartX) fineStart = laserEdgeProbeStartX;
            laserEdgeProbeReturnTargetX = fineStart;
            laserEdgeProbeReturnTargetY = currentJobPositionY;
            snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_YNEG:
            fineStart = laserEdgeProbeRoughEdge + LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart > laserEdgeProbeStartY) fineStart = laserEdgeProbeStartY;
            laserEdgeProbeReturnTargetX = currentJobPositionX;
            laserEdgeProbeReturnTargetY = fineStart;
            snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          case PROBE_DIR_YPOS:
            fineStart = laserEdgeProbeRoughEdge - LASER_EDGE_PROBE_FINE_BACKOFF_MM;
            if (fineStart < laserEdgeProbeStartY) fineStart = laserEdgeProbeStartY;
            laserEdgeProbeReturnTargetX = currentJobPositionX;
            laserEdgeProbeReturnTargetY = fineStart;
            snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineStart, LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN);
            break;
          default:
            pushLaserEdgeProbingCompletedEvent(false);
            return;
        }
        laserEdgeProbeReturnMotionObserved = false;
        laserEdgeProbeStepSentMs = 0;
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (waitForOk > 0) return;
        bool isX = (laserEdgeProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserEdgeProbeCurrentDir == PROBE_DIR_XPOS);
        float currentAxis = isX ? currentJobPositionX : currentJobPositionY;
        float targetAxis = isX ? laserEdgeProbeReturnTargetX : laserEdgeProbeReturnTargetY;
        if (!laserEdgeProbeReturnMotionObserved) {
          if (currentMachineState == RUN || fabsf(currentAxis - targetAxis) <= 0.25f) {
            laserEdgeProbeReturnMotionObserved = true;
          } else {
            return;
          }
        }
        if (currentMachineState != IDLE) {
          laserEdgeProbeStepSentMs = 0;
          return;
        }
        if (fabsf(currentAxis - targetAxis) > 0.25f) return;
        if (laserEdgeProbeStepSentMs == 0) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserEdgeProbeStepSentMs = 0;
        laserEdgeProbeTraveled = 0.0f;
        laserEdgeProbeState = LASER_EDGE_PROBE_FINE_SCAN;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── FINE SCAN ──────────────────────────────────────────────────────────────
    // Single continuous slow G90 pass through the edge zone at 75 mm/min.
    // Edge is detected in motion; a feed hold stops the machine.  The position
    // captured at HOLD is corrected by LASER_EDGE_PROBE_EDGE_OFFSET_MM (applied
    // against the scan direction) to yield the calibrated edge coordinate.
    case LASER_EDGE_PROBE_FINE_SCAN: {

      // ── SEND: set G90 absolute mode ─────────────────────────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      // ── SEND_MOTION: move slowly through the full fine-scan zone ────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        float dirSign = (laserEdgeProbeCurrentDir == PROBE_DIR_XPOS || laserEdgeProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        bool isX = (laserEdgeProbeCurrentDir == PROBE_DIR_XNEG ||
                    laserEdgeProbeCurrentDir == PROBE_DIR_XPOS);
        // Scan target: roughEdge + BACKOFF in the scan direction (scan well past expected edge)
        float fineEnd = laserEdgeProbeRoughEdge + dirSign * LASER_EDGE_PROBE_FINE_BACKOFF_MM;
        if (isX)
          snprintf(cmd, sizeof(cmd), "G1 X%.3f F%d", fineEnd, LASER_EDGE_PROBE_FINE_FEED_RATE_MM_MIN);
        else
          snprintf(cmd, sizeof(cmd), "G1 Y%.3f F%d", fineEnd, LASER_EDGE_PROBE_FINE_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeStepSentMs = millis();
        laserEdgeProbeSubState = LASER_EDGE_SUB_MONITOR;
        return;
      }

      // ── MONITOR: read laser continuously while moving ────────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_MONITOR) {
        // Give machine 200 ms to start moving
        if (millis() - laserEdgeProbeStepSentMs < 200) return;

        // Full fine-scan move finished without detecting edge → fail
        if (currentMachineState == IDLE) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[80];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] FINE FAIL dir=%d no edge in scan range",
                     (int)laserEdgeProbeCurrentDir);
            serialManager.USBSerialPrintln(dbg);
          }
          pushLaserEdgeProbingCompletedEvent(false);
          return;
        }

        if (currentMachineState != RUN) return;

        float distMm  = 0.0f;
        bool outOfRange = false;
        bool readOk = readDistanceLaserMm(&distMm, &outOfRange);

        bool edgeDetected = false;
        if (!readOk && outOfRange) {
          edgeDetected = true;
        } else if (!readOk) {
          pushLaserEdgeProbingCompletedEvent(false); // sensor comm error
          return;
        } else {
          edgeDetected = fabsf(distMm - laserEdgeProbeBaselineMm) >= LASER_EDGE_PROBE_THRESHOLD_MM;
        }

        if (edgeDetected) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[100];
            snprintf(dbg, sizeof(dbg),
                     "[LaserProbe] FINE DETECT dir=%d dist=%.3f base=%.3f delta=%.3f OOR=%d",
                     (int)laserEdgeProbeCurrentDir, distMm, laserEdgeProbeBaselineMm,
                     fabsf(distMm - laserEdgeProbeBaselineMm), (int)outOfRange);
            serialManager.USBSerialPrintln(dbg);
          }
          serialManager.CPUASerialWrite(GRBL_HOLD);
          laserEdgeProbeSuppressAlarmAbort = true;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_HOLD;
        }
        return;
      }

      // ── WAIT_HOLD: machine fully stopped → capture edge + apply calibration offset ─
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_HOLD) {
        if (currentMachineState != HOLD && currentMachineState != IDLE) return;
        if (currentFeedRate > 0) return;
        // The machine overshot the actual edge by the braking distance.
        // Subtract offset in the scan direction to recover the true edge position.
        float dirSign = (laserEdgeProbeCurrentDir == PROBE_DIR_XPOS || laserEdgeProbeCurrentDir == PROBE_DIR_YPOS) ? 1.0f : -1.0f;
        float captured = 0.0f;
        const char* dirName = nullptr;
        switch (laserEdgeProbeCurrentDir) {
          case PROBE_DIR_XNEG:
          case PROBE_DIR_XPOS:
            laserEdgeProbeXNeg = currentJobPositionX - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserEdgeProbeXNeg;
            dirName = "X";
            break;
          case PROBE_DIR_YNEG:
          case PROBE_DIR_YPOS:
            laserEdgeProbeYNeg = currentJobPositionY - dirSign * LASER_EDGE_PROBE_EDGE_OFFSET_MM;
            captured = laserEdgeProbeYNeg;
            dirName = "Y";
            break;
          default:
            pushLaserEdgeProbingCompletedEvent(false);
            return;
        }
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[120];
          snprintf(dbg, sizeof(dbg),
                   "[LaserProbe] FINE HOLD state=%d feed=%ld %s=%.3f",
                   (int)currentMachineState, currentFeedRate, dirName, captured);
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeStepSentMs = millis();
        laserEdgeProbeRecoveryNeedsReset = true;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[100];
          snprintf(dbg, sizeof(dbg),
                   "[LaserProbe] FINE RESET dir=%d after %lu ms",
                   (int)laserEdgeProbeCurrentDir,
                   (unsigned long)(millis() - laserEdgeProbeStepSentMs));
          serialManager.USBSerialPrintln(dbg);
        }
        serialManager.CPUASerialWrite(GRBL_STOP_RESET);
        waitForOk = 0;
        laserEdgeProbeRecoveryNeedsReset = false;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_ALARM;
        return;
      }

      // ── WAIT_ALARM: some controllers go ALARM->$X->IDLE, others reset
      // directly to IDLE. Accept either path. ───────────────────────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_ALARM) {
        if (currentMachineState == ALARM) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[60];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] FINE ALARM dir=%d sending $X",
                     (int)laserEdgeProbeCurrentDir);
            serialManager.USBSerialPrintln(dbg);
          }
          serialManager.CPUASerialPrintln(GRBL_UNLOCK);
          ++waitForOk;
          laserEdgeProbeStepSentMs = 0;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        if (currentMachineState == IDLE) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[60];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] FINE RESET dir=%d already IDLE",
                     (int)laserEdgeProbeCurrentDir);
            serialManager.USBSerialPrintln(dbg);
          }
          laserEdgeProbeStepSentMs = 0;
          laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT_IDLE;
          return;
        }
        return;
      }

      // ── WAIT_IDLE → clear flag and advance to the next phase ───────────────
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT_IDLE) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) {
          laserEdgeProbeStepSentMs = 0;
          return;
        }
        if (laserEdgeProbeStepSentMs == 0) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserEdgeProbeStepSentMs = 0;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[60];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] FINE IDLE dir=%d -> %s",
                   (int)laserEdgeProbeCurrentDir,
                   (laserEdgeProbeCurrentDir == laserEdgeProbeSecondDir) ? "GO_CORNER" : "FINE_RETURN");
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeSuppressAlarmAbort = false;
        laserEdgeProbeState = (laserEdgeProbeCurrentDir == laserEdgeProbeSecondDir)
          ? LASER_EDGE_PROBE_GO_CORNER
          : LASER_EDGE_PROBE_FINE_RETURN;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── FINE RETURN ────────────────────────────────────────────────────────────
    // Return to the full start position (startX, startY) at 750 mm/min before
    // probing the next direction.  Both axes are restored in a single G1 move so
    // that Y probing always begins from the correct origin regardless of any drift
    // introduced by the previous direction's stop sequence.
    case LASER_EDGE_PROBE_FINE_RETURN: {

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        // Ensure absolute mode before the return move (soft reset clears modal state).
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        // Return to work-coordinate start position.  Work coords are stable during
        // probing — G10 only fires in SET_ORIGIN which is the very last step.
        laserEdgeProbeReturnTargetX = laserEdgeProbeStartX;
        laserEdgeProbeReturnTargetY = laserEdgeProbeStartY;
        laserEdgeProbeReturnMotionObserved = false;
        snprintf(cmd, sizeof(cmd), "G1 X%.3f Y%.3f F%d",
                 laserEdgeProbeStartX, laserEdgeProbeStartY,
                 LASER_EDGE_PROBE_FINE_RETURN_FEED_RATE_MM_MIN);
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[80];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] RETURN dir=%d -> work(%.3f,%.3f)",
                   (int)laserEdgeProbeCurrentDir,
                   laserEdgeProbeStartX, laserEdgeProbeStartY);
          serialManager.USBSerialPrintln(dbg);
        }
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeStepSentMs = 0;
        laserEdgeProbeSubState = LASER_EDGE_SUB_READ;  // READ fires once when ack arrives
        return;
      }

      // ── READ: fires exactly once when ack is received ───────────────────────
      // Logs the machine state at that moment — if it's not RUN the command
      // was rejected or the machine was already at the target.
      if (laserEdgeProbeSubState == LASER_EDGE_SUB_READ) {
        if (waitForOk > 0) return;
        if (currentMachineState == RUN)
          laserEdgeProbeReturnMotionObserved = true;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[80];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] RETURN ack dir=%d machState=%d pos=(%.3f,%.3f)",
                   (int)laserEdgeProbeCurrentDir, (int)currentMachineState,
                   currentMachinePositionX, currentMachinePositionY);
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (!laserEdgeProbeReturnMotionObserved) {
          if (currentMachineState == RUN ||
              (fabsf(currentJobPositionX - laserEdgeProbeReturnTargetX) <= 0.25f &&
               fabsf(currentJobPositionY - laserEdgeProbeReturnTargetY) <= 0.25f)) {
            laserEdgeProbeReturnMotionObserved = true;
            laserEdgeProbeStepSentMs = 0;
          } else {
            return;
          }
        }
        if (currentMachineState != IDLE) {
          laserEdgeProbeStepSentMs = 0;
          return;
        }
        if (fabsf(currentJobPositionX - laserEdgeProbeReturnTargetX) > 0.25f ||
            fabsf(currentJobPositionY - laserEdgeProbeReturnTargetY) > 0.25f) return;
        if (laserEdgeProbeStepSentMs == 0) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserEdgeProbeStepSentMs = 0;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[60];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] RETURN IDLE dir=%d -> next",
                   (int)laserEdgeProbeCurrentDir);
          serialManager.USBSerialPrintln(dbg);
        }
        if (laserEdgeProbeCurrentDir == laserEdgeProbeSecondDir) {
          // Both requested directions done.
          laserEdgeProbeState = LASER_EDGE_PROBE_GO_CORNER;
        } else if (laserEdgeProbeCurrentDir == laserEdgeProbeFirstDir) {
          // After the X edge, switch to the corner's Y direction and take a
          // fresh Y baseline first.
          laserEdgeProbeCurrentDir = laserEdgeProbeSecondDir;
          laserEdgeProbeState = LASER_EDGE_PROBE_Y_BASELINE;
        } else {
          pushLaserEdgeProbingCompletedEvent(false);
          return;
        }
        laserEdgeProbeTraveled = 0.0f;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── RE-MEASURE BASELINE FOR Y PHASE ───────────────────────────────────────
    // The machine has returned to (startX, startY).  Take a fresh laser reading
    // so that the threshold check uses a baseline appropriate for Y-direction
    // scanning (the laser geometry may differ from the X-phase baseline).
    case LASER_EDGE_PROBE_Y_BASELINE: {

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        laserEdgeProbeBaselineSampleSum = 0.0f;
        laserEdgeProbeBaselineSampleCount = 0;
        laserEdgeProbeStepSentMs = millis();
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (millis() - laserEdgeProbeStepSentMs < 75) return;

        float yBaselineMm = 0.0f;
        bool yOutOfRange = false;
        bool yReadOk = readDistanceLaserMm(&yBaselineMm, &yOutOfRange);

        if (!yReadOk) {
          if (debuggingSettings.debuggingMacroBuffer) {
            char dbg[96];
            snprintf(dbg, sizeof(dbg), "[LaserProbe] Y BASELINE invalid read (outOfRange=%d)",
                     (int)yOutOfRange);
            serialManager.USBSerialPrintln(dbg);
          }
          // The start position is expected to be over the workpiece surface.
          // An invalid/out-of-range baseline here would cause the Y scan to
          // false-trigger almost immediately, so fail instead of proceeding.
          pushLaserEdgeProbingCompletedEvent(false);
          return;
        }

        laserEdgeProbeBaselineSampleSum += yBaselineMm;
        ++laserEdgeProbeBaselineSampleCount;

        if (laserEdgeProbeBaselineSampleCount < 3) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }

        laserEdgeProbeBaselineMm = laserEdgeProbeBaselineSampleSum / laserEdgeProbeBaselineSampleCount;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[80];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] Y BASELINE=%.3f samples=%d",
                   laserEdgeProbeBaselineMm, (int)laserEdgeProbeBaselineSampleCount);
          serialManager.USBSerialPrintln(dbg);
        }
        laserEdgeProbeState = LASER_EDGE_PROBE_COARSE_SCAN;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── MOVE TO DETECTED CORNER ────────────────────────────────────────────────
    case LASER_EDGE_PROBE_GO_CORNER: {

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        // Set absolute mode as a separate command
        serialManager.CPUASerialPrintln("G90");
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND_MOTION;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND_MOTION) {
        if (waitForOk > 0) return;
        // Move to the detected corner, offset INTO the material on each axis.
        // "Into the material" is opposite the edge/probe direction: for an X-
        // (left) edge the interior is +X, for an X+ (right) edge it is -X; same
        // for Y. A zero offset leaves the origin exactly on the corner.
        float intoSignX = (laserEdgeProbeFirstDir == PROBE_DIR_XPOS) ? -1.0f : 1.0f;
        float intoSignY = (laserEdgeProbeSecondDir == PROBE_DIR_YPOS) ? -1.0f : 1.0f;
        float cornerX = laserEdgeProbeXNeg + intoSignX * laserEdgeProbeXOffset;
        float cornerY = laserEdgeProbeYNeg + intoSignY * laserEdgeProbeYOffset;
        laserEdgeProbeReturnTargetX = cornerX;
        laserEdgeProbeReturnTargetY = cornerY;
        laserEdgeProbeReturnMotionObserved = false;
        if (debuggingSettings.debuggingMacroBuffer) {
          char dbg[100];
          snprintf(dbg, sizeof(dbg), "[LaserProbe] CORNER xNeg=%.3f yNeg=%.3f -> (%.3f,%.3f)",
                   laserEdgeProbeXNeg, laserEdgeProbeYNeg,
                   cornerX, cornerY);
          serialManager.USBSerialPrintln(dbg);
        }
        snprintf(cmd, sizeof(cmd), "G1 X%.3f Y%.3f F%d", cornerX, cornerY, LASER_EDGE_PROBE_FINE_RETURN_FEED_RATE_MM_MIN);
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeStepSentMs = 0;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (waitForOk > 0) return;
        if (!laserEdgeProbeReturnMotionObserved) {
          if (currentMachineState == RUN ||
              (fabsf(currentJobPositionX - laserEdgeProbeReturnTargetX) <= 0.25f &&
               fabsf(currentJobPositionY - laserEdgeProbeReturnTargetY) <= 0.25f)) {
            laserEdgeProbeReturnMotionObserved = true;
            laserEdgeProbeStepSentMs = 0;
          } else {
            return;
          }
        }
        if (currentMachineState != IDLE) {
          laserEdgeProbeStepSentMs = 0;
          return;
        }
        if (fabsf(currentJobPositionX - laserEdgeProbeReturnTargetX) > 0.25f ||
            fabsf(currentJobPositionY - laserEdgeProbeReturnTargetY) > 0.25f) return;
        if (laserEdgeProbeStepSentMs == 0) {
          laserEdgeProbeStepSentMs = millis();
          return;
        }
        if (millis() - laserEdgeProbeStepSentMs < LASER_EDGE_PROBE_SETTLE_DELAY_MS) return;
        laserEdgeProbeStepSentMs = 0;
        laserEdgeProbeState = LASER_EDGE_PROBE_SET_ORIGIN;
        laserEdgeProbeSetOriginStep = 0;
        laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
        return;
      }

      break;
    }

    // ── SET WORK ORIGIN ────────────────────────────────────────────────────────
    case LASER_EDGE_PROBE_SET_ORIGIN: {

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_SEND) {
        if (waitForOk > 0) return;
        if (laserEdgeProbeSetOriginStep == 0) {
          snprintf(cmd, sizeof(cmd), "G90");
          laserEdgeProbeSetOriginStep = 1;
        } else {
          snprintf(cmd, sizeof(cmd), "G10 L20 P1 X0 Y0");
          laserEdgeProbeSetOriginStep = 2;
        }
        serialManager.CPUASerialPrintln(cmd);
        ++waitForOk;
        laserEdgeProbeSubState = LASER_EDGE_SUB_WAIT;
        return;
      }

      if (laserEdgeProbeSubState == LASER_EDGE_SUB_WAIT) {
        if (waitForOk > 0) return;
        if (currentMachineState != IDLE) return;
        if (laserEdgeProbeSetOriginStep == 1) {
          laserEdgeProbeSubState = LASER_EDGE_SUB_SEND; // send G10 next
        } else {
          pushLaserEdgeProbingCompletedEvent(true); // done
        }
        return;
      }

      break;
    }

    default: break;
  }

}

void handleUSBLine() {

  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln("Line read from USB"); }
  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrint("USBLineArray: "); }
  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln(USBLineArray); }

  currentInputSource = INPUT_SOURCE_USB;

  receivedJsonBuffer.clear();
  DeserializationError error = deserializeJson(receivedJsonBuffer, USBLineArray);
    
    //if(error == DeserializationError::Ok)

  //check if it is a Json, if so it is message for CPUB, if not it is a G-code line for GRBL/CPUA or other
  if(error == DeserializationError::Ok) {

    //if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrintln("Valid JSON received."); }  
    handleJson();

  }else{

    handleRawPassthroughLine(USBLineArray);

  }

  
  /*
  //check if there is an M3 to enable the spindle
  bool sCommandPresent = false;

  char* subString = NULL;
  subString = strstr(USBLineArray, M3);

  if (subString != NULL) {

    spindleEnabled = true;
    wantedSpindleRPM = 0;
  }

  subString = NULL;
  subString = strstr(USBLineArray, m3);

  if (subString != NULL) {

    spindleEnabled = true;
    wantedSpindleRPM = 0;
  }

  subString = NULL;
  subString = strstr(USBLineArray, m5);

  if (subString != NULL) {

    spindleEnabled = false;
    wantedSpindleRPM = 0;
  }


  //check if there is an M5 or M30 to disable the spindle
  subString = NULL;
  subString = strstr(USBLineArray, M5);

  if (subString != NULL) {

    spindleEnabled = false;
    wantedSpindleRPM = 0;
  }


  subString = NULL;
  subString = strstr(USBLineArray, m5);

  if (subString != NULL) {

    spindleEnabled = false;
    wantedSpindleRPM = 0;
  }

  subString = NULL;
  subString = strstr(USBLineArray, M30);

  if (subString != NULL) {

    spindleEnabled = false;
    wantedSpindleRPM = 0;
  }

  subString = NULL;
  subString = strstr(USBLineArray, m30);

  if (subString != NULL) {

    spindleEnabled = false;
    wantedSpindleRPM = 0;
  }

  //check if an S command is received
  subString = NULL;
  subString = strstr(USBLineArray, s);

  if (subString != NULL)
    sCommandPresent = true;

  if (subString != NULL && spindleEnabled) {

    ++subString;
    wantedSpindleRPM = atoi(subString);

    if(wantedSpindleRPM < MINIMUM_SPINDLE_RPM)
      wantedSpindleRPM = 0;

  }

  subString = NULL;
  subString = strstr(USBLineArray, S);
  
  if (subString != NULL)
    sCommandPresent = true;

  if (subString != NULL && spindleEnabled) {

    ++subString;
    wantedSpindleRPM = atoi(subString);

    if(wantedSpindleRPM < MINIMUM_SPINDLE_RPM)
      wantedSpindleRPM = 0;

  }

  //set here the start of the ramping if a command is received!
  if(wantedSpindleRPM > MINIMUM_SPINDLE_RPM && spindleEnabled)
    startRamping = true;

  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrint("Is spindle enabled: "); }
  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrintln(spindleEnabled); }

  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrint("Set spindle RPM: "); }
  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrintln(wantedSpindleRPM); }


  */
  
  
  
}

void handleMobileWebsocketLine() {

  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln("Line read from Mobile WebSocket"); }
  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrint("MobileWebSocketLineBuffer: "); }
  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln(mobileWebSocketMessageBuffer); }

  currentInputSource = INPUT_SOURCE_MOBILE_WEBSOCKET;

  receivedJsonBuffer.clear();
  DeserializationError error = deserializeJson(receivedJsonBuffer, mobileWebSocketMessageBuffer);

  if(error == DeserializationError::Ok) {

    handleJson();

  }else{

    handleRawPassthroughLine(mobileWebSocketMessageBuffer);

  }

  clearResponseJsonBuffer();

}

void handleCPUALine() {

  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln("Line read from CPUA"); }

  //first of all we check if the line contains a reset report, in that case we reset wait for ok
  if (grblUtil.isResetReport(CPUALineArray)){

    waitForOk = 0;
    resetManualSpindleRampState();
    resetSpindleModalState(false);
    return;

  }

  //while a job is streaming, oks are counted tolerantly (damaged "k"/"0k"/"Pk", or several glued in one
  //line) and consumed here, before the status report handling below can return early and drop them.
  //Outside a job the plain ok matching further down is used, unchanged.
  int jobOkCount = 0;

  if (isJobRunning) {

    bool okRecovered = false;
    jobOkCount = grblUtil.countJobStreamingOks(CPUALineArray, &okRecovered);

    for (int i = 0; i < jobOkCount; ++i)
      if (waitForOk > 0)
        --waitForOk;

    if (okRecovered)
      logRecoveredJobOk(jobOkCount);
  }
  

  //determine position if the line contains the position report, if it does, update the last known position
  float bufferPositionX = grblUtil.getMachineX(CPUALineArray);
  float bufferPositionY = grblUtil.getMachineY(CPUALineArray);
  float bufferPositionZ = grblUtil.getMachineZ(CPUALineArray);
  float bufferPositionA = grblUtil.getMachineA(CPUALineArray);
  
  if(bufferPositionX != GRBL_NULL_POSITION && 
    bufferPositionY != GRBL_NULL_POSITION && 
    bufferPositionZ != GRBL_NULL_POSITION){

    currentMachinePositionX = bufferPositionX;
    currentMachinePositionY = bufferPositionY;
    currentMachinePositionZ = bufferPositionZ;

    if(bufferPositionA != GRBL_NULL_POSITION)
      currentMachinePositionA = bufferPositionA;

    long bufferFeedRate = grblUtil.getFeedRate(CPUALineArray);

    if(bufferFeedRate >= 0)
      currentFeedRate = bufferFeedRate;

    
    long bufferSpindleRPM = grblUtil.getSpindleRPM(CPUALineArray);

    if(bufferSpindleRPM >= 0)
      currentSpindleRPM = bufferSpindleRPM;

    // Close the loop on the spindle override: GRBL-HAL reports the real value
    // in the "Ov:" status field (intermittently). Treat it as the source of
    // truth so the local counter cannot drift out of sync with the firmware.
    // Skip while a resume restore ramp is actively driving the override, so a
    // stale/lagging Ov: report does not fight the in-progress ramp.
    int bufferSpindleOverride = grblUtil.getSpindleOverride(CPUALineArray);

    if (bufferSpindleOverride >= 0) {
      if (debuggingSettings.debuggingSpindle && bufferSpindleOverride != spindleOverride) {
        serialManager.USBSerialPrint("Spindle override sync: local ");
        serialManager.USBSerialPrint(spindleOverride);
        serialManager.USBSerialPrint("%% -> grbl Ov: ");
        serialManager.USBSerialPrint(bufferSpindleOverride);
        serialManager.USBSerialPrintln("%%");
      }
      // Always record GRBL's real override so the resume CONFIRM stage can close the
      // loop even while the ramp is active (adoption into spindleOverride stays gated).
      lastReportedSpindleOverride = bufferSpindleOverride;
      lastReportedSpindleOverrideMs = millis();
      if (!isResumeSpindleOverrideRampActive)
        spindleOverride = bufferSpindleOverride;
    }

    currentMachineState = grblUtil.getMachineState(CPUALineArray);
    // Pause is implemented via grblHAL's safety-door/parking (triggered by 0x84). Present that
    // Door state to the rest of the firmware and the app as a normal Hold/pause.
    if (currentMachineState == DOOR)
      currentMachineState = HOLD;
    // The grblHAL parking resume keeps reporting Hold (Door) for a few seconds; once the
    // machine actually leaves Hold the resume is complete — clear the transient flag.
    if (currentMachineState != HOLD)
      isResuming = false;
    lastGRBLStatusReceivedMs = millis();
    currentProbeTriggered = grblUtil.isProbeTriggered(CPUALineArray);

    //detect homing completion: HOME -> IDLE means success, HOME -> ALARM means failure
    if (isHoming) {
      if (currentMachineState == IDLE)
        pushHomingCompletedEvent(true);
      else if (currentMachineState == ALARM)
        pushHomingCompletedEvent(false);
    }

    // Detect probe Z0 macro failure: ALARM while probe macro is active
    if (isProbeZ0MacroRunning && currentMachineState == ALARM) {
      arrayUtil.clear2DCharArray(bufferMacroLines);
      isMacroRunning = false;
      pushProbeZ0CompletedEvent(false);
    }

    // Detect tool length macro failure: ALARM while macro is active
    if (isProbeToolLengthMacroRunning && currentMachineState == ALARM) {
      arrayUtil.clear2DCharArray(bufferMacroLines);
      isMacroRunning = false;
      pushProbeToolLengthCompletedEvent(false);
    }

    // Detect laser edge probe failure: ALARM while probing is active
    if (isLaserEdgeProbingActive && currentMachineState == ALARM && !laserEdgeProbeSuppressAlarmAbort) {
      if (debuggingSettings.debuggingMacroBuffer) {
        char dbg[80];
        snprintf(dbg, sizeof(dbg),
                 "[LaserProbe] ABORT unsuppressed ALARM state=%d sub=%d",
                 (int)laserEdgeProbeState, (int)laserEdgeProbeSubState);
        serialManager.USBSerialPrintln(dbg);
      }
      isMacroRunning = false;
      pushLaserEdgeProbingCompletedEvent(false);
    }

    if (isLaserMaterialBoundsProbingActive && currentMachineState == ALARM && !laserMaterialBoundsProbeSuppressAlarmAbort) {
      if (debuggingSettings.debuggingMacroBuffer) {
        char dbg[96];
        snprintf(dbg, sizeof(dbg),
                 "[LaserBounds] ABORT unsuppressed ALARM state=%d",
                 (int)laserMaterialBoundsProbeState);
        serialManager.USBSerialPrintln(dbg);
      }
      isMacroRunning = false;
      pushLaserMaterialBoundsCompletedEvent(false);
    }
      
    if(debuggingSettings.debuggingStatus) {
      serialManager.USBSerialPrint("Machine state: ");
      serialManager.USBSerialPrintln(currentMachineState);
    }

    if (debuggingSettings.debuggingStatus) {
      serialManager.USBSerialPrint("Probe triggered: ");
      serialManager.USBSerialPrintln(currentProbeTriggered);
    }

    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint("Machine positions X,Y,Z: "); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(currentMachinePositionX); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(","); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(currentMachinePositionY); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(","); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrintln(currentMachinePositionZ); }

    //update WCO cache when GRBL includes it in this status report, then derive job position every time
    bufferPositionX = grblUtil.getJobX(CPUALineArray);
    bufferPositionY = grblUtil.getJobY(CPUALineArray);
    bufferPositionZ = grblUtil.getJobZ(CPUALineArray);
    bufferPositionA = grblUtil.getJobA(CPUALineArray);

    if(bufferPositionX != GRBL_NULL_POSITION && 
    bufferPositionY != GRBL_NULL_POSITION && 
    bufferPositionZ != GRBL_NULL_POSITION){

      wcoX = bufferPositionX;
      wcoY = bufferPositionY;
      wcoZ = bufferPositionZ;

      if(bufferPositionA != GRBL_NULL_POSITION)
        wcoA = bufferPositionA;

      // A height map is only valid for the zero it was scanned against.
      checkHeightMapWorkOffsetChange();

    }

    //job position = machine position - WCO (updated on every status report using cached WCO)
    currentJobPositionX = currentMachinePositionX - wcoX;
    currentJobPositionY = currentMachinePositionY - wcoY;
    currentJobPositionZ = currentMachinePositionZ - wcoZ;
    currentJobPositionA = currentMachinePositionA - wcoA;

    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint("Job position X,Y,Z: "); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(currentJobPositionX); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(","); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(currentJobPositionY); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint(","); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrintln(currentJobPositionZ); }

    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint("Current feed rate: "); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrintln(currentFeedRate); }

    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrint("Current spindle RPM: "); }
    if (debuggingSettings.debuggingStatus) { serialManager.USBSerialPrintln(currentSpindleRPM); }

    return;//we return here to avoid printing the position report to USB, since it is already printed in a more user friendly way above and it is not useful to have the raw position report from GRBL in the USB logs

  }

  // [PRB:x,y,z:1] is sent by GRBL immediately when the probe triggers during G38.2.
  // Capture Z from it — more reliable than polling |Pn:P| in status reports,
  // which may arrive after the retract has already released the probe pin.
  float prbZ;
  if (isProbeToolLengthMacroRunning && !probeToolLengthZCaptured && grblUtil.parsePRBZ(CPUALineArray, &prbZ)) {
    toolLength = prbZ - toolLengthReferenceSettings.refZ;
    probeToolLengthZCaptured = true;
  }

  //in case we are waiting for "ok" from grbl here we check if we have received one.
  //GRBL also sends "error:N" as a response to a command that failed — this also consumes a waitForOk slot.
  //during a job the oks were already counted and consumed at the top of this function, and an error can
  //share the line with a glued ok, so it is checked on its own.
  bool isGrblOk = isJobRunning ? jobOkCount > 0 : grblUtil.checkForOk(CPUALineArray);
  bool isGrblError = (isJobRunning || !isGrblOk) && strstr(CPUALineArray, GRBL_ERROR_PREFIX) != nullptr;

  if (isGrblOk || isGrblError) {

    if (isJobRunning) {
      if (isGrblError && waitForOk > 0)
        --waitForOk;
    } else if (waitForOk > 0) {
      --waitForOk;
    }

    // An error response while probing Z0 means the probe didn't trigger (e.g. error:9).
    // Push failure immediately without waiting for the next status poll.
    if (isGrblError && isProbeZ0MacroRunning) {
      arrayUtil.clear2DCharArray(bufferMacroLines);
      isMacroRunning = false;
      pushProbeZ0CompletedEvent(false);
    }

    // An error response while probing tool length means the probe didn't trigger.
    if (isGrblError && isProbeToolLengthMacroRunning) {
      arrayUtil.clear2DCharArray(bufferMacroLines);
      isMacroRunning = false;
      pushProbeToolLengthCompletedEvent(false);
    }

    if (isGrblOk && !isGrblError)
      return; //we do not print the ok from GRBL to usb
  }

  if(enableCPUAPassthrough && CPUALinePos > 2)
    serialManager.USBSerialPrint(CPUALineArray);//for now used for debug purposes

}

//Logs an ok that only counted thanks to the tolerant job-streaming matching (damaged, or glued to another
//response), with the raw bytes, so link noise shows up in the console instead of as a silent hang.
//Only the first RECOVERED_OK_LOG_MAX_BYTES bytes are dumped, so the buffers stay small on the stack.
#define RECOVERED_OK_LOG_MAX_BYTES 32
void logRecoveredJobOk(int okCount) {

  ++jobRecoveredOkCount;

  char hexBytes[RECOVERED_OK_LOG_MAX_BYTES * 3 + 4];
  int hexPos = 0;
  int i = 0;

  for (; i < CPUALinePos && i < RECOVERED_OK_LOG_MAX_BYTES && CPUALineArray[i] != '\0'; ++i)
    hexPos += snprintf(hexBytes + hexPos, sizeof(hexBytes) - hexPos, "%02X ", (byte)CPUALineArray[i]);

  if (i < CPUALinePos && CPUALineArray[i] != '\0')
    hexPos += snprintf(hexBytes + hexPos, sizeof(hexBytes) - hexPos, "...");

  hexBytes[hexPos] = '\0';

  char logLine[sizeof(hexBytes) + 64];
  snprintf(logLine, sizeof(logLine), "CPUB: recovered %d ok during job (#%lu this job), bytes: %s",
           okCount, jobRecoveredOkCount, hexBytes);
  serialManager.USBSerialPrintln(logLine);
}

void handleJson(){

    //first of all we try to get the filechunk for performance reasons, since it is the only type of message that we expect to receive in chunks, we want to handle it as soon as possible without trying to parse the whole JSON if we can avoid it
    if(!receivedJsonBuffer[JSON_KEY_FILE_CHUNK_LINES_STR].isNull()){

      // Safety: only process fc chunks when a transfer is actually active.
      // If the app sends fc chunks before the transfer is acknowledged (race on
      // the app side), silently discard rather than forwarding GCode content.
      if (!isFileTransferInProgress)
        return;

      handleFileChunkJson();
      return;

    }

    JsonValueType jsonType = jsonUtil.getJsonType(receivedJsonBuffer);

    switch (jsonType)
    {
    case COMMAND:
      handleJsonCommand();
      break;

    case SET:
      handleJsonSet();
      break;

    case GET:
      handleJsonGet();
      break;

    case SERIAL_PING:
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
      resetCurrentSerialPingWatchdog();

    break;

  case WEBSOCKET_PING:

      if(jsonSettings.enableJsonResponse){
  
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    timerUtil.resetWebSocketPingTimer();
    resetCurrentWebSocketPingWatchdog();

    break;

    default:

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_JSON_TYPE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;
    }

}

// Sends a GRBL command to CPUA immediately if the serial channel is idle (waitForOk <= 0),
// or queues it in the macro buffer to be sent as soon as GRBL acknowledges the previous command.
bool scheduleOrSendCPUA(const char* cmd) {
  if(!evaluateAndApplySoftLimitsForOutgoingLine(cmd))
    return false;

  if(waitForOk <= 0) {
    serialManager.CPUASerialPrintln(cmd);
    ++waitForOk;
  } else {
    for(int _i = 0; _i < MACRO_BUFFER_LINES; _i++) {
      if(bufferMacroLines[_i][0] == NULL_CHAR) {
        strncpy(bufferMacroLines[_i], cmd, MACRO_LINE_BUFFER_CHARS - 1);
        bufferMacroLines[_i][MACRO_LINE_BUFFER_CHARS - 1] = NULL_CHAR;
        break;
      }
    }
    isMacroRunning = true;
  }

  return true;
}

bool appendMacroPair(const char* firstCmd, const char* secondCmd) {

  int lastOccupiedIndex = -1;
  for(int index = MACRO_BUFFER_LINES - 1; index >= 0; index--) {
    if(bufferMacroLines[index][0] != NULL_CHAR) {
      lastOccupiedIndex = index;
      break;
    }
  }

  int insertIndex = lastOccupiedIndex + 1;
  if(insertIndex < 0)
    insertIndex = 0;

  if(insertIndex + 1 >= MACRO_BUFFER_LINES)
    return false;

  strncpy(bufferMacroLines[insertIndex], firstCmd, MACRO_LINE_BUFFER_CHARS - 1);
  bufferMacroLines[insertIndex][MACRO_LINE_BUFFER_CHARS - 1] = NULL_CHAR;

  strncpy(bufferMacroLines[insertIndex + 1], secondCmd, MACRO_LINE_BUFFER_CHARS - 1);
  bufferMacroLines[insertIndex + 1][MACRO_LINE_BUFFER_CHARS - 1] = NULL_CHAR;

  isMacroRunning = true;
  return true;

}

void handleJsonCommand(){

  JsonValueCommandType commandType = jsonUtil.getJsonCommandType(receivedJsonBuffer);

  switch (commandType)
  {
  case ENABLE_CPUA_PASSTHROUGH:

    enableCPUAPassthrough = true;
    
    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
      
    break;

  case DISABLE_CPUA_PASSTHROUGH:

    enableCPUAPassthrough = false;

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
      

    break;
  
  case ENABLE_ESP32_PASSTHROUGH:

    enableESP32Passthrough = true;
    
    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
      
    break;

  case DISABLE_ESP32_PASSTHROUGH:

    enableESP32Passthrough = false;

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
      
    break;

  case ENABLE_LASER_SENSOR:

    requestDistanceLaserSensorInitialization(DISTANCE_LASER_SENSOR_INIT_DELAY_MS, true);

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case DISABLE_LASER_SENSOR:

    disableDistanceLaserSensor();

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case START_HEIGHT_MAP_SCAN: {

    if (isJobRunning || isJobPaused || isMacroRunning || isLaserEdgeProbingActive || heightMapScanInProgress || pointCloudScanInProgress) {

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (openedReadFileName[0] == NULL_CHAR) {

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_OPENED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    bool useOpenedFileSize = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_USE_OPENED_FILE_SIZE_STR] | false;

    if(!useOpenedFileSize && (receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR].isNull() || receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR].isNull())) {

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if(receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_DENSITY_STR].isNull()) {

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (!hasDistanceLaserSensorPresence || !isDistanceLaserSensorInitialized) {

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      float requestedSizeX = 0.0f;
      float requestedSizeY = 0.0f;
      // Top-left corner of the opened file's toolpath, in job coordinates, when the metadata carries
      // it. This is what the map has to be anchored to; the toolpath does NOT necessarily start at
      // job zero.
      bool haveFileOrigin = false;
      float fileOriginX = 0.0f;
      float fileOriginY = 0.0f;
      // Top-left corner of an explicitly-sized patch, derived from the head position and the SIGN of
      // the requested sizes.
      bool haveExplicitOrigin = false;
      float explicitOriginX = 0.0f;
      float explicitOriginY = 0.0f;
      const char* density = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_DENSITY_STR].as<const char*>();

      if (useOpenedFileSize) {
        char metadataLine[256];
        float fileSizeX = 0.0f;
        float fileSizeY = 0.0f;
        float fileSizeZ = 0.0f;

        bool metadataParsed = sdCardManager.readFirstLine(openedReadFileName, metadataLine, sizeof(metadataLine))
          && GCodeBoundsUtil::parseMetadataLine(metadataLine, &fileSizeX, &fileSizeY, &fileSizeZ);

        if (!metadataParsed || fileSizeX <= 0.0f || fileSizeY <= 0.0f) {

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;

        }

        requestedSizeX = fileSizeX;
        requestedSizeY = fileSizeY;

        // The metadata also carries the toolpath's actual extent, when the file was written by a
        // version that emits MIN/MAX. Anchor the map on that: SIZEX/SIZEY alone say how big the
        // toolpath is, not where it sits, and a toolpath that starts anywhere other than job zero
        // would otherwise be compensated against a map offset by its own start coordinates.
        // The map grows +X and -Y from its origin, so the anchor is (minX, maxY).
        float boundsMinX = 0.0f, boundsMaxX = 0.0f, boundsMinY = 0.0f, boundsMaxY = 0.0f;
        if (GCodeBoundsUtil::parseMetadataBounds(metadataLine, &boundsMinX, &boundsMaxX, &boundsMinY, &boundsMaxY)
            && (boundsMaxX - boundsMinX) > 0.0f
            && (boundsMaxY - boundsMinY) > 0.0f) {
          haveFileOrigin = true;
          fileOriginX = boundsMinX;
          fileOriginY = boundsMaxY;
          // Use the bounds for the extent too, so origin and size cannot disagree.
          requestedSizeX = boundsMaxX - boundsMinX;
          requestedSizeY = boundsMaxY - boundsMinY;
        }
      } else {
        const float signedSizeX = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR].as<float>();
        const float signedSizeY = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR].as<float>();

        // The SIGN says which way the patch extends from the head; the magnitude is its size.
        //
        // A patch used to always grow +X and -Y, which is only right when the head sits at the work's
        // top-left. Zero a job at its bottom-right corner -- normal for the second side of a
        // double-sided board -- and the scan headed away from the work entirely, off the far side of
        // the table, and refused on a soft limit while the board sat well within reach.
        //
        //   sizeX > 0 extends +X      sizeX < 0 extends -X
        //   sizeY > 0 extends -Y      sizeY < 0 extends +Y
        //
        // Normalised here to a top-left origin plus positive extents, which is what everything
        // downstream expects: the soft-limit rectangle, the serpentine sweep, the column binning and
        // the interpolation all assume the map grows +X and -Y from its origin. Positive values
        // behave exactly as before, so nothing that already worked changes.
        explicitOriginX = currentJobPositionX + ((signedSizeX < 0.0f) ? signedSizeX : 0.0f);
        explicitOriginY = currentJobPositionY + ((signedSizeY < 0.0f) ? -signedSizeY : 0.0f);
        haveExplicitOrigin = true;

        requestedSizeX = fabsf(signedSizeX);
        requestedSizeY = fabsf(signedSizeY);
      }

      if(requestedSizeX <= 0.0f || requestedSizeY <= 0.0f) {

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if (density == nullptr
          || (strcmp(density, JSON_VALUE_HEIGHT_MAP_DENSITY_COARSE_STR) != 0
              && strcmp(density, JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR) != 0)) {

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      heightMapSizeXmm = requestedSizeX;
      heightMapSizeYmm = requestedSizeY;
      heightMapFineDensity = (strcmp(density, JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR) == 0);
      heightMapConfigured = true;

      float spacingMm = heightMapFineDensity ? HEIGHT_MAP_FINE_SPACING_MM : HEIGHT_MAP_COARSE_SPACING_MM;

      uint16_t computedX = getHeightMapGridPointsForSize(heightMapSizeXmm, spacingMm, HEIGHT_MAP_MAX_POINTS_PER_AXIS);
      uint16_t computedY = getHeightMapGridPointsForSize(heightMapSizeYmm, spacingMm, HEIGHT_MAP_MAX_POINTS_PER_AXIS);

      if (computedX < 2 || computedY < 2) {

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      // Where the map's (0,0) cell sits, in job coordinates.
      //
      // Anchored on the opened file's own toolpath bounds when the metadata carries them, so "scan
      // the opened file" covers exactly the rectangle the toolpath occupies no matter where the head
      // happens to be parked. Jogging to look at something before pressing scan no longer slides the
      // map off the work.
      //
      // Otherwise the head position is the anchor: with an explicit size the operator is deliberately
      // scanning a patch, and for a file written before MIN/MAX metadata existed the head is the only
      // information there is about where the toolpath sits. In that case park on the toolpath's
      // top-left corner before starting. getHeightMapStatus reports the origin actually used.
      float scanOriginX = haveFileOrigin ? fileOriginX
                        : (haveExplicitOrigin ? explicitOriginX : currentJobPositionX);
      float scanOriginY = haveFileOrigin ? fileOriginY
                        : (haveExplicitOrigin ? explicitOriginY : currentJobPositionY);

      // NOTE: there was a second pre-flight here requiring the laser to reach job (0,0), because the
      // stationary reading used to be taken there. With a +9.21 mm Y laser offset and a machine whose
      // Y ceiling is 0, that demanded job zero sit at least that far down the bed and refused almost
      // every scan on a soft limit, whatever its size. The reading now happens over the map's own
      // origin, which the rectangle check below already covers, so the extra constraint is gone.

      // Pre-flight: ensure the scan rectangle fits within the configured soft limits.
      // The scan drives the SPINDLE to gridTarget + laser offset (so the LASER lands on each
      // grid point), so the spindle-travel rectangle is the job rectangle shifted by the laser
      // offset. Check that shifted rectangle — this is why the usable scan area shrinks by the
      // laser offset (~xBackMm in X / yBackMm in Y) near the envelope edges.
      if (!isScanRectWithinSoftLimitRange(scanOriginX + laserOffsetZeroSettings.xBackMm,
                                          scanOriginY + laserOffsetZeroSettings.yBackMm,
                                          heightMapSizeXmm, heightMapSizeYmm)) {

        // Say WHAT is out of reach and BY HOW MUCH. "softLimitExceeded" alone leaves no way to tell
        // whether the scan is too big, the work is too close to an envelope edge, or the laser offset
        // is eating the margin -- which are three different fixes. The numbers below are the SPINDLE
        // rectangle in machine coordinates: the job rectangle shifted by the laser offset, because
        // the spindle has to carry the laser to every grid point.
        const float spindleMinX = scanOriginX + laserOffsetZeroSettings.xBackMm + wcoX;
        const float spindleMaxX = spindleMinX + heightMapSizeXmm;
        const float spindleMaxY = scanOriginY + laserOffsetZeroSettings.yBackMm + wcoY;
        const float spindleMinY = spindleMaxY - heightMapSizeYmm;

        char dbg[260];
        snprintf(dbg, sizeof(dbg),
                 "HeightMap soft limit: spindle would need machine X[%.2f..%.2f] Y[%.2f..%.2f]; limits are X[%.1f..%.1f] Y[%.1f..%.1f]. Over by X-%.2f X+%.2f Y-%.2f Y+%.2f (laser offset %.2f,%.2f; job zero at machine %.2f,%.2f)",
                 spindleMinX, spindleMaxX, spindleMinY, spindleMaxY,
                 (double)SOFT_LIMIT_MIN_X_MM, (double)SOFT_LIMIT_MAX_X_MM,
                 (double)SOFT_LIMIT_MIN_Y_MM, (double)SOFT_LIMIT_MAX_Y_MM,
                 spindleMinX < SOFT_LIMIT_MIN_X_MM ? (double)(SOFT_LIMIT_MIN_X_MM - spindleMinX) : 0.0,
                 spindleMaxX > SOFT_LIMIT_MAX_X_MM ? (double)(spindleMaxX - SOFT_LIMIT_MAX_X_MM) : 0.0,
                 spindleMinY < SOFT_LIMIT_MIN_Y_MM ? (double)(SOFT_LIMIT_MIN_Y_MM - spindleMinY) : 0.0,
                 spindleMaxY > SOFT_LIMIT_MAX_Y_MM ? (double)(spindleMaxY - SOFT_LIMIT_MAX_Y_MM) : 0.0,
                 laserOffsetZeroSettings.xBackMm, laserOffsetZeroSettings.yBackMm,
                 wcoX, wcoY);
        serialManager.USBSerialPrint(FROM_CPUB);
        serialManager.USBSerialPrintln(dbg);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_SOFT_LIMIT_EXCEEDED_STR;
          // Where the map would have sat, so the frontend can show the operator what to move.
          responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_X_MM_STR] = roundf(scanOriginX * 1000.0f) / 1000.0f;
          responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_Y_MM_STR] = roundf(scanOriginY * 1000.0f) / 1000.0f;
          responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR] = heightMapSizeXmm;
          responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR] = heightMapSizeYmm;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      invalidateHeightMap(false);
      heightMapInputSource = currentInputSource;
      heightMapGridPointsX = computedX;
      heightMapGridPointsY = computedY;
      heightMapTotalPoints = heightMapGridPointsX * heightMapGridPointsY;
      heightMapSpacingXmm = (heightMapGridPointsX > 1) ? (heightMapSizeXmm / (float)(heightMapGridPointsX - 1)) : 0.0f;
      heightMapSpacingYmm = (heightMapGridPointsY > 1) ? (heightMapSizeYmm / (float)(heightMapGridPointsY - 1)) : 0.0f;
      heightMapOriginX = scanOriginX;
      heightMapOriginY = scanOriginY;
      // The scan still parks back where it was launched from, which is not the map origin when the
      // map is anchored at job zero.
      heightMapReturnX = currentJobPositionX;
      heightMapReturnY = currentJobPositionY;
      // Remember the work offset the map is being built against, so a later re-zero can be detected.
      heightMapWcoX = wcoX;
      heightMapWcoY = wcoY;
      // With the offset applied to the scan moves, the laser now samples ON each grid point,
      // so the map origin's sample sits at the spindle's start machine position (no laser-back
      // subtraction). This anchors the machine-coord points export to the true sampled points.
      heightMapStartMachineX = scanOriginX + wcoX;
      heightMapStartMachineY = scanOriginY + wcoY;
      // Ensure the laser stays enabled throughout scan acquisition.
      bool laserWasDisabled = (digitalRead(LASER_SENSOR_ENABLE_PIN) != HIGH);
      digitalWrite(LASER_SENSOR_ENABLE_PIN, HIGH);
      heightMapScanInProgress = true;
      heightMapSensorReadyMs = millis() + (laserWasDisabled ? LASER_SENSOR_ENABLE_RESPONSE_DELAY_MS : 0);
      heightMapScanState = HEIGHT_MAP_SCAN_WAIT_SENSOR_READY;
      heightMapNextPointIndex = 0;
      heightMapWaitOkSinceMs = 0;
    }

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_X_STR] = heightMapGridPointsX;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_Y_STR] = heightMapGridPointsY;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DENSITY_STR] =
        heightMapFineDensity ? JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR : JSON_VALUE_HEIGHT_MAP_DENSITY_COARSE_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;
  }

  case ENABLE_HEIGHT_MAP_COMPENSATION:

    heightMapCompensationEnabled = true;

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COMPENSATION_ENABLED_STR] = true;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case DISABLE_HEIGHT_MAP_COMPENSATION:

    heightMapCompensationEnabled = false;

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COMPENSATION_ENABLED_STR] = false;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case CLEAR_HEIGHT_MAP:

    invalidateHeightMap(false);
    heightMapCompensationEnabled = false;

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case START_POINT_CLOUD_SCAN:
  {
    if (isJobRunning || isJobPaused) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if (pointCloudScanInProgress) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_POINT_CLOUD_SCAN_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if (receivedJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_X_MM_STR].isNull()
        || receivedJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_Y_MM_STR].isNull()
        || receivedJsonBuffer[JSON_KEY_POINT_CLOUD_DENSITY_STR].isNull()
        || receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    float requestedSizeX = receivedJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_X_MM_STR].as<float>();
    float requestedSizeY = receivedJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_Y_MM_STR].as<float>();
    const char* density  = receivedJsonBuffer[JSON_KEY_POINT_CLOUD_DENSITY_STR].as<const char*>();
    const char* userFilename = receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>();

    if (requestedSizeX <= 0.0f || requestedSizeX > POINT_CLOUD_MAX_SCAN_DIMENSION_X_MM
        || requestedSizeY <= 0.0f || requestedSizeY > POINT_CLOUD_MAX_SCAN_DIMENSION_Y_MM) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    // Pre-flight: ensure the scan rectangle fits within the configured soft limits
    // (machine coords). Origin is the current job position; rectangle extends to
    // (originX + sizeX, originY + sizeY). Reject before any state mutation.
    if (!isScanRectWithinSoftLimitRange(currentJobPositionX, currentJobPositionY,
                                        requestedSizeX, requestedSizeY)) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_SOFT_LIMIT_EXCEEDED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    float spacing = 0.0f;
    if (strcmp(density, JSON_VALUE_POINT_CLOUD_DENSITY_ULTRAFINE_STR) == 0)
      spacing = POINT_CLOUD_SPACING_ULTRAFINE_MM;
    else if (strcmp(density, JSON_VALUE_POINT_CLOUD_DENSITY_FINE_STR) == 0)
      spacing = POINT_CLOUD_SPACING_FINE_MM;
    else if (strcmp(density, JSON_VALUE_POINT_CLOUD_DENSITY_MEDIUM_STR) == 0)
      spacing = POINT_CLOUD_SPACING_MEDIUM_MM;
    else if (strcmp(density, JSON_VALUE_POINT_CLOUD_DENSITY_COARSE_STR) == 0)
      spacing = POINT_CLOUD_SPACING_COARSE_MM;
    else {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if (userFilename == nullptr || userFilename[0] == NULL_CHAR
        || strlen(userFilename) >= (SD_MAX_FILE_NAME_LENGTH - 4)) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    uint16_t gx = (uint16_t)((requestedSizeX / spacing) + 1.5f);
    uint16_t gy = (uint16_t)((requestedSizeY / spacing) + 1.5f);
    if (gx < 2) gx = 2;
    if (gy < 2) gy = 2;
    if (gx > POINT_CLOUD_MAX_POINTS_PER_AXIS) gx = POINT_CLOUD_MAX_POINTS_PER_AXIS;
    if (gy > POINT_CLOUD_MAX_POINTS_PER_AXIS) gy = POINT_CLOUD_MAX_POINTS_PER_AXIS;

    arrayUtil.clearCharArray(pointCloudFilename, SD_MAX_FILE_NAME_LENGTH);
    snprintf(pointCloudFilename, SD_MAX_FILE_NAME_LENGTH, "%s%s", userFilename, POINT_CLOUD_FILENAME_SUFFIX);
    arrayUtil.clearCharArray(pointCloudDensityStr, sizeof(pointCloudDensityStr));
    strncpy(pointCloudDensityStr, density, sizeof(pointCloudDensityStr) - 1);

    pointCloudSizeXmm = requestedSizeX;
    pointCloudSizeYmm = requestedSizeY;
    pointCloudSpacingMm = spacing;
    pointCloudGridPointsX = 0;        // unknown in line scan mode
    pointCloudGridPointsY = gy;
    pointCloudTotalPoints = 0;        // unknown in line scan mode
    pointCloudOriginX = currentJobPositionX;
    pointCloudOriginY = currentJobPositionY;
    pointCloudNextPointIndex = 0;
    pointCloudWaitOkSinceMs = 0;
    pointCloudReady = false;
    pointCloudInputSource = currentInputSource;
    pointCloudAbortStage = nullptr;
    pointCloudAbortDetail = nullptr;

    pointCloudFileYear = 0;
    pointCloudFileMonth = 0;
    pointCloudFileDay = 0;
    pointCloudFileHour = 0;
    pointCloudFileMinute = 0;
    pointCloudFileSecond = 0;
    if (!receivedJsonBuffer[JSON_KEY_FILE_DATE_STR].isNull()) {
      const char* dateStr = receivedJsonBuffer[JSON_KEY_FILE_DATE_STR].as<const char*>();
      sscanf(dateStr, "%hu-%hhu-%hhu %hhu:%hhu:%hhu",
        &pointCloudFileYear, &pointCloudFileMonth, &pointCloudFileDay,
        &pointCloudFileHour, &pointCloudFileMinute, &pointCloudFileSecond);
    }

    bool laserWasDisabled = (digitalRead(LASER_SENSOR_ENABLE_PIN) != HIGH);
    digitalWrite(LASER_SENSOR_ENABLE_PIN, HIGH);
    pointCloudScanInProgress = true;
    pointCloudSensorReadyMs = millis() + (laserWasDisabled ? LASER_SENSOR_ENABLE_RESPONSE_DELAY_MS : 0);
    pointCloudScanState = POINT_CLOUD_SCAN_WAIT_SENSOR_READY;

    if (jsonSettings.enableJsonResponse) {
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_FILENAME_STR] = pointCloudFilename;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_X_STR] = pointCloudGridPointsX;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_Y_STR] = pointCloudGridPointsY;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_TOTAL_STR] = pointCloudTotalPoints;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_DENSITY_STR] = pointCloudDensityStr;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }
  }
  break;

  case ABORT_POINT_CLOUD_SCAN:

    if (!pointCloudScanInProgress) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_POINT_CLOUD_SCAN_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    abortPointCloudScan("ABORT", "user_requested");

    if (jsonSettings.enableJsonResponse) {
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    break;

  case ABORT_HEIGHT_MAP_SCAN:

    if (!heightMapScanInProgress) {
      if (jsonSettings.enableJsonResponse) {
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_HEIGHT_MAP_SCAN_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    abortHeightMapScan("ABORT", "user_requested");

    if (jsonSettings.enableJsonResponse) {
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    break;

    case MOVE_TO_ROTARY_POSITION_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

    }

    if(isMacroRunning){

       if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

    }

    if(jsonSettings.enableJsonResponse){

      isMacroRunning = true;
      isRotaryYLockBypassed = true;

      arrayUtil.clear2DCharArray(bufferMacroLines);
      arrayUtil.copyMacro(bufferMacroLines, MOVE_TO_ROTARY_POSITION);

      // Override the rotary Y target (index 1: "G53 Y...") with the value for the
      // installed spindle variant, since the two spindles seat the rotary differently.
      snprintf(bufferMacroLines[1], MACRO_LINE_BUFFER_CHARS, "G53 Y%.3f",
               (INSTALLED_SPINDLE_TYPE == SPINDLE_48V) ? MOVE_TO_ROTARY_Y_48V : MOVE_TO_ROTARY_Y_24V);

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

    case FRAME_JOB_MACRO:

      if(isJobRunning || isJobPaused){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(isMacroRunning){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(isRotaryAxisConnected){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(openedReadFileName[0] == NULL_CHAR){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_OPENED_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      {

        char frameMetadataLine[256];
        float frameMinX = 0.0f, frameMaxX = 0.0f, frameMinY = 0.0f, frameMaxY = 0.0f;

        bool frameBoundsOk =
          sdCardManager.readFirstLine(openedReadFileName, frameMetadataLine, sizeof(frameMetadataLine))
          && GCodeBoundsUtil::parseMetadataBounds(frameMetadataLine, &frameMinX, &frameMaxX, &frameMinY, &frameMaxY);

        if(!frameBoundsOk){

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_BOUNDS_UNAVAILABLE_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;

        }

        if(jsonSettings.enableJsonResponse){

          isMacroRunning = true;

          // Trace the job bounding box at the CURRENT Z (no Z motion), in work coordinates.
          // The stored MIN/MAX already reference the job-zero (WCS) origin, so the rectangle
          // frames exactly the space the job will occupy.
          //
          // First move to job zero (G1 X0 Y0 F1000) so the frame always begins from zero: the
          // following move to the min corner is then the real first edge of the frame, not a
          // diagonal from wherever the tool happened to be. The frame itself runs at F1200.
          // With job zero at a corner of the bounds (the usual case) the lead-in from zero draws
          // the remaining edge, so the closing move to X0 Y0 starts and ends the frame at zero
          // with no extra move to the bottom corner.
          arrayUtil.clear2DCharArray(bufferMacroLines);
          snprintf(bufferMacroLines[0], MACRO_LINE_BUFFER_CHARS, "G90");
          snprintf(bufferMacroLines[1], MACRO_LINE_BUFFER_CHARS, "G1 X0 Y0 F1000");
          snprintf(bufferMacroLines[2], MACRO_LINE_BUFFER_CHARS, "G1 F1200 X%.3f Y%.3f", frameMinX, frameMinY);
          snprintf(bufferMacroLines[3], MACRO_LINE_BUFFER_CHARS, "G1 X%.3f", frameMaxX);
          snprintf(bufferMacroLines[4], MACRO_LINE_BUFFER_CHARS, "G1 Y%.3f", frameMaxY);
          snprintf(bufferMacroLines[5], MACRO_LINE_BUFFER_CHARS, "G1 X%.3f", frameMinX);
          snprintf(bufferMacroLines[6], MACRO_LINE_BUFFER_CHARS, "G1 X0 Y0");

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }

      break;

    case INSTALL_ROTARY_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning){

         if(jsonSettings.enableJsonResponse){
      
            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();
        
          }

          break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;
        isRotaryYLockBypassed = true;

        arrayUtil.clear2DCharArray(bufferMacroLines);
        arrayUtil.copyMacro(bufferMacroLines, INSTALL_ROTARY);

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    case PROBE_SET_JOB_Z0_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning){

         if(jsonSettings.enableJsonResponse){
      
            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();
        
          }

          break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;
        isProbeZ0MacroRunning = true;
        probeZ0MacroInputSource = currentInputSource;

        arrayUtil.clear2DCharArray(bufferMacroLines);
        arrayUtil.copyMacro(bufferMacroLines, PROBE_SET_JOB_Z0);

        // Build the Z-offset lines at runtime from the EEPROM-backed probe Z0
        // offset (see setProbeHeight): the touch point becomes Z = offset and
        // the retract goes to offset + margin. The values in JobMacros.h are
        // placeholders overwritten here.
        snprintf(bufferMacroLines[3], MACRO_LINE_BUFFER_CHARS, "G10 L20 P1 Z%.3f", probeHeightSettings.heightMm);
        snprintf(bufferMacroLines[4], MACRO_LINE_BUFFER_CHARS, "G1 Z%.3f F250", probeHeightSettings.heightMm + PROBE_HEIGHT_RETRACT_MARGIN_MM);

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    case PROBE_TOOL_LENGTH_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning){

        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;
        isProbeToolLengthMacroRunning = true;
        probeToolLengthZCaptured = false;
        probeToolLengthMacroInputSource = currentInputSource;

        // Build probe line dynamically: travel to just above soft limit from current Z,
        // so it works regardless of where Z currently is.
        float probeTravel = (SOFT_LIMIT_MIN_Z_MM + 0.5f) - currentMachinePositionZ;
        arrayUtil.clear2DCharArray(bufferMacroLines);
        strncpy(bufferMacroLines[0], "G91", MACRO_LINE_BUFFER_CHARS - 1);
        snprintf(bufferMacroLines[1], MACRO_LINE_BUFFER_CHARS, "G38.2 Z%.3f F%d", probeTravel, TOOL_LENGTH_PROBE_FEED_RATE_MM_MIN);
        snprintf(bufferMacroLines[2], MACRO_LINE_BUFFER_CHARS, "G1 Z%d F250", TOOL_LENGTH_PROBE_RETRACT_MM);
        strncpy(bufferMacroLines[3], "G90", MACRO_LINE_BUFFER_CHARS - 1);
        bufferMacroLines[4][0] = '\0';

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    case RETURN_JOB_XY0_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning){

         if(jsonSettings.enableJsonResponse){
      
            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();
        
          }

          break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;

        arrayUtil.clear2DCharArray(bufferMacroLines);
        arrayUtil.copyMacro(bufferMacroLines, RETURN_JOB_XY0);

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    case MOVE_TO_LASER_OFFSET_ZERO_MACRO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning){

         if(jsonSettings.enableJsonResponse){
      
            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();
        
          }

          break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;

        arrayUtil.clear2DCharArray(bufferMacroLines);
        strncpy(bufferMacroLines[0], "G91", MACRO_LINE_BUFFER_CHARS - 1);
        snprintf(
          bufferMacroLines[1],
          MACRO_LINE_BUFFER_CHARS,
          "G1 X-%.3f Y-%.3f F%d",
          laserOffsetZeroSettings.xBackMm,
          laserOffsetZeroSettings.yBackMm,
          LASER_OFFSET_ZERO_FEED_RATE_MM_MIN
        );
        strncpy(bufferMacroLines[2], "G90", MACRO_LINE_BUFFER_CHARS - 1);
        strncpy(bufferMacroLines[3], "G10 L20 P1 X0 Y0", MACRO_LINE_BUFFER_CHARS - 1);
        bufferMacroLines[4][0] = '\0';

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    case CONFIRM_MATERIAL_CENTER_AND_SET_ZERO:

      if(isJobRunning || isJobPaused){
      
        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(isMacroRunning || isLaserEdgeProbingActive || isLaserMaterialBoundsProbingActive){

         if(jsonSettings.enableJsonResponse){
      
            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();
        
          }

          break;

      }

      if(!hasLastDetectedMaterialCenter){

        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MATERIAL_CENTER_NOT_AVAILABLE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        
        }

        break;

      }

      if(jsonSettings.enableJsonResponse){

        isMacroRunning = true;

        arrayUtil.clear2DCharArray(bufferMacroLines);
        strncpy(bufferMacroLines[0], "G90", MACRO_LINE_BUFFER_CHARS - 1);
        snprintf(
          bufferMacroLines[1],
          MACRO_LINE_BUFFER_CHARS,
          "G1 X%.3f Y%.3f F%d",
          lastDetectedMaterialCenterX,
          lastDetectedMaterialCenterY,
          LASER_EDGE_PROBE_RETURN_FEED_RATE_MM_MIN
        );
        strncpy(bufferMacroLines[2], "G91", MACRO_LINE_BUFFER_CHARS - 1);
        snprintf(
          bufferMacroLines[3],
          MACRO_LINE_BUFFER_CHARS,
          "G1 X-%.3f Y-%.3f F%d",
          laserOffsetZeroSettings.xBackMm,
          laserOffsetZeroSettings.yBackMm,
          LASER_OFFSET_ZERO_FEED_RATE_MM_MIN
        );
        strncpy(bufferMacroLines[4], "G90", MACRO_LINE_BUFFER_CHARS - 1);
        strncpy(bufferMacroLines[5], "G10 L20 P1 X0 Y0", MACRO_LINE_BUFFER_CHARS - 1);
        bufferMacroLines[6][0] = '\0';

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;
  
  case OPEN_GCODE_FILE:

    if(!receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()){

      if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrint("Filename received: "); }
      if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrintln(receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>()); }

      arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
      strcat(openedReadFileName, receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>());

      long fileTotalLines = sdCardManager.countFileLines(openedReadFileName);
      long executableFileTotalLines = getExecutableFileLineCount(openedReadFileName, fileTotalLines);

      if(fileTotalLines >= 0 && sdCardManager.openGCodeFiletoRead(openedReadFileName)){

        openedReadFileTotalLines = executableFileTotalLines > 0 ? (unsigned long)executableFileTotalLines : 0;
        jobLineIndex = 0;
        // Drop the height map only when opening a DIFFERENT file than the one it's bound to; re-opening
        // the same file keeps its map so compensation survives until another file is opened.
        if (strcmp(openedReadFileName, heightMapBoundFile) != 0)
          invalidateHeightMap(false);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
          
        }
          
      }else{

        arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
        openedReadFileTotalLines = 0;
        jobLineIndex = 0;
        invalidateHeightMap(false);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

    }
  
  }else{
   
    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
  
  }
    
    break;

  case CLOSE_GCODE_FILE:

    sdCardManager.closeFile();

    if(openedReadFileName[0] == NULL_CHAR){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }
      
    }else{

      arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
      openedReadFileTotalLines = 0;
      jobLineIndex = 0;
      invalidateHeightMap(false);

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

    }    
  
    break;

  case DELETE_GCODE_FILE:

    if(!receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()){

      const char* fileToDelete = receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>();

      if(!sdCardManager.fileExists(fileToDelete)){

        if(jsonSettings.enableJsonResponse){
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        }

      }else{

        bool deleted = sdCardManager.deleteFile(fileToDelete);

        if (deleted)
          deleteHashSidecarIfPresent(fileToDelete);

        if(jsonSettings.enableJsonResponse){
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = deleted ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        }

      }

    }else{

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

    }

    break;

  case RENAME_GCODE_FILE:

    if(receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull() || receivedJsonBuffer[JSON_KEY_NEW_FILENAME_STR].isNull()){

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    {
      const char* oldFilename = receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>();
      const char* newFilename = receivedJsonBuffer[JSON_KEY_NEW_FILENAME_STR].as<const char*>();

      if(!sdCardManager.isNcFilename(oldFilename) || !sdCardManager.isNcFilename(newFilename)){

        if(jsonSettings.enableJsonResponse){
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        }

        break;

      }

      if(!sdCardManager.fileExists(oldFilename)){

        if(jsonSettings.enableJsonResponse){
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        }

        break;

      }

      if(sdCardManager.fileExists(newFilename)){

        if(jsonSettings.enableJsonResponse){
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_ALREADY_EXISTS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
        }

        break;

      }

      bool reopenRenamedFile = strcmp(openedReadFileName, oldFilename) == 0;
      if(reopenRenamedFile)
        sdCardManager.closeFile();

      bool renamed = sdCardManager.renameFile(oldFilename, newFilename);

      if (renamed && !renameHashSidecarIfPresent(oldFilename, newFilename)) {
        sdCardManager.renameFile(newFilename, oldFilename);
        renamed = false;
      }

      if(renamed && reopenRenamedFile){
        arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
        strcat(openedReadFileName, newFilename);

        if(sdCardManager.openGCodeFiletoRead(openedReadFileName)) {
          long reopenedFileTotalLines = sdCardManager.countFileLines(openedReadFileName);
          long reopenedExecutableFileTotalLines = getExecutableFileLineCount(openedReadFileName, reopenedFileTotalLines);
          if(reopenedFileTotalLines >= 0)
            openedReadFileTotalLines = reopenedExecutableFileTotalLines > 0 ? (unsigned long)reopenedExecutableFileTotalLines : 0;
        } else {
          arrayUtil.clearCharArray(openedReadFileName, SD_MAX_FILE_NAME_LENGTH);
          openedReadFileTotalLines = 0;
          jobLineIndex = 0;
        }
      }

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = renamed ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
        if(!renamed)
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_RENAME_FAILED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      if (renamed)
        invalidateHeightMap(false);
    }

    break;

  case CLEAR_EEPROM:

    eepromManager.clearEEPROM();

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
    
    break;    

    case START_FILE_TRANSFER:

    if(isFileDownloadInProgress){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_DOWNLOAD_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
  
      }

      break;

    }

    if(isJobRunning || isJobPaused){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
    
      }

      break;

    }

    if(!receivedJsonBuffer[JSON_KEY_FILE_LINES_STR].isNull() 
      && !receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()
      && !receivedJsonBuffer[JSON_KEY_FILE_DATE_STR].isNull()
      && transferFileLines == 0){
      
      arrayUtil.clearCharArray(transferFileName, SD_MAX_FILE_NAME_LENGTH);
      strcat(transferFileName, receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>());
      strcat(transferFileName, TMP_FILENAME);

      // Remove any leftover TMP from a previous failed upload so createTextFile succeeds
      if (sdCardManager.fileExists(transferFileName))
        sdCardManager.deleteFile(transferFileName);

      if(sdCardManager.createTextFile(transferFileName) && sdCardManager.openTextFileForWriting(transferFileName)){

        transferFileLines = receivedJsonBuffer[JSON_KEY_FILE_LINES_STR].as<int>();
        isFileTransferInProgress = true;
        fileTransferInputSource = currentInputSource; // remember where to send a timeout/abort notification
        fileTransferHasExistingMetadata = false;
        fileTransferMetadataChecked = false;
        fileTransferFailureReason = FILE_TRANSFER_FAILURE_NONE;
        fileTransferFailureSizeX = 0.0f;
        fileTransferFailureSizeY = 0.0f;
        fileTransferFailureSizeZ = 0.0f;
        fileTransferLastChunkActivityMs = millis();
        gcodeBoundsUtil.reset();

        const char* dateStr = receivedJsonBuffer[JSON_KEY_FILE_DATE_STR].as<const char*>();
        sscanf(dateStr, "%hu-%hhu-%hhu %hhu:%hhu:%hhu", &transferFileYear, &transferFileMonth, &transferFileDay, &transferFileHour, &transferFileMinute, &transferFileSecond);
        sdCardManager.setWriteFileTimestamp(transferFileYear, transferFileMonth, transferFileDay, transferFileHour, transferFileMinute, transferFileSecond);

        if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrint("Filename received for transfer: "); }
        if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrintln(transferFileName); }
        if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrint("Number of lines received for transfer: "); }
        if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrintln(transferFileLines); }

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }else{

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_CREATION_FAILED_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        resetFileTransferState(true);

      }


        

    }else{

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
  
      }

    }
    

    break;

  case START_FILE_DOWNLOAD:

    if(isFileTransferInProgress){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_TRANSFER_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if(isFileDownloadInProgress){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_DOWNLOAD_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if(receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      const char* filename = receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>();
      long fileSize = sdCardManager.getFileSize(filename);
      // Counting is a blocking full-file read, so it is only done for small files; a multi-MB file
      // would stall loop() (and with it USB/WebSocket) for many seconds before this reply goes out.
      // 0 means "unknown" -- clients fall back to downloadedBytes / fileSize for progress.
      long totalLines = 0;
      if(fileSize >= 0 && fileSize <= DOWNLOAD_LINE_COUNT_MAX_FILE_SIZE_BYTES)
        totalLines = sdCardManager.countNonCommentFileLines(filename);

      if(fileSize < 0 || totalLines < 0 || !sdCardManager.openDownloadFileToRead(filename)){

        resetFileDownloadState();

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      isFileDownloadInProgress = true;
      fileDownloadLastChunkActivityMs = millis();
      arrayUtil.clearCharArray(downloadFileName, SD_MAX_FILE_NAME_LENGTH);
      strcat(downloadFileName, filename);
      downloadFileCurrentLine = 0;
      downloadFileTotalLines = (unsigned long)totalLines;
      downloadChunkSendFailures = 0;

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_FILE_NAME_STR] = downloadFileName;
        responseJsonBuffer[JSON_KEY_FILE_SIZE_STR] = fileSize;
        responseJsonBuffer[JSON_KEY_TOTAL_FILE_LINES_STR] = downloadFileTotalLines;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }
    }

    break;

  case ABORT_FILE_DOWNLOAD:

    if(!isFileDownloadInProgress){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_DOWNLOAD_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    resetFileDownloadState();

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case ABORT_FILE_TRANSFER:

    resetFileTransferState(true);

    if(jsonSettings.enableJsonResponse){
  
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
  
    }

    break;

  case START_JOB:

    if(isFileTransferInProgress){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_TRANSFER_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
  
      }

      break;

    }

    if(isMacroRunning){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    // Housing installed + lid open: a real job cuts, so it is refused outright rather than
    // started and paused at the first M3. Dry runs stay allowed — sanitizeLineForDryRun
    // strips every M/S/Z word, so nothing spins and nothing plunges.
    if(isDoorInterlockActiveNow() && !dryRunEnabled){

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DOOR_OPEN_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Job start refused: lid open (housing installed). Only dry runs are allowed.");

      break;

    }

    if(isJobRunning){

      if(jsonSettings.enableJsonResponse){
    
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
    
      }

    }else
    if(openedReadFileName[0] != NULL_CHAR ){

      if (heightMapCompensationEnabled) {
        bool heightMapBoundMatches = (strcmp(openedReadFileName, heightMapBoundFile) == 0);
        if (!heightMapReady || !heightMapBoundMatches || heightMapUsedForCurrentFile) {

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_HEIGHT_MAP_RESCAN_REQUIRED_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;
        }

        // The map must actually COVER the toolpath. interpolateHeightMapZ clamps any lookup that
        // falls outside the scanned rectangle to the nearest edge cell, silently -- so a map anchored
        // over the wrong area does not fail, it feeds edge values to the whole job and the tool rides
        // at a constant error for the entire run. Checked on every start, including when the map was
        // sized from this same file: matching SIZES do not imply matching POSITIONS.
        {
          bool coverageKnown = false;
          bool covers = doesHeightMapCoverOpenedJob(&coverageKnown);

          if (coverageKnown) {

            float jobMinX = 0.0f, jobMaxX = 0.0f, jobMinY = 0.0f, jobMaxY = 0.0f;
            readOpenedFileBounds(&jobMinX, &jobMaxX, &jobMinY, &jobMaxY);
            float mapMinX = heightMapOriginX;
            float mapMaxX = heightMapOriginX + heightMapSizeXmm;
            float mapMaxY = heightMapOriginY;
            float mapMinY = heightMapOriginY - heightMapSizeYmm;

            if (!covers) {

              char dbg[200];
              snprintf(dbg, sizeof(dbg),
                       "HeightMap does not cover the job: job X[%.3f..%.3f] Y[%.3f..%.3f] map X[%.3f..%.3f] Y[%.3f..%.3f]",
                       jobMinX, jobMaxX, jobMinY, jobMaxY, mapMinX, mapMaxX, mapMinY, mapMaxY);
              serialManager.USBSerialPrint(FROM_CPUB);
              serialManager.USBSerialPrintln(dbg);

              if(jsonSettings.enableJsonResponse){

                clearResponseJsonBuffer();
                responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
                responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_HEIGHT_MAP_DOES_NOT_COVER_JOB_STR;
                // Where the map actually is, so the frontend can say what to rescan.
                responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_X_MM_STR] = roundf(heightMapOriginX * 1000.0f) / 1000.0f;
                responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_Y_MM_STR] = roundf(heightMapOriginY * 1000.0f) / 1000.0f;
                responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR] = heightMapSizeXmm;
                responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR] = heightMapSizeYmm;
                serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
                sendJsonResponse();

              }

              break;

            }

          } else {

            // Written before MIN/MAX metadata existed, so coverage cannot be verified here. The scan
            // anchor falls back to the head position for such files too, so it is on the operator to
            // have started the scan on the toolpath's top-left corner.
            serialManager.USBSerialPrint(FROM_CPUB);
            serialManager.USBSerialPrintln("HeightMap coverage NOT verified: opened file carries no MIN/MAX metadata");

          }
        }
      }

      // Clear any leftover outstanding-ok count from a prior operation. If
      // waitForOk leaked > 0 (e.g. an aborted probe/scan or a lost status ok),
      // handleJob() and handleManualSpindleRamp() would both stall at the start
      // of this job — the job would show as running but neither the first line
      // nor the spindle ramp would be sent. Reset before the dry-run block so
      // its homing-Z ++waitForOk below is preserved.
      waitForOk = 0;
      jobRecoveredOkCount = 0;

      // Start the file from a clean spindle state. A prior pause/resume "recycle"
      // drives the GRBL spindle override below 100% and, if it was interrupted or
      // never fully restored, GRBL keeps that reduced override — which would make
      // every commanded RPM in this new file run low for the whole job. Clear any
      // leftover resume-restore state and reset the override to 100% so the file
      // relies solely on the normal manual RPM ramp (handleManualSpindleRamp),
      // never the resume override ramp. The modal spindle memory (M3 flag,
      // memorized S, held line) is also cleared so nothing leaks from the
      // console or a previous file into this job.
      resetManualSpindleRampState();
      resetSpindleModalState(false);
      isResumeSpindleOverrideRampActive = false;
      resumeMotionAfterOverrideRampPending = false;
      resumeSpindleRestoreStage = RESUME_RESTORE_DROP;
      pauseResumeSpindleRestorePending = false;
      pendingPauseSpindleStop = false;
      pauseHoldRequestedMs = 0;
      serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_RESET));
      spindleOverride = GRBL_OVERRIDE_DEFAULT;
      pauseResumeOriginalSpindleOverride = GRBL_OVERRIDE_DEFAULT;
      // Clear any leftover resume feed-ramp state so a new job never inherits it.
      isResumeFeedRampActive = false;
      resumeFeedRampStage = RESUME_FEED_IDLE;
      pauseResumeOriginalFeedOverride = GRBL_OVERRIDE_DEFAULT;
      resumeCompletionPending = false;
      isPauseFeedDropActive = false;

      if(dryRunEnabled) {
        // Global dry-run flag is set: home Z so the spindle is lifted safely, then
        // execute with motion-only sanitisation exactly like startDryRun.
        serialManager.CPUASerialPrintln(GRBL_HOMING_Z);
        ++waitForOk;
        // Defer feed-rate command until homing is acknowledged, then send it from handleJob().
        pendingDryRunFeedRate = true;
        isDryRunActive = true;
      } else {
        pendingDryRunFeedRate = false;
        isDryRunActive = false; //ensure dry run flag is always cleared for a normal job
      }

      // Seed the outbound gcode-position tracker with the machine's current work position so the
      // first height-map-compensated move interpolates at the REAL start point. Without this a stale
      // X/Y/Z (left from a previous job, or 0,0,0 at boot) makes the first Z-only/XY-only move apply
      // the wrong Z offset — an odd move in the air before the cut. Later lines update it normally.
      outboundCurrentWorkX = currentJobPositionX;
      outboundCurrentWorkY = currentJobPositionY;
      outboundCurrentWorkZ = currentJobPositionZ;
      outboundAbsoluteMode = true;   // grbl starts each job in G90; the file switches to G91 if needed
      outboundLastMotionMode = 0;    // no modal motion assumed until the first G0/G1/G2/G3

      isJobRunning = true;
      jobLineIndex = 0;
      jobStartMs = millis();
      if (heightMapCompensationEnabled)
        heightMapUsedForCurrentFile = true;

      // Job launched: log "<startTime>,<jobFileName>" to the hidden SD job log and
      // bump the lifetime job counter. startTime is client-provided (no onboard RTC).
      {
        const char* jobStartTime = receivedJsonBuffer[JSON_KEY_START_TIME_STR].isNull()
                                      ? ""
                                      : receivedJsonBuffer[JSON_KEY_START_TIME_STR].as<const char*>();
        logJobLaunch(jobStartTime);
        ++usageJobCount;
        saveMachineUsage(); // persist the counter immediately so a power loss does not lose it
      }

        if(jsonSettings.enableJsonResponse){
      
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;        
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
      
        }
      
    }else{

      if(jsonSettings.enableJsonResponse){
    
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_OPENED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
    
      }

    }

  break;

  case PAUSE_JOB:

  {

    /*
  if(!isJobRunning || !isMacroRunning){

    if(jsonSettings.enableJsonResponse){
    
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    
    }

    break;

  }

  if(isJobPaused){

    if(jsonSettings.enableJsonResponse){
    
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_PAUSED_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    
    }

    break;

  }*/
    

  pauseJobInternal(true);

  if(jsonSettings.enableJsonResponse){

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

  }

  }

  break;

  case RESUME_JOB:

  {

  /*
  if(!isJobRunning || !isMacroRunning){

    if(jsonSettings.enableJsonResponse){
    
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    
    }

    break;

  }

   if(!isJobPaused){

    if(jsonSettings.enableJsonResponse){
    
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;        
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_NOT_PAUSED_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    
    }

    break;

  }
    */
  
  // A resume IS a spindle start: grblHAL's parking restore restarts the spindle on '~' by
  // itself, which no M3 filtering on our side can intercept. So the same rule as startJob
  // applies — refuse while the housing is installed and the lid is open. A dry run has no
  // spindle to restore and stays resumable.
  if(isDoorInterlockActiveNow() && !isDryRunActive){

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DOOR_OPEN_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("Resume refused: lid open (housing installed)");

    break;

  }

  // Release grblHAL's virtual safety door before the cycle start — while it is asserted grblHAL
  // will (correctly) refuse to restore. The door check above already guarantees the lid is closed,
  // so this only forwards a state change the next loop pass would have sent anyway; doing it here
  // removes the one-loop window between closing the lid and pressing resume.
  handleVirtualDoorSignal();

  // ── RESUME offloaded to grblHAL (safety-door parking). ──
  // grblHAL is parked; a cycle-start ('~') restarts the spindle (with a spin-up dwell) and
  // plunges Z back to the resume position. The old CPUB resume (spindle recycle + feed ramp)
  // is disabled below.
  serialManager.CPUASerialWrite(GRBL_RESUME);

#if 0  // ─── OLD CPUB resume procedure (disabled; kept for revert) ───
  // A resume arriving mid pause-feed-drop (before the hold completed) cancels it.
  isPauseFeedDropActive = false;

  // If a deferred pause-stop never fired (resume pressed before GRBL
  // reached HOLD), the spindle was never toggled off via 0x9E, so we can
  // just resume motion immediately without any spindle restore.
  if (!pauseResumeSpindleRestorePending) {
    pendingPauseSpindleStop = false;
    pauseHoldRequestedMs = 0;
    serialManager.CPUASerialWrite(GRBL_RESUME);
  } else {
    // The spindle is currently stopped via 0x9E. Resume motion ('~') and restart
    // the spindle at full at the same instant, then "recycle" the override:
    // startResumeSpindleOverrideRamp paces it DOWN to the floor and back UP to the
    // captured target. GRBL-HAL applies overrides only out of HOLD, so the cycle
    // runs after '~' while the spindle is already spinning. '~' and 0x9E are
    // distinct realtime bytes, so sending them back-to-back is safe.
    pauseResumeSpindleRestorePending = false;
    pendingPauseSpindleStop = false;
    pauseHoldRequestedMs = 0;

    const int targetOverride = (pauseResumeOriginalSpindleOverride >= GRBL_OVERRIDE_MIN)
                                 ? pauseResumeOriginalSpindleOverride
                                 : GRBL_OVERRIDE_DEFAULT;

    serialManager.CPUASerialWrite(GRBL_RESUME);
    serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_STOP_TOGGLE));

    // The override was never altered during pause/resume, so the spindle restarts
    // at the captured target; keep the local counter consistent before the cycle.
    spindleOverride = targetOverride;

    const bool rampStarted = startResumeSpindleOverrideRamp(targetOverride);

    if (rampStarted) {
      // Motion + spindle are already resumed above; the cycle must NOT re-send '~'.
      resumeMotionAfterOverrideRampPending = false;
      // Remember who asked to resume so the deferred resumeCompleted event
      // (pushed when the ramp finishes) is routed back to that client.
      resumeInputSource = currentInputSource;
    }

    // Motion has resumed; protect the tool while the spindle spins back up by ramping the
    // FEED (motion) speed: drop it immediately, then ramp it back to the captured pre-pause
    // value. grblHAL ignores feed-override changes during HOLD, so this must run AFTER '~'.
    // resumeCompleted is deferred until BOTH this feed ramp and the spindle recycle finish.
    resumeCompletionPending = true;
    const int feedTarget = (pauseResumeOriginalFeedOverride >= GRBL_OVERRIDE_MIN)
                             ? pauseResumeOriginalFeedOverride
                             : GRBL_OVERRIDE_DEFAULT;
    startResumeFeedRamp(feedTarget);
  }
#endif  // ─── end OLD CPUB resume procedure ───

  isJobPaused = false;
  isResuming = true;  // grblHAL now ramps the spindle + descends Z; cleared when it reaches Run

  if(jsonSettings.enableJsonResponse){
      
    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;        
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();
      
  }

  }

  break;

  case STOP_JOB_RESET:

  stopJobWithReset();

  if(jsonSettings.enableJsonResponse){
      
    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;        
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();
      
  }

  break;

  case GRBL_SOFT_RESET_COMMAND:

  stopJobWithReset();

  if(jsonSettings.enableJsonResponse){

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

  }

  break;

  case GRBL_HOMING_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isRotaryAxisConnected){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    scheduleOrSendCPUA(GRBL_HOMING);
    isHoming = true;
    homingInputSource = currentInputSource;

  }

  break;

  case GRBL_HOMING_X_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isRotaryAxisConnected){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    scheduleOrSendCPUA(GRBL_HOMING_X);
    isHoming = true;
    homingInputSource = currentInputSource;

  }

  break;

  case GRBL_HOMING_Y_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isRotaryAxisConnected){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    scheduleOrSendCPUA(GRBL_HOMING_Y);
    isHoming = true;
    homingInputSource = currentInputSource;

  }

  break;

  case GRBL_HOMING_Z_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    scheduleOrSendCPUA(GRBL_HOMING_Z);
    isHoming = true;
    homingInputSource = currentInputSource;

  }

  break;

  case MOVE_X_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      char moveCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(moveCmd, sizeof(moveCmd), "X%.4f", receivedJsonBuffer[JSON_KEY_MOVE_X_STR].as<float>());

      bool accepted = scheduleWorkMove(
        moveCmd, receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_RELATIVE_STR] | false);

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = accepted ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
        if(!accepted)
          responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case MOVE_Y_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      char moveCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(moveCmd, sizeof(moveCmd), "Y%.4f", receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].as<float>());

      bool accepted = scheduleWorkMove(
        moveCmd, receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_RELATIVE_STR] | false);

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = accepted ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
        if(!accepted)
          responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case MOVE_Z_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      char moveCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(moveCmd, sizeof(moveCmd), "Z%.4f", receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].as<float>());

      bool accepted = scheduleWorkMove(
        moveCmd, receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_RELATIVE_STR] | false);

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = accepted ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
        if(!accepted)
          responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case MOVE_XYZ_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull() || receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull() || receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      char moveCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(moveCmd, sizeof(moveCmd), "X%.4f Y%.4f Z%.4f",
        receivedJsonBuffer[JSON_KEY_MOVE_X_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].as<float>());

      bool accepted = scheduleWorkMove(
        moveCmd, receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_RELATIVE_STR] | false);

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = accepted ? JSON_VALUE_RESULT_SUCCESS_STR : JSON_VALUE_RESULT_FAILURE_STR;
        if(!accepted)
          responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case MOVE_MACHINE_X_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    char feedCmd[MACRO_LINE_BUFFER_CHARS];
    char moveCmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(feedCmd, sizeof(feedCmd), "G1 F%.4f", receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>());
    snprintf(moveCmd, sizeof(moveCmd), "G90 G53 G1 X%.4f", receivedJsonBuffer[JSON_KEY_MOVE_X_STR].as<float>());

    if(!evaluateAndApplySoftLimitsForOutgoingLine(moveCmd)) {
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if(appendMacroPair(feedCmd, moveCmd)){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }else if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_BUFFER_FULL_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }

  break;

  case MOVE_MACHINE_Y_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    char feedCmd[MACRO_LINE_BUFFER_CHARS];
    char moveCmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(feedCmd, sizeof(feedCmd), "G1 F%.4f", receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>());
    snprintf(moveCmd, sizeof(moveCmd), "G90 G53 G1 Y%.4f", receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].as<float>());

    if(!evaluateAndApplySoftLimitsForOutgoingLine(moveCmd)) {
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if(appendMacroPair(feedCmd, moveCmd)){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }else if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_BUFFER_FULL_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }

  break;

  case MOVE_MACHINE_Z_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    char feedCmd[MACRO_LINE_BUFFER_CHARS];
    char moveCmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(feedCmd, sizeof(feedCmd), "G1 F%.4f", receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>());
    snprintf(moveCmd, sizeof(moveCmd), "G90 G53 G1 Z%.4f", receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].as<float>());

    if(!evaluateAndApplySoftLimitsForOutgoingLine(moveCmd)) {
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if(appendMacroPair(feedCmd, moveCmd)){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }else if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_BUFFER_FULL_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }

  break;

  case MOVE_MACHINE_XYZ_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull() || receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull() || receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    char feedCmd[MACRO_LINE_BUFFER_CHARS];
    char moveCmd[MACRO_LINE_BUFFER_CHARS];
    snprintf(feedCmd, sizeof(feedCmd), "G1 F%.4f", receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>());
    snprintf(moveCmd, sizeof(moveCmd), "G90 G53 G1 X%.4f Y%.4f Z%.4f",
      receivedJsonBuffer[JSON_KEY_MOVE_X_STR].as<float>(),
      receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].as<float>(),
      receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].as<float>());

    if(!evaluateAndApplySoftLimitsForOutgoingLine(moveCmd)) {
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    if(appendMacroPair(feedCmd, moveCmd)){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }else if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_BUFFER_FULL_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }

  break;

  case GRBL_UNLOCK_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    scheduleOrSendCPUA(GRBL_UNLOCK);

  }

  break;

  case MOVE_A_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_MOVE_A_STR].isNull() || receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      char moveCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(moveCmd, sizeof(moveCmd), "A%.4f", receivedJsonBuffer[JSON_KEY_MOVE_A_STR].as<float>());

      // The A axis has no soft limits to reject it (README 5.31), and this
      // command has always answered success; the result is not consulted.
      scheduleWorkMove(
        moveCmd, receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>(),
        receivedJsonBuffer[JSON_KEY_MOVE_RELATIVE_STR] | false);

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case JOG_COMMAND:

  if(isJobRunning || isJobPaused){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isMotionBlockedByDoor()){

    // Housing installed, lid open, and `allowMotionWhenDoorOpen` is false: refuse the jog up
    // front. Without this the jog would start and the door interlock would park the machine
    // ~50 ms later — a visible lurch, plus a pause latch the user then has to clear. Placed
    // before the `isJogging` keep-alive branch so a press held across a lid-open also starts
    // reporting the refusal instead of acking.
    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DOOR_OPEN_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].isNull()
        || (receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull()
            && receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull()
            && receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull())){

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isRotaryAxisConnected && !isRotaryYLockBypassed && !receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull()){

    // A Y jog is not allowed while the rotary axis is engaged, mirroring the move commands.
    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_ROTARY_Y_LOCK_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(!homedSincePowerOn){

    // Continuous jog drives toward the soft-limit using the live machine position, which is
    // only meaningful once the machine has been homed. Reject otherwise so we never jog from
    // an unreferenced position (which could send the move the wrong way or into a limit).
    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NOT_HOMED_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else if(isJogging){

    // A jog is already in motion for this press-and-hold. The app keeps streaming the same
    // jog message as a keep-alive while the button is held, but the actual motion was already
    // committed as a single long move on the first message (see the else branch below). So
    // here we only refresh the keep-alive watchdog and acknowledge — we do NOT send another
    // $J= line. (A direction change mid-hold without a jogCancel is ignored; the app sends
    // jogCancel on release, which ends the session and allows a fresh jog.)
    jogLastActivityMs = millis();

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

  }else{

    {
      // Press-and-hold start. Instead of streaming many short incremental jogs (which makes
      // GRBL decelerate to a stop between steps and stutter, especially at the start), we
      // commit ONE long jog that runs all the way to the soft-limit in the requested
      // direction(s). GRBL then accelerates a single time and holds feed -> smooth continuous
      // motion -> and the move is stopped instantly on button release by the jogCancel (0x85)
      // realtime byte. The app sends only the sign of each axis (e.g. x:+1 / x:-1); the
      // magnitude is ignored here and replaced by the distance remaining to the soft-limit,
      // computed from the live (homed) machine position. This keeps the jog within soft
      // limits without relying on grblHAL's optional jog-limit setting ($40), which is not
      // compiled into this controller.
      const float jogEpsilonMm = 0.01f; // ignore an axis that has no meaningful room left

      float jogX = 0.0f, jogY = 0.0f, jogZ = 0.0f;

      if(!receivedJsonBuffer[JSON_KEY_MOVE_X_STR].isNull()){
        float dir = receivedJsonBuffer[JSON_KEY_MOVE_X_STR].as<float>();
        if(dir > 0.0f)      jogX = SOFT_LIMIT_MAX_X_MM - currentMachinePositionX;
        else if(dir < 0.0f) jogX = SOFT_LIMIT_MIN_X_MM - currentMachinePositionX;
      }
      if(!receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].isNull()){
        float dir = receivedJsonBuffer[JSON_KEY_MOVE_Y_STR].as<float>();
        if(dir > 0.0f)      jogY = SOFT_LIMIT_MAX_Y_MM - currentMachinePositionY;
        else if(dir < 0.0f) jogY = SOFT_LIMIT_MIN_Y_MM - currentMachinePositionY;
      }
      if(!receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].isNull()){
        float dir = receivedJsonBuffer[JSON_KEY_MOVE_Z_STR].as<float>();
        if(dir > 0.0f)      jogZ = SOFT_LIMIT_MAX_Z_MM - currentMachinePositionZ;
        else if(dir < 0.0f) jogZ = SOFT_LIMIT_MIN_Z_MM - currentMachinePositionZ;
      }

      char jogCmd[MACRO_LINE_BUFFER_CHARS];
      int written = snprintf(jogCmd, sizeof(jogCmd), "$J=G91");
      bool hasMove = false;

      if(fabsf(jogX) >= jogEpsilonMm){
        written += snprintf(jogCmd + written, sizeof(jogCmd) - written, " X%.4f", jogX);
        hasMove = true;
      }
      if(fabsf(jogY) >= jogEpsilonMm){
        written += snprintf(jogCmd + written, sizeof(jogCmd) - written, " Y%.4f", jogY);
        hasMove = true;
      }
      if(fabsf(jogZ) >= jogEpsilonMm){
        written += snprintf(jogCmd + written, sizeof(jogCmd) - written, " Z%.4f", jogZ);
        hasMove = true;
      }

      // Only commit a jog if at least one requested axis still has room before its soft-limit;
      // if the machine is already parked against the limit there is simply nothing to move
      // (we still acknowledge success so the app's press is accepted).
      if(hasMove){
        snprintf(jogCmd + written, sizeof(jogCmd) - written, " F%.4f", receivedJsonBuffer[JSON_KEY_FEED_RATE_STR].as<float>());
        sendJogStep(jogCmd);
      }

      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
    }

  }

  break;

  case JOG_CANCEL_COMMAND:

  serialManager.CPUASerialWrite(GRBL_JOG_CANCEL); // 0x85 is a realtime command, no 'ok' response
  isJogging = false;

  if(jsonSettings.enableJsonResponse){
    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();
  }

  break;

  case SET_FEED_RATE_OVERRIDE:

  {
    if(receivedJsonBuffer[JSON_VALUE_OVERRIDE_ACTION_KEY_STR].isNull()){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    const char* action = receivedJsonBuffer[JSON_VALUE_OVERRIDE_ACTION_KEY_STR].as<const char*>();
    unsigned char overrideByte = 0;

    if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_RESET_STR) == 0){
      overrideByte = GRBL_OVERRIDE_FEED_RESET;
      feedRateOverride = GRBL_OVERRIDE_DEFAULT;
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_COARSE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_FEED_INCREASE_COARSE;
      feedRateOverride = min(GRBL_OVERRIDE_MAX, feedRateOverride + 10);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_COARSE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_FEED_DECREASE_COARSE;
      feedRateOverride = max(GRBL_OVERRIDE_MIN, feedRateOverride - 10);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_FINE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_FEED_INCREASE_FINE;
      feedRateOverride = min(GRBL_OVERRIDE_MAX, feedRateOverride + 1);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_FINE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_FEED_DECREASE_FINE;
      feedRateOverride = max(GRBL_OVERRIDE_MIN, feedRateOverride - 1);
    }else{
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    serialManager.CPUASerialWrite(overrideByte);

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_FEED_RATE_OVERRIDE_STR] = feedRateOverride;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }
  }

  break;

  case SET_SPINDLE_OVERRIDE:

  {
    if(receivedJsonBuffer[JSON_VALUE_OVERRIDE_ACTION_KEY_STR].isNull()){
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    const char* action = receivedJsonBuffer[JSON_VALUE_OVERRIDE_ACTION_KEY_STR].as<const char*>();
    unsigned char overrideByte = 0;

    // The reduction the spindle can still take before it drops below
    // SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM. A step past it is refused here, where the override byte
    // is forwarded, so no client can get around it: the byte is never sent, and GRBL therefore
    // never sees the reduction at all.
    const int spindleOverrideFloor = minimumSpindleOverridePercent();
    int steppedOverride = spindleOverride;

    if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_RESET_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_RESET;
      spindleOverride = GRBL_OVERRIDE_DEFAULT;
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_COARSE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_INCREASE_COARSE;
      spindleOverride = min(GRBL_OVERRIDE_MAX, spindleOverride + GRBL_OVERRIDE_COARSE_STEP);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_COARSE_STR) == 0){
      steppedOverride = spindleOverrideAfterStep(spindleOverride, -GRBL_OVERRIDE_COARSE_STEP, spindleOverrideFloor);
      if(steppedOverride < 0){
        sendSpindleOverrideFloorRefusal();
        break;
      }
      overrideByte = GRBL_OVERRIDE_SPINDLE_DECREASE_COARSE;
      spindleOverride = steppedOverride;
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_FINE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_INCREASE_FINE;
      spindleOverride = min(GRBL_OVERRIDE_MAX, spindleOverride + 1);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_FINE_STR) == 0){
      steppedOverride = spindleOverrideAfterStep(spindleOverride, -1, spindleOverrideFloor);
      if(steppedOverride < 0){
        sendSpindleOverrideFloorRefusal();
        break;
      }
      overrideByte = GRBL_OVERRIDE_SPINDLE_DECREASE_FINE;
      spindleOverride = steppedOverride;
    }else{
      if(jsonSettings.enableJsonResponse){
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }
      break;
    }

    serialManager.CPUASerialWrite(overrideByte);

    if(jsonSettings.enableJsonResponse){
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_SPINDLE_OVERRIDE_STR] = spindleOverride;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }
  }

  break;

  case PROBE_LASER_EDGES:

    if (isJobRunning || isJobPaused) {

      logLaserProbeStartFailure("job already running or paused");

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (isMacroRunning) {

      logLaserProbeStartFailure("macro already running");

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (!hasDistanceLaserSensorPresence) {

      logLaserProbeStartFailure("distance laser sensor not detected");

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (digitalRead(LASER_SENSOR_ENABLE_PIN) != HIGH) {

      logLaserProbeStartFailure("distance laser sensor enable pin is LOW");

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (!isDistanceLaserSensorInitialized) {

      logLaserProbeStartFailure("distance laser sensor not initialized");

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      float baselineMm = 0.0f;
      bool baselineOutOfRange = false;
      bool baselineOk = readDistanceLaserMm(&baselineMm, &baselineOutOfRange);

      if (!baselineOk && !baselineOutOfRange) {

        logLaserProbeStartFailure("distance laser baseline read failed");

        if (jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_READ_FAILED_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      isMacroRunning = true;
      isLaserEdgeProbingActive = true;
      laserEdgeProbeSuppressAlarmAbort = false;
      laserEdgeProbeWaitingForInitialUnlock = false;
      laserEdgeProbeInputSource = currentInputSource;
      laserEdgeProbeBaselineMm = baselineOk ? baselineMm : 0.0f;
      laserEdgeProbeStartX = currentJobPositionX;
      laserEdgeProbeStartY = currentJobPositionY;
      laserEdgeProbeStartMachineX = currentMachinePositionX;
      laserEdgeProbeStartMachineY = currentMachinePositionY;
      // Corner selector (default bottom-left) → the X/Y edge directions probed.
      // left = X-, right = X+, bottom = Y-, top = Y+ (natural Cartesian mapping).
      // Unknown / missing values fall through to the bottom-left default.
      const char* cornerStr = receivedJsonBuffer[JSON_KEY_XY0_CORNER_STR] | JSON_VALUE_XY0_CORNER_BOTTOM_LEFT_STR;
      LaserEdgeProbeDirState xDir = PROBE_DIR_XNEG;
      LaserEdgeProbeDirState yDir = PROBE_DIR_YNEG;
      if (strcmp(cornerStr, JSON_VALUE_XY0_CORNER_BOTTOM_RIGHT_STR) == 0) { xDir = PROBE_DIR_XPOS; yDir = PROBE_DIR_YNEG; }
      else if (strcmp(cornerStr, JSON_VALUE_XY0_CORNER_TOP_LEFT_STR) == 0)  { xDir = PROBE_DIR_XNEG; yDir = PROBE_DIR_YPOS; }
      else if (strcmp(cornerStr, JSON_VALUE_XY0_CORNER_TOP_RIGHT_STR) == 0) { xDir = PROBE_DIR_XPOS; yDir = PROBE_DIR_YPOS; }

      laserEdgeProbeFirstDir  = xDir;   // X phase probes this direction first
      laserEdgeProbeSecondDir = yDir;   // Y phase follows
      // Offset moved INTO the material along each axis (mm); default 0 if omitted.
      laserEdgeProbeXOffset = receivedJsonBuffer[JSON_KEY_XY0_X_OFFSET_STR] | 0.0f;
      laserEdgeProbeYOffset = receivedJsonBuffer[JSON_KEY_XY0_Y_OFFSET_STR] | 0.0f;

      laserEdgeProbeXNeg = 0.0f;
      laserEdgeProbeYNeg = 0.0f;
      laserEdgeProbeCurrentDir = laserEdgeProbeFirstDir;
      laserEdgeProbeState = LASER_EDGE_PROBE_COARSE_SCAN;
      laserEdgeProbeSubState = LASER_EDGE_SUB_SEND;
      laserEdgeProbeTraveled = 0.0f;
      laserEdgeProbeRoughEdge = 0.0f;
      laserEdgeProbeSetOriginStep = 0;

      if (currentMachineState == ALARM) {
        scheduleOrSendCPUA(GRBL_UNLOCK);
        laserEdgeProbeSuppressAlarmAbort = true;
        laserEdgeProbeWaitingForInitialUnlock = true;
      }

      if (debuggingSettings.debuggingMacroBuffer) {
        char dbg[120];
        snprintf(dbg, sizeof(dbg), "[LaserProbe] START work=(%.3f,%.3f) machine=(%.3f,%.3f) baseline=%.3f",
                 laserEdgeProbeStartX, laserEdgeProbeStartY,
                 laserEdgeProbeStartMachineX, laserEdgeProbeStartMachineY,
                 laserEdgeProbeBaselineMm);
        serialManager.USBSerialPrintln(dbg);
      }

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }

    break;

  default:

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_JSON_COMMAND_TYPE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    break;

  case PROBE_LASER_MATERIAL_BOUNDS: {

    if (isJobRunning || isJobPaused) {

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (isMacroRunning || isLaserEdgeProbingActive || isLaserMaterialBoundsProbingActive) {

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MACRO_ALREADY_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    if (!hasDistanceLaserSensorPresence || !isDistanceLaserSensorInitialized || digitalRead(LASER_SENSOR_ENABLE_PIN) != HIGH) {

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      float baselineMm = 0.0f;
      bool baselineOutOfRange = false;
      bool baselineOk = readDistanceLaserMm(&baselineMm, &baselineOutOfRange);

      if (!baselineOk || baselineOutOfRange) {

        if (jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_READ_FAILED_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      isMacroRunning = true;
      isLaserMaterialBoundsProbingActive = true;
      laserMaterialBoundsProbeSuppressAlarmAbort = false;
      laserMaterialBoundsProbeWaitingForInitialUnlock = false;
      laserMaterialBoundsProbeInputSource = currentInputSource;
      laserMaterialBoundsProbeBaselineMm = baselineOk ? baselineMm : 0.0f;
      laserMaterialBoundsProbeStartX = currentJobPositionX;
      laserMaterialBoundsProbeStartY = currentJobPositionY;
      laserMaterialBoundsProbeXMin = 0.0f;
      laserMaterialBoundsProbeXMax = 0.0f;
      laserMaterialBoundsProbeYMin = 0.0f;
      laserMaterialBoundsProbeYMax = 0.0f;
      laserMaterialBoundsProbeRoughEdge = 0.0f;
      laserMaterialBoundsProbeCoarseCurrentTarget = 0.0f;
      laserMaterialBoundsProbeTraveled = 0.0f;
      laserMaterialBoundsProbeCenterX = 0.0f;
      laserMaterialBoundsProbeCenterY = 0.0f;
      hasLastDetectedMaterialCenter = false;
      lastDetectedMaterialCenterX = 0.0f;
      lastDetectedMaterialCenterY = 0.0f;
      laserMaterialBoundsProbeCurrentDir = PROBE_DIR_XNEG;
      laserMaterialBoundsProbeState = LASER_MATERIAL_BOUNDS_PROBE_COARSE_SCAN;
      laserMaterialBoundsProbeSubState = LASER_EDGE_SUB_SEND;
      laserMaterialBoundsProbeBaselineSampleSum = 0.0f;
      laserMaterialBoundsProbeBaselineSampleCount = 0;
      laserMaterialBoundsProbeReturnMotionObserved = false;
      laserMaterialBoundsProbeRecoveryNeedsReset = false;
      laserMaterialBoundsProbeAbortStage = nullptr;
      laserMaterialBoundsProbeAbortDetail = nullptr;

      if (currentMachineState == ALARM) {
        scheduleOrSendCPUA(GRBL_UNLOCK);
        laserMaterialBoundsProbeSuppressAlarmAbort = true;
        laserMaterialBoundsProbeWaitingForInitialUnlock = true;
      }

      if (debuggingSettings.debuggingMacroBuffer) {
        char dbg[128];
        snprintf(dbg, sizeof(dbg), "[LaserBounds] START work=(%.3f,%.3f) baseline=%.3f",
                 laserMaterialBoundsProbeStartX,
                 laserMaterialBoundsProbeStartY,
                 laserMaterialBoundsProbeBaselineMm);
        serialManager.USBSerialPrintln(dbg);
      }

      if (jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }

    break;
  }
  }

}

void handleJsonSet(){

  JsonValueSetType jsonType = jsonUtil.getJsonSetType(receivedJsonBuffer);

  switch (jsonType)
  {
    case SET_JOB_X:{

    if(receivedJsonBuffer[JSON_VALUE_JOB_POSITION_X_STR].isNull()){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    float tempJobX = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_X_STR]|GRBL_NULL_POSITION ;

    if(tempJobX == GRBL_NULL_POSITION){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    if(isJobRunning){

        if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
        }

        break;

      }

    if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
    }

    {
      char setJobCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(setJobCmd, sizeof(setJobCmd), "%s%.4f", GRBL_SET_JOB_X, tempJobX);
      scheduleOrSendCPUA(setJobCmd);
    }
      
  break;
  }

  case SET_JOB_Y:{

    if(receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Y_STR].isNull()){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    float tempJobY = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Y_STR]|GRBL_NULL_POSITION ;

    if(tempJobY == GRBL_NULL_POSITION){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    if(isJobRunning){

        if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
        }

        break;

      }

    if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
    }

    {
      char setJobCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(setJobCmd, sizeof(setJobCmd), "%s%.4f", GRBL_SET_JOB_Y, tempJobY);
      scheduleOrSendCPUA(setJobCmd);
    }
      
  break;
  }

  case SET_JOB_Z:{

    if(receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Z_STR].isNull()){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    float tempJobZ = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Z_STR]|GRBL_NULL_POSITION ;

    if(tempJobZ == GRBL_NULL_POSITION){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    if(isJobRunning){

        if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
        }

        break;

      }

    if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
    }

    {
      char setJobCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(setJobCmd, sizeof(setJobCmd), "%s%.4f", GRBL_SET_JOB_Z, tempJobZ);
      scheduleOrSendCPUA(setJobCmd);
    }
      
  break;
  }

  case SET_JOB_XYZ:{

        if(receivedJsonBuffer[JSON_VALUE_JOB_POSITION_X_STR].isNull()
        || receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Y_STR].isNull()
        || receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Z_STR].isNull()
        ){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }
    float tempJobX = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_X_STR]|GRBL_NULL_POSITION ;
    float tempJobY = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Y_STR]|GRBL_NULL_POSITION ;
    float tempJobZ = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_Z_STR]|GRBL_NULL_POSITION ;

    if(tempJobX == GRBL_NULL_POSITION 
      || tempJobY == GRBL_NULL_POSITION 
      || tempJobZ == GRBL_NULL_POSITION){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    if(isJobRunning){

        if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
        }

        break;

      }
    
    if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
    }

    {
      char setJobCmdX[MACRO_LINE_BUFFER_CHARS];
      char setJobCmdY[MACRO_LINE_BUFFER_CHARS];
      char setJobCmdZ[MACRO_LINE_BUFFER_CHARS];
      snprintf(setJobCmdX, sizeof(setJobCmdX), "%s%.4f", GRBL_SET_JOB_X, tempJobX);
      snprintf(setJobCmdY, sizeof(setJobCmdY), "%s%.4f", GRBL_SET_JOB_Y, tempJobY);
      snprintf(setJobCmdZ, sizeof(setJobCmdZ), "%s%.4f", GRBL_SET_JOB_Z, tempJobZ);
      scheduleOrSendCPUA(setJobCmdX);
      scheduleOrSendCPUA(setJobCmdY);
      scheduleOrSendCPUA(setJobCmdZ);
    }
      
  break;
  }

  case SET_JOB_A:{

    if(receivedJsonBuffer[JSON_VALUE_JOB_POSITION_A_STR].isNull()){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    float tempJobA = receivedJsonBuffer[JSON_VALUE_JOB_POSITION_A_STR]|GRBL_NULL_POSITION ;

    if(tempJobA == GRBL_NULL_POSITION){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
      }

      break;

    }

    if(isJobRunning){

        if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
        }

        break;

      }

    // Unlike SET_JOB_X/Y/Z, the command is issued before the response so a rejected line is
    // reported as a failure instead of a success the machine never acted on. A itself is not
    // soft-limit checked (the outgoing-line checker only parses X/Y/Z), so in practice this
    // only guards against the rotary Y-lock and any future outgoing-line rule.
    {
      char setJobCmd[MACRO_LINE_BUFFER_CHARS];
      snprintf(setJobCmd, sizeof(setJobCmd), "%s%.4f", GRBL_SET_JOB_A, tempJobA);

      if(!scheduleOrSendCPUA(setJobCmd)){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = getOutgoingLineRejectErrorJsonValue();
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }
    }

    if(jsonSettings.enableJsonResponse){
    
          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();
    
    }
      
  break;
  }

    
    case SET_DRY_RUN:

      if(!receivedJsonBuffer[JSON_KEY_DRY_RUN_ENABLED_STR].isNull()){

        dryRunEnabled = receivedJsonBuffer[JSON_KEY_DRY_RUN_ENABLED_STR].as<bool>();

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          responseJsonBuffer[JSON_KEY_DRY_RUN_ENABLED_STR] = dryRunEnabled;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }else{

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }

    break;


    case SET_WIFI_SETTINGS:

    if(isJobRunning || isJobPaused){

      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
  
      }

      break;

    }

    if(!receivedJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR].isNull() && !receivedJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR].isNull()
    && !receivedJsonBuffer[JSON_KEY_CLIENT_WIFI_SSID_STR].isNull() && !receivedJsonBuffer[JSON_KEY_CLIENT_WIFI_PASSWORD_STR].isNull()){
      
        arrayUtil.clearCharArray(wifiSettings.apSsid, ACCESS_POINT_SSID_CHARS);
        arrayUtil.clearCharArray(wifiSettings.apPassword, ACCESS_POINT_PASSWORD_CHARS);
        arrayUtil.clearCharArray(wifiSettings.clientSsid, ACCESS_POINT_SSID_CHARS);
        arrayUtil.clearCharArray(wifiSettings.clientPassword, ACCESS_POINT_PASSWORD_CHARS);

        strcat(wifiSettings.apSsid, receivedJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR].as<const char*>());
        strcat(wifiSettings.apPassword, receivedJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR].as<const char*>());
        strcat(wifiSettings.clientSsid, receivedJsonBuffer[JSON_KEY_CLIENT_WIFI_SSID_STR].as<const char*>());
        strcat(wifiSettings.clientPassword, receivedJsonBuffer[JSON_KEY_CLIENT_WIFI_PASSWORD_STR].as<const char*>());

        eepromManager.saveWiFiSettings(wifiSettings);
  
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      //here we apply the new settings by resetting the ESP32C3 initial configuration
      resetESP32InitialConfiguration();

    }else{

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

    }

    break;

    case SET_DEBUGGING_BASE:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_BASE_STR].isNull()){

        debuggingSettings.debuggingBase = receivedJsonBuffer[JSON_KEY_DEBUGGING_BASE_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_DEBUGGING_SPINDLE:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_SPINDLE_STR].isNull()){

        debuggingSettings.debuggingSpindle = receivedJsonBuffer[JSON_KEY_DEBUGGING_SPINDLE_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }


    break;

    case SET_DEBUGGING_STATUS:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_STATUS_STR].isNull()){

        debuggingSettings.debuggingStatus = receivedJsonBuffer[JSON_KEY_DEBUGGING_STATUS_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_DEBUGGING_MACRO_BUFFER:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_MACRO_BUFFER_STR].isNull()){

        debuggingSettings.debuggingMacroBuffer = receivedJsonBuffer[JSON_KEY_DEBUGGING_MACRO_BUFFER_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_DEBUGGING_SD_CARD:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_SD_CARD_STR].isNull()){

        debuggingSettings.debuggingSDCard = receivedJsonBuffer[JSON_KEY_DEBUGGING_SD_CARD_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_DEBUGGING_JSON:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_JSON_STR].isNull()){

        debuggingSettings.debuggingJson = receivedJsonBuffer[JSON_KEY_DEBUGGING_JSON_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

      break;

      case SET_DEBUGGING_ESP32:

      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_ESP32_STR].isNull()){

        debuggingSettings.debuggingESP32 = receivedJsonBuffer[JSON_KEY_DEBUGGING_ESP32_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_DEBUGGING_EEPROM:
    
      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_EEPROM_STR].isNull()){

        debuggingSettings.debuggingEEPROM = receivedJsonBuffer[JSON_KEY_DEBUGGING_EEPROM_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

      case SET_DEBUGGING_JOB:
    
      if(!receivedJsonBuffer[JSON_KEY_DEBUGGING_JOB_STR].isNull()){

        debuggingSettings.debuggingJob = receivedJsonBuffer[JSON_KEY_DEBUGGING_JOB_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_BENCHMARKING:

      if(!receivedJsonBuffer[JSON_KEY_BENCHMARKING_STR].isNull()){

        debuggingSettings.benchmarking = receivedJsonBuffer[JSON_KEY_BENCHMARKING_STR].as<bool>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_BENCHMARK_MINIMUM_MS:

      if(!receivedJsonBuffer[JSON_KEY_BENCHMARK_MINIMUM_MS_STR].isNull()){

        debuggingSettings.benchmarkMinimumMs = receivedJsonBuffer[JSON_KEY_BENCHMARK_MINIMUM_MS_STR].as<int>();
        eepromManager.saveDebuggingSettings(debuggingSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      
      }else{
      
        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

      break;

    case SET_JSON_SETTINGS:

      if(!receivedJsonBuffer[JSON_KEY_JSON_RESPONSE_STR].isNull()){

        jsonSettings.enableJsonResponse = receivedJsonBuffer[JSON_KEY_JSON_RESPONSE_STR].as<bool>();
        eepromManager.saveJsonSettings(jsonSettings);

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }else{

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    break;

    case SET_MACHINE_NAME:

      if(receivedJsonBuffer[JSON_KEY_MACHINE_NAME_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      {
        const char* requestedMachineName = receivedJsonBuffer[JSON_KEY_MACHINE_NAME_STR].as<const char*>();

        if(requestedMachineName == nullptr || strlen(requestedMachineName) == 0 || strlen(requestedMachineName) > MACHINE_NAME_MAX_CHARS){

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;

        }

        arrayUtil.clearCharArray(machineNameSettings.machineName, MACHINE_NAME_CHARS);
        strncpy(machineNameSettings.machineName, requestedMachineName, MACHINE_NAME_MAX_CHARS);
        machineNameSettings.machineName[MACHINE_NAME_MAX_CHARS] = NULL_CHAR;
        machineNameSettings.checkChar = EEPROM_CHECK_CHAR;
        eepromManager.saveMachineNameSettings(machineNameSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          responseJsonBuffer[JSON_KEY_MACHINE_NAME_STR] = machineNameSettings.machineName;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }

    break;

    case SET_HOTSPOT_SETTINGS:

      if(isJobRunning || isJobPaused){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_JOB_RUNNING_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(!receivedJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR].isNull() && !receivedJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR].isNull()){

        {
          const char* requestedApSsid = receivedJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR].as<const char*>();
          const char* requestedApPassword = receivedJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR].as<const char*>();

          if(requestedApSsid == nullptr || strlen(requestedApSsid) == 0 || strlen(requestedApSsid) > ACCESS_POINT_SSID_CHARS - 1
          || requestedApPassword == nullptr || strlen(requestedApPassword) == 0 || strlen(requestedApPassword) > ACCESS_POINT_PASSWORD_CHARS - 1){

            if(jsonSettings.enableJsonResponse){

              clearResponseJsonBuffer();
              responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
              responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
              serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
              sendJsonResponse();

            }

            break;

          }

          arrayUtil.clearCharArray(wifiSettings.apSsid, ACCESS_POINT_SSID_CHARS);
          arrayUtil.clearCharArray(wifiSettings.apPassword, ACCESS_POINT_PASSWORD_CHARS);

          strncpy(wifiSettings.apSsid, requestedApSsid, ACCESS_POINT_SSID_CHARS - 1);
          wifiSettings.apSsid[ACCESS_POINT_SSID_CHARS - 1] = NULL_CHAR;
          strncpy(wifiSettings.apPassword, requestedApPassword, ACCESS_POINT_PASSWORD_CHARS - 1);
          wifiSettings.apPassword[ACCESS_POINT_PASSWORD_CHARS - 1] = NULL_CHAR;

        }

      }

      // Update hotspotEnabled if the key is present (may be sent alone or alongside SSID/password)
      if(!receivedJsonBuffer[JSON_KEY_HOTSPOT_ENABLED_STR].isNull()){

        wifiSettings.hotspotEnabled = receivedJsonBuffer[JSON_KEY_HOTSPOT_ENABLED_STR].as<bool>();

      }

      // At least one field must have been provided
      if(receivedJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR].isNull() && receivedJsonBuffer[JSON_KEY_HOTSPOT_ENABLED_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      wifiSettings.checkChar = EEPROM_CHECK_CHAR;
      eepromManager.saveWiFiSettings(wifiSettings);

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_RESPONSE_KEY_AP_WIFI_SSID_STR] = wifiSettings.apSsid;
        responseJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR] = wifiSettings.apPassword;
        responseJsonBuffer[JSON_KEY_HOTSPOT_ENABLED_STR] = wifiSettings.hotspotEnabled;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      //apply the new hotspot settings to the ESP32C3 on the fly
      resetESP32InitialConfiguration();

    break;

    case SET_TOOL_LENGTH_REFERENCES:

      if(receivedJsonBuffer[JSON_KEY_TOOL_LENGTH_REFERENCE_Z_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      toolLengthReferenceSettings.refZ      = receivedJsonBuffer[JSON_KEY_TOOL_LENGTH_REFERENCE_Z_STR].as<float>();
      toolLengthReferenceSettings.checkChar = EEPROM_CHECK_CHAR;
      eepromManager.saveToolLengthReferenceSettings(toolLengthReferenceSettings);

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_TOOL_LENGTH_REFERENCE_Z_STR] = toolLengthReferenceSettings.refZ;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    break;

    case SET_PROBE_HEIGHT:

      if(receivedJsonBuffer[JSON_KEY_PROBE_HEIGHT_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      {
        const float requestedProbeHeightMm = receivedJsonBuffer[JSON_KEY_PROBE_HEIGHT_STR].as<float>();

        // The offset is a physical touch-plate/reference thickness: it must be positive.
        if(requestedProbeHeightMm <= 0.0f){

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;

        }

        probeHeightSettings.heightMm  = requestedProbeHeightMm;
        probeHeightSettings.checkChar = EEPROM_CHECK_CHAR;
        eepromManager.saveProbeHeightSettings(probeHeightSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          responseJsonBuffer[JSON_KEY_PROBE_HEIGHT_STR] = probeHeightSettings.heightMm;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }
      }

    break;

    case SET_LASER_OFFSET_ZERO_SETTINGS:

      if(receivedJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_X_BACK_MM_STR].isNull() || receivedJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_Y_BACK_MM_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      {
        float requestedXBackMm = receivedJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_X_BACK_MM_STR].as<float>();
        float requestedYBackMm = receivedJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_Y_BACK_MM_STR].as<float>();

        if(requestedXBackMm < 0.0f || requestedYBackMm < 0.0f){

          if(jsonSettings.enableJsonResponse){

            clearResponseJsonBuffer();
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
            serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
            sendJsonResponse();

          }

          break;

        }

        laserOffsetZeroSettings.xBackMm = requestedXBackMm;
        laserOffsetZeroSettings.yBackMm = requestedYBackMm;
        laserOffsetZeroSettings.checkChar = EEPROM_CHECK_CHAR;
        eepromManager.saveLaserOffsetZeroSettings(laserOffsetZeroSettings);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          responseJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_X_BACK_MM_STR] = laserOffsetZeroSettings.xBackMm;
          responseJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_Y_BACK_MM_STR] = laserOffsetZeroSettings.yBackMm;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

      }

    break;

    case SET_BRUSHLESS_MOTOR_SETTINGS:

      if(receivedJsonBuffer[JSON_KEY_BRUSHLESS_MOTOR_DIRECTION_REVERSED_STR].isNull()){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      brushlessMotorSettings.directionReversed =
        receivedJsonBuffer[JSON_KEY_BRUSHLESS_MOTOR_DIRECTION_REVERSED_STR].as<bool>();
      brushlessMotorSettings.checkChar = EEPROM_CHECK_CHAR;
      eepromManager.saveBrushlessMotorSettings(brushlessMotorSettings);
      // Applied immediately: the accessory reads a static level, so the new direction takes effect
      // on its next run without a reboot.
      applyBrushlessMotorDirection();

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_BRUSHLESS_MOTOR_DIRECTION_REVERSED_STR] = brushlessMotorSettings.directionReversed;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    break;

    case SET_HOUSING_SETTINGS:
    {
      // Both fields are optional; update whichever is provided. Fail only if neither is present.
      bool hasInstalled   = !receivedJsonBuffer[JSON_KEY_HOUSING_INSTALLED_STR].isNull();
      bool hasAllowMotion = !receivedJsonBuffer[JSON_KEY_ALLOW_MOTION_WHEN_DOOR_OPEN_STR].isNull();

      if(!hasInstalled && !hasAllowMotion){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      // A value was provided via API: persist to EEPROM so we stop relying on the compile-time default.
      if(hasInstalled)
        housingSettings.housingInstalled = receivedJsonBuffer[JSON_KEY_HOUSING_INSTALLED_STR].as<bool>();
      if(hasAllowMotion)
        housingSettings.allowMotionWhenDoorOpen = receivedJsonBuffer[JSON_KEY_ALLOW_MOTION_WHEN_DOOR_OPEN_STR].as<bool>();
      housingSettings.checkChar = EEPROM_CHECK_CHAR;
      eepromManager.saveHousingSettings(housingSettings);

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_HOUSING_INSTALLED_STR] = housingSettings.housingInstalled;
        responseJsonBuffer[JSON_KEY_ALLOW_MOTION_WHEN_DOOR_OPEN_STR] = housingSettings.allowMotionWhenDoorOpen;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }
    }
    break;

    default:

    if(jsonSettings.enableJsonResponse){
    
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_JSON_TYPE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

    }
    
    break;

  }

}

void handleJsonGet(){

  JsonValueGetType getType = jsonUtil.getJsonGetType(receivedJsonBuffer);

  switch (getType)
  {
  case STATUS:

  if(jsonSettings.enableJsonResponse){

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    responseJsonBuffer[JSON_KEY_MACHINE_STATUS_STR] = grblUtil.getMachineStateString(getReportedMachineState());
    responseJsonBuffer[JSON_KEY_PROBE_TRIGGERED_STR] = currentProbeTriggered;
    responseJsonBuffer[JSON_KEY_LASER_SENSOR_ENABLED_STR] = digitalRead(LASER_SENSOR_ENABLE_PIN) == HIGH;
    responseJsonBuffer[JSON_KEY_DISTANCE_LASER_SENSOR_CONNECTED_STR] = hasDistanceLaserSensorPresence;
    // SD card initialised at boot (it is not retried later, so false means "power cycle with a card in").
    responseJsonBuffer[JSON_KEY_SD_CARD_CONNECTED_STR] = sdCardManager.connected;
    responseJsonBuffer[JSON_KEY_ROTARY_AXIS_CONNECTED_STR] = isRotaryAxisConnected;
    responseJsonBuffer[JSON_KEY_HOMED_SINCE_POWER_ON_STR] = homedSincePowerOn;
    // Live door/lid sensor state (raw pin, true = open). Meaningful when the housing is installed.
    responseJsonBuffer[JSON_KEY_DOOR_OPEN_STR] = isDoorOpen();

    bool isFileOpened = openedReadFileName[0] != NULL_CHAR;
    responseJsonBuffer[JSON_KEY_FILE_OPENED_STR] = isFileOpened;
    responseJsonBuffer[JSON_KEY_JOB_RUNNING_STR] = isJobRunning;
    responseJsonBuffer[JSON_KEY_DRY_RUN_ENABLED_STR] = dryRunEnabled;
    responseJsonBuffer[JSON_KEY_DRY_RUN_ACTIVE_STR] = isDryRunActive;
    responseJsonBuffer[JSON_KEY_PROCEDURE_BUSY_STR] = isProcedureBusy();
    responseJsonBuffer[JSON_KEY_JOB_PAUSED_STR] = isJobPaused;
    // True from a resumeJob request until the machine leaves Hold (reaches Run) — lets the app
    // show a transient "Resuming" state during grblHAL's spindle soft-start ramp + Z descent.
    responseJsonBuffer[JSON_KEY_JOB_RESUMING_STR] = isResuming;
    responseJsonBuffer[JSON_KEY_FILE_TRANSFER_IN_PROGRESS_STR] = isFileTransferInProgress;
    responseJsonBuffer[JSON_KEY_FILE_DOWNLOAD_IN_PROGRESS_STR] = isFileDownloadInProgress;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_CONFIGURED_STR] = heightMapConfigured;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SCAN_IN_PROGRESS_STR] = heightMapScanInProgress;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_READY_STR] = heightMapReady;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COMPENSATION_ENABLED_STR] = heightMapCompensationEnabled;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DENSITY_STR] =
      heightMapFineDensity ? JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR : JSON_VALUE_HEIGHT_MAP_DENSITY_COARSE_STR;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ACTIVE_FOR_OPENED_FILE_STR] =
      (openedReadFileName[0] != NULL_CHAR)
      && heightMapReady
      && !heightMapUsedForCurrentFile
      && (strcmp(openedReadFileName, heightMapBoundFile) == 0);

    if(isFileOpened)
      responseJsonBuffer[JSON_KEY_OPENED_FILE_NAME_STR] = openedReadFileName;

    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

  }

    break;

  case POSITIONS:

  if(jsonSettings.enableJsonResponse){

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    responseJsonBuffer[JSON_KEY_MACHINE_POSITIONX_STR] = currentMachinePositionX;
    responseJsonBuffer[JSON_KEY_MACHINE_POSITIONY_STR] = currentMachinePositionY;
    responseJsonBuffer[JSON_KEY_MACHINE_POSITIONZ_STR] = currentMachinePositionZ;
    responseJsonBuffer[JSON_KEY_MACHINE_POSITIONA_STR] = currentMachinePositionA;
    responseJsonBuffer[JSON_KEY_JOB_POSITIONX_STR] = currentJobPositionX;
    responseJsonBuffer[JSON_KEY_JOB_POSITIONY_STR] = currentJobPositionY;
    responseJsonBuffer[JSON_KEY_JOB_POSITIONZ_STR] = currentJobPositionZ;
    responseJsonBuffer[JSON_KEY_JOB_POSITIONA_STR] = currentJobPositionA;
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

  }

    break;

  case JOB_PROGRESS:

    if(openedReadFileName[0] != NULL_CHAR){

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_CURRENT_FILE_LINE_STR] = jobLineIndex;

        float progress = getOpenedFileJobProgressPercent();
        responseJsonBuffer[JSON_KEY_JOB_PROGRESS_STR] = progress;

        // Estimated remaining seconds based on elapsed time and current progress
        if (isJobRunning && jobStartMs > 0 && progress > 0.0f) {
          unsigned long elapsedMs = millis() - jobStartMs;
          float elapsedSeconds = elapsedMs / 1000.0f;
          float estimatedRemainingSeconds = elapsedSeconds / progress * (100.0f - progress);
          responseJsonBuffer[JSON_KEY_ESTIMATED_REMAINING_SECONDS_STR] = (unsigned long)estimatedRemainingSeconds;
        } else {
          responseJsonBuffer[JSON_KEY_ESTIMATED_REMAINING_SECONDS_STR] = (unsigned long)0;
        }

        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    } else {

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_OPENED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }

    break;

  case MACHINE_POSITION_X:

  if(jsonSettings.enableJsonResponse){

    clearResponseJsonBuffer();
    responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
    responseJsonBuffer[JSON_KEY_MACHINE_POSITIONX_STR] = currentMachinePositionX;
    serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
    sendJsonResponse();

  }

    break;
  
  case MACHINE_POSITION_Y:


    if(jsonSettings.enableJsonResponse)   {
    
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONY_STR] = currentMachinePositionY;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case MACHINE_POSITION_Z:
  

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONZ_STR] = currentMachinePositionZ;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
      
    }
    
    break;  

  case MACHINE_POSITION_A:


    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONA_STR] = currentMachinePositionA;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;
  
  case MACHINE_POSITION_XYZ:


    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONX_STR] = currentMachinePositionX;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONY_STR] = currentMachinePositionY;
      responseJsonBuffer[JSON_KEY_MACHINE_POSITIONZ_STR] = currentMachinePositionZ;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

  break;

  case JOB_POSITION_X:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONX_STR] = currentJobPositionX;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
    
    break;

  case JOB_POSITION_Y:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONY_STR] = currentJobPositionY;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case JOB_POSITION_Z:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONZ_STR] = currentJobPositionZ;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case JOB_POSITION_A:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONA_STR] = currentJobPositionA;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case JOB_POSITION_XYZ:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONX_STR] = currentJobPositionX;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONY_STR] = currentJobPositionY;
      responseJsonBuffer[JSON_KEY_JOB_POSITIONZ_STR] = currentJobPositionZ;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

     break;

  case GET_DEVICE_ID:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_DEVICE_ID_STR] = boardDeviceId;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case FILE_LIST:
    
    char fileList[SD_MAX_FILES][SD_MAX_FILE_NAME_LENGTH];
    long fileSizes[SD_MAX_FILES];
    char fileDates[SD_MAX_FILES][20];
    
    int fileAmount;

    fileAmount = sdCardManager.listFilesWithSizeAndDate(NC_FILE_EXTENSION, fileList, fileSizes, fileDates, SD_MAX_FILES);
    
    /*
    if(debuggingSettings.debuggingSDCard){
    
      serialManager.USBSerialPrintln("Number of files:");
      serialManager.USBSerialPrintln(fileAmount);
    
    }*/

    if(jsonSettings.enableJsonResponse){

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;

      JsonArray fileListJsonArray = responseJsonBuffer[JSON_KEY_FILE_LIST_STR].to<JsonArray>();

      for (int i = 0; i < fileAmount; i++)
      {
        
        if (fileList[i][0] != NULL_CHAR) {
          float fileDepth = 0.0f;
          float fileWidth = 0.0f;
          float fileHeight = 0.0f;
          unsigned long estimatedDurationSeconds = 0;
          char metadataLine[256];

          if (sdCardManager.readFirstLine(fileList[i], metadataLine, sizeof(metadataLine)))
            // parseMetadataLine fills its args in SIZEX, SIZEY, SIZEZ order. Width is the X
            // extent and depth is the Y extent, so SIZEX -> width and SIZEY -> depth.
            GCodeBoundsUtil::parseMetadataLine(metadataLine, &fileWidth, &fileDepth, &fileHeight, &estimatedDurationSeconds);

          JsonObject fileObj = fileListJsonArray.add<JsonObject>();
          fileObj[JSON_KEY_FILE_NAME_STR] = fileList[i];
          fileObj[JSON_KEY_FILE_SIZE_STR] = fileSizes[i];
          fileObj[JSON_KEY_FILE_DATE_STR] = fileDates[i];
          fileObj[JSON_KEY_FILE_DEPTH_STR] = roundf(fileDepth * 1000.0f) / 1000.0f;
          fileObj[JSON_KEY_FILE_WIDTH_STR] = roundf(fileWidth * 1000.0f) / 1000.0f;
          fileObj[JSON_KEY_FILE_HEIGHT_STR] = roundf(fileHeight * 1000.0f) / 1000.0f;
          fileObj[JSON_KEY_ESTIMATED_DURATION_SECONDS_STR] = estimatedDurationSeconds;
        } else 
          break; // No more files in the list
        
      }

      int plyFileAmount = sdCardManager.listFilesWithSizeAndDate(PLY_FILE_EXTENSION, fileList, fileSizes, fileDates, SD_MAX_FILES);
      for (int i = 0; i < plyFileAmount; i++) {
        if (fileList[i][0] != NULL_CHAR) {
          JsonObject fileObj = fileListJsonArray.add<JsonObject>();
          fileObj[JSON_KEY_FILE_NAME_STR] = fileList[i];
          fileObj[JSON_KEY_FILE_SIZE_STR] = fileSizes[i];
          fileObj[JSON_KEY_FILE_DATE_STR] = fileDates[i];
          fileObj[JSON_KEY_FILE_DEPTH_STR] = 0.0f;
          fileObj[JSON_KEY_FILE_WIDTH_STR] = 0.0f;
          fileObj[JSON_KEY_FILE_HEIGHT_STR] = 0.0f;
          fileObj[JSON_KEY_ESTIMATED_DURATION_SECONDS_STR] = (unsigned long)0;
        } else
          break;
      }

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }
    
  break;

  case GCODE_FILE_HASH:

    if(receivedJsonBuffer[JSON_KEY_FILENAME_STR].isNull()) {

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      const char* filename = receivedJsonBuffer[JSON_KEY_FILENAME_STR].as<const char*>();

      if(!sdCardManager.isNcFilename(filename)) {

        if(jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(!sdCardManager.fileExists(filename)) {

        if(jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      char hashFilename[SD_MAX_FILE_NAME_LENGTH];
      char fileHashHex[(SHA256_RESULT_BYTES * 2) + 1];
      arrayUtil.clearCharArray(hashFilename, sizeof(hashFilename));
      arrayUtil.clearCharArray(fileHashHex, sizeof(fileHashHex));

      bool hasHashSidecar = buildHashSidecarFilename(filename, hashFilename, sizeof(hashFilename))
        && sdCardManager.fileExists(hashFilename)
        && sdCardManager.readFirstLine(hashFilename, fileHashHex, sizeof(fileHashHex));

      if(!hasHashSidecar) {

        if(jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_HASH_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;

      }

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_FILE_NAME_STR] = filename;
        responseJsonBuffer[JSON_KEY_FILE_SHA256_STR] = fileHashHex;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }
    }

  break;

  case OPENED_GCODE_FILE_NAME:
    
    if(openedReadFileName[0] != NULL_CHAR){
      
      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_OPENED_FILE_NAME_STR] = openedReadFileName;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
        
      }

    } else {
      
      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }

    break;

  case OPENED_GCODE_FILE_TOTAL_LINES:

    if(openedReadFileName[0] != NULL_CHAR){

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_TOTAL_FILE_LINES_STR] = openedReadFileTotalLines;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    } else {

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_OPENED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }

    break;

  case NEXT_FILE_DOWNLOAD_CHUNK:

    if(!isFileDownloadInProgress){

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_NO_FILE_DOWNLOAD_IN_PROGRESS_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

      break;

    }

    {
      unsigned long chunkLinesRead = 0;
      bool eof = false;
      fileDownloadLastChunkActivityMs = millis();

      // Remember where this chunk starts so a failed send can rewind the file and
      // let the client re-request the exact same chunk on a later call.
      long chunkStartPos = sdCardManager.getDownloadFilePosition();

      // Retry the SD read on transient glitches. readDownloadChunk only returns
      // false before appending any line (linesRead stays 0 and the file position
      // is not advanced), so re-reading from the same position is safe and never
      // duplicates or skips data.
      bool readOk = false;
      for(int attempt = 0; attempt < DOWNLOAD_CHUNK_READ_MAX_ATTEMPTS; ++attempt) {
        chunkLinesRead = 0;
        eof = false;
        arrayUtil.clearCharArray(downloadFileChunkBuffer, DOWNLOAD_FILE_CHUNK_BUFFER);
        if(sdCardManager.readDownloadChunk(downloadFileChunkBuffer, DOWNLOAD_FILE_CHUNK_BUFFER, &chunkLinesRead, &eof)) {
          readOk = true;
          break;
        }
        if(debuggingSettings.debuggingJson) {
          serialManager.USBSerialPrint(FROM_CPUB);
          serialManager.USBSerialPrint("Download chunk read failed, retry ");
          serialManager.USBSerialPrintln(attempt + 1);
        }
      }

      if(!readOk){
        resetFileDownloadState();

        if(jsonSettings.enableJsonResponse) {

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        break;
      }

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_FILE_CHUNK_LINES_STR] = downloadFileChunkBuffer;
        responseJsonBuffer[JSON_KEY_CURRENT_FILE_LINE_STR] = downloadFileCurrentLine + chunkLinesRead;
        // File position after this chunk, so progress works without a line count.
        responseJsonBuffer[JSON_KEY_DOWNLOADED_BYTES_STR] = sdCardManager.getDownloadFilePosition();
        responseJsonBuffer[JSON_KEY_EOF_STR] = eof;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);

        // The WebSocket send is NEVER retried in place: sendWebSocketMessage() runs a
        // stateful, non-idempotent AT handshake (AT+WSSEND -> '>' -> data -> SEND OK),
        // and re-issuing it back-to-back after an ambiguous failure desyncs the link.
        // Instead, on failure we rewind the file to the start of this chunk and keep
        // the session open, so the client can re-request the exact same chunk on its
        // next call — a fresh AT command after the ESP has settled. The confirmed
        // progress counter is left untouched (we only advance it once the send
        // succeeds). A consecutive-failure cap bounds a genuinely dead link.
        if(!sendJsonResponse()) {

          ++downloadChunkSendFailures;

          if(debuggingSettings.debuggingJson) {
            serialManager.USBSerialPrint(FROM_CPUB);
            serialManager.USBSerialPrint("Download chunk send failed, rewinding for re-request (");
            serialManager.USBSerialPrint(downloadChunkSendFailures);
            serialManager.USBSerialPrint("/");
            serialManager.USBSerialPrint(DOWNLOAD_CHUNK_SEND_MAX_FAILURES);
            serialManager.USBSerialPrintln(")");
          }

          if(downloadChunkSendFailures >= DOWNLOAD_CHUNK_SEND_MAX_FAILURES || chunkStartPos < 0) {
            // Link appears dead (or we can't rewind) — give up on the transfer.
            resetFileDownloadState();
          } else {
            // Rewind so the next nextFileDownloadChunk re-reads and re-sends this chunk.
            sdCardManager.seekDownloadFile((uint32_t)chunkStartPos);
          }

          break;
        }

      }

      // Send confirmed: advance the progress counter and clear the failure streak.
      downloadFileCurrentLine += chunkLinesRead;
      downloadChunkSendFailures = 0;

      if(eof)
        resetFileDownloadState();
    }

    break;

  case GET_WIFI_SETTINGS:

    if(wifiSettings.apSsid[0] != NULL_CHAR){

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_AP_WIFI_SSID_STR] = wifiSettings.apSsid;
        responseJsonBuffer[JSON_KEY_AP_WIFI_PASSWORD_STR] = wifiSettings.apPassword;
        responseJsonBuffer[JSON_KEY_CLIENT_WIFI_SSID_STR] = wifiSettings.clientSsid;
        responseJsonBuffer[JSON_KEY_CLIENT_WIFI_PASSWORD_STR] = wifiSettings.clientPassword;
        responseJsonBuffer[JSON_KEY_HOTSPOT_ENABLED_STR] = wifiSettings.hotspotEnabled;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
        
      }

    }else{

      if(jsonSettings.enableJsonResponse) {

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_WIFI_SETTINGS_NOT_FOUND_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

    }

    }

    break;

  case GET_DEBUGGING_SETTINGS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;

      responseJsonBuffer[JSON_KEY_DEBUGGING_BASE_STR] = debuggingSettings.debuggingBase;
      responseJsonBuffer[JSON_KEY_DEBUGGING_SPINDLE_STR] = debuggingSettings.debuggingSpindle;
      responseJsonBuffer[JSON_KEY_DEBUGGING_STATUS_STR] = debuggingSettings.debuggingStatus;
      responseJsonBuffer[JSON_KEY_DEBUGGING_MACRO_BUFFER_STR] = debuggingSettings.debuggingMacroBuffer;
      responseJsonBuffer[JSON_KEY_DEBUGGING_SD_CARD_STR] = debuggingSettings.debuggingSDCard;
      responseJsonBuffer[JSON_KEY_DEBUGGING_JSON_STR] = debuggingSettings.debuggingJson;
      responseJsonBuffer[JSON_KEY_DEBUGGING_ESP32_STR] = debuggingSettings.debuggingESP32;
      responseJsonBuffer[JSON_KEY_DEBUGGING_EEPROM_STR] = debuggingSettings.debuggingEEPROM;
      responseJsonBuffer[JSON_KEY_DEBUGGING_JOB_STR] = debuggingSettings.debuggingJob;

      responseJsonBuffer[JSON_KEY_BENCHMARKING_STR] = debuggingSettings.benchmarking;
      responseJsonBuffer[JSON_KEY_BENCHMARK_MINIMUM_MS_STR] = debuggingSettings.benchmarkMinimumMs;
      
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
      
    }

    break;

  case GET_CURRENT_FEED_RATE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_CURRENT_FEED_RATE_STR] = currentFeedRate;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_CURRENT_SPINDLE_RPM:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_CURRENT_SPINDLE_RPM_STR] = currentSpindleRPM;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_CPUA_PASSTHROUGH_STATUS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_CPUA_PASSTHROUGH_STATUS_STR] = enableCPUAPassthrough;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_ESP32_PASSTHROUGH_STATUS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_ESP32_PASSTHROUGH_STATUS_STR] = enableESP32Passthrough;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_JSON_SETTINGS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JSON_ENABLE_JSON_RESPONSE_STR] = jsonSettings.enableJsonResponse;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
      
    }

    break;

  case GET_FEED_RATE_OVERRIDE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_FEED_RATE_OVERRIDE_STR] = feedRateOverride;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_SPINDLE_OVERRIDE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_SPINDLE_OVERRIDE_STR] = spindleOverride;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_ELECTRICAL_CURRENT:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();

      if(!isCurrentSensorInitialized) {
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_CURRENT_SENSOR_NOT_INITIALIZED_STR;
      } else {
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_ELECTRICAL_CURRENT_MA_STR] = currentSensor.getCurrent_mA();
      }

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_LASER_SENSOR_ENABLED:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_LASER_SENSOR_ENABLED_STR] = digitalRead(LASER_SENSOR_ENABLE_PIN) == HIGH;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_DISTANCE_LASER_MM:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();

      if(!isDistanceLaserSensorInitialized) {
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_DISTANCE_LASER_SENSOR_NOT_INITIALIZED_STR;
      } else {
        float distanceMm = 0.0f;
        bool isOutOfRange = false;

        if(readDistanceLaserMm(&distanceMm, &isOutOfRange)) {
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          responseJsonBuffer[JSON_KEY_DISTANCE_LASER_MM_STR] = roundf(distanceMm * 1000.0f) / 1000.0f;
        } else {
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = isOutOfRange
            ? JSON_VALUE_ERROR_DISTANCE_LASER_OUT_OF_RANGE_STR
            : JSON_VALUE_ERROR_DISTANCE_LASER_READ_FAILED_STR;
        }
      }

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_MACHINE_NAME:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MACHINE_NAME_STR] = machineNameSettings.machineName;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_FIRMWARE_VERSION:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_FIRMWARE_VERSION_STR] = firmwareVersion;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_TOOL_LENGTH:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_TOOL_LENGTH_STR] = toolLength;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_TOOL_LENGTH_REFERENCES:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_TOOL_LENGTH_REFERENCE_Z_STR] = toolLengthReferenceSettings.refZ;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_PROBE_HEIGHT:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_PROBE_HEIGHT_STR] = probeHeightSettings.heightMm;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_LASER_OFFSET_ZERO_SETTINGS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_X_BACK_MM_STR] = laserOffsetZeroSettings.xBackMm;
      responseJsonBuffer[JSON_KEY_LASER_OFFSET_ZERO_Y_BACK_MM_STR] = laserOffsetZeroSettings.yBackMm;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_OPENED_FILE_BOUNDS:

    if(jsonSettings.enableJsonResponse) {

      float jobMinX = 0.0f, jobMaxX = 0.0f, jobMinY = 0.0f, jobMaxY = 0.0f;

      clearResponseJsonBuffer();

      if (readOpenedFileBounds(&jobMinX, &jobMaxX, &jobMinY, &jobMaxY)) {

        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
        responseJsonBuffer[JSON_KEY_OPENED_FILE_NAME_STR] = openedReadFileName;
        responseJsonBuffer[JSON_KEY_FILE_MIN_X_MM_STR] = roundf(jobMinX * 1000.0f) / 1000.0f;
        responseJsonBuffer[JSON_KEY_FILE_MAX_X_MM_STR] = roundf(jobMaxX * 1000.0f) / 1000.0f;
        responseJsonBuffer[JSON_KEY_FILE_MIN_Y_MM_STR] = roundf(jobMinY * 1000.0f) / 1000.0f;
        responseJsonBuffer[JSON_KEY_FILE_MAX_Y_MM_STR] = roundf(jobMaxY * 1000.0f) / 1000.0f;

      } else {

        // No file open, or one written before MIN/MAX metadata existed. Distinguishing the two is not
        // worth a second error code: either way the toolpath's position is unknown.
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_NOT_FOUND_STR;

      }

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_BRUSHLESS_MOTOR_SETTINGS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_BRUSHLESS_MOTOR_DIRECTION_REVERSED_STR] = brushlessMotorSettings.directionReversed;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_HOUSING_SETTINGS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HOUSING_INSTALLED_STR] = housingSettings.housingInstalled;
      responseJsonBuffer[JSON_KEY_ALLOW_MOTION_WHEN_DOOR_OPEN_STR] = housingSettings.allowMotionWhenDoorOpen;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_SPINDLE_TYPE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_SPINDLE_TYPE_STR] =
          (INSTALLED_SPINDLE_TYPE == SPINDLE_48V) ? SPINDLE_TYPE_48V_STR : SPINDLE_TYPE_24V_STR;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_MACHINE_USAGE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_POWER_ON_HOURS_STR] = usagePowerOnSeconds / 3600.0f;
      responseJsonBuffer[JSON_KEY_RUN_HOURS_STR] = usageRunSeconds / 3600.0f;
      responseJsonBuffer[JSON_KEY_SPINDLE_HOURS_STR] = usageSpindleSeconds / 3600.0f;
      responseJsonBuffer[JSON_KEY_JOB_COUNT_STR] = usageJobCount;
      responseJsonBuffer[JSON_KEY_USAGE_DATA_VALID_STR] = usageDataValid;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_JOB_HISTORY:

    if(jsonSettings.enableJsonResponse) {

      // Optional args: "rangeStart" selects which 100-job file (snapped to a block
      // start: 1, 101, 201, ...); defaults to the latest block. "offset" pages within
      // that file when long file names mean not all 100 entries fit in one response.
      uint32_t rangeStart;
      if (!receivedJsonBuffer[JSON_KEY_RANGE_START_STR].isNull()) {
        long requested = receivedJsonBuffer[JSON_KEY_RANGE_START_STR].as<long>();
        if (requested < 1) requested = 1;
        rangeStart = ((uint32_t)(requested - 1) / JOBS_PER_LOG_FILE) * JOBS_PER_LOG_FILE + 1;
      } else {
        uint32_t latestJob = (usageJobCount < 1) ? 1 : usageJobCount;
        rangeStart = ((latestJob - 1) / JOBS_PER_LOG_FILE) * JOBS_PER_LOG_FILE + 1;
      }
      uint32_t rangeEnd = rangeStart + JOBS_PER_LOG_FILE - 1;

      uint32_t offset = 0;
      if (!receivedJsonBuffer[JSON_KEY_OFFSET_STR].isNull()) {
        long requestedOffset = receivedJsonBuffer[JSON_KEY_OFFSET_STR].as<long>();
        if (requestedOffset > 0) offset = (uint32_t)requestedOffset;
      }

      char fileName[JOB_LOG_FILENAME_CHARS];
      jobLogFileNameForJob(rangeStart, fileName, sizeof(fileName));

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_RANGE_START_STR] = rangeStart;
      responseJsonBuffer[JSON_KEY_RANGE_END_STR] = rangeEnd;
      responseJsonBuffer[JSON_KEY_OFFSET_STR] = offset;
      responseJsonBuffer[JSON_KEY_JOB_COUNT_STR] = usageJobCount; // total jobs ever, so the app can derive how many ranges exist
      JsonArray jobHistoryArray = responseJsonBuffer[JSON_KEY_JOB_HISTORY_STR].to<JsonArray>();

      uint32_t returned = 0;
      bool hasMore = false;

      long fileSize = sdCardManager.getFileSize(fileName);

      if (fileSize > 0) {

        // The whole file is at most JOB_LOG_FILE_MAX_BYTES (100 short lines), so read it in one go.
        uint32_t readLen = (fileSize > JOB_LOG_FILE_MAX_BYTES) ? JOB_LOG_FILE_MAX_BYTES : (uint32_t)fileSize;
        static char jobLogBuffer[JOB_LOG_FILE_MAX_BYTES + 1];
        int bytesRead = sdCardManager.readBinaryAt(fileName, 0, (uint8_t*)jobLogBuffer, readLen);

        if (bytesRead > 0) {

          jobLogBuffer[bytesRead] = NULL_CHAR;

          // Conservative running estimate of the serialized size so we never overflow
          // jsonResponseCharBuffer (JSON_BUFFER_SIZE); page the rest via "offset".
          size_t estimatedBytes = 256; // wrapper + numeric fields
          uint32_t lineIndex = 0;

          char* line = strtok(jobLogBuffer, "\n");
          while (line != NULL) {

            if (lineIndex >= offset) {

              char* comma = strchr(line, ',');
              const char* startTimeField = (comma != NULL) ? line : "";
              const char* nameField = (comma != NULL) ? (comma + 1) : line;
              if (comma != NULL) *comma = NULL_CHAR;

              size_t entryBytes = strlen(startTimeField) + strlen(nameField) + 40; // keys/quotes/braces
              if (estimatedBytes + entryBytes > (size_t)(JSON_BUFFER_SIZE - 64)) {
                hasMore = true; // more entries remain in this file; client should re-request with a higher offset
                break;
              }
              estimatedBytes += entryBytes;

              JsonObject entry = jobHistoryArray.add<JsonObject>();
              entry[JSON_KEY_START_TIME_STR] = startTimeField;
              entry[JSON_KEY_FILE_NAME_STR] = nameField;
              ++returned;

            }

            ++lineIndex;
            line = strtok(NULL, "\n");
          }

        }

      }

      responseJsonBuffer[JSON_KEY_RETURNED_STR] = returned;
      responseJsonBuffer[JSON_KEY_HAS_MORE_STR] = hasMore;

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_MATERIAL_CENTER_POSITION:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_MATERIAL_CENTER_VALID_STR] = hasLastDetectedMaterialCenter;
      if (hasLastDetectedMaterialCenter) {
        responseJsonBuffer[JSON_KEY_MATERIAL_CENTER_X_STR] = lastDetectedMaterialCenterX;
        responseJsonBuffer[JSON_KEY_MATERIAL_CENTER_Y_STR] = lastDetectedMaterialCenterY;
      }
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_JOB_COUNT:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_JOB_COUNT_STR] = usageJobCount;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_HEIGHT_MAP_STATUS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_CONFIGURED_STR] = heightMapConfigured;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_X_MM_STR] = heightMapSizeXmm;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SIZE_Y_MM_STR] = heightMapSizeYmm;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SCAN_IN_PROGRESS_STR] = heightMapScanInProgress;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_READY_STR] = heightMapReady;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COMPENSATION_ENABLED_STR] = heightMapCompensationEnabled;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DENSITY_STR] =
        heightMapFineDensity ? JSON_VALUE_HEIGHT_MAP_DENSITY_FINE_STR : JSON_VALUE_HEIGHT_MAP_DENSITY_COARSE_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ACTIVE_FOR_OPENED_FILE_STR] =
        (openedReadFileName[0] != NULL_CHAR)
        && heightMapReady
        && !heightMapUsedForCurrentFile
        && (strcmp(openedReadFileName, heightMapBoundFile) == 0);
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_X_STR] = heightMapGridPointsX;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_GRID_POINTS_Y_STR] = heightMapGridPointsY;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_SCANNED_STR] = heightMapNextPointIndex;
      // Actual grid pitch in mm. heightMapDensity only names the mode, and the pitch is recomputed as
      // size / (points - 1), so it can come out finer than the coarse/fine nominal value.
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SPACING_MM_STR] =
        roundf(heightMapSpacingYmm * 1000.0f) / 1000.0f;
      // Job coordinates of the map's (0,0) cell, so the frontend can check the map really is over the
      // toolpath (compare against the file's own bounds) instead of assuming it.
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_X_MM_STR] =
        roundf(heightMapOriginX * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ORIGIN_Y_MM_STR] =
        roundf(heightMapOriginY * 1000.0f) / 1000.0f;
#if HEIGHT_MAP_USE_LEGACY_GRID_SCAN
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_LINE_SCAN_STR] = false;
#else
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_LINE_SCAN_STR] = true;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ROWS_SCANNED_STR] = heightMapCurrentRow;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_ROWS_TOTAL_STR] = heightMapGridPointsY;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_SAMPLES_TAKEN_STR] = heightMapSamplesTaken;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_CELLS_INTERPOLATED_STR] = heightMapCellsInterpolated;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MIN_CELL_SAMPLES_STR] = heightMapMinCellSamples;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_MAX_CELL_SPREAD_MM_STR] =
        roundf(heightMapMaxCellSpreadMm * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISY_CELLS_STR] = heightMapNoisyCells;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_X_MM_STR] =
        roundf((float)heightMapNoisiestCellCol * heightMapSpacingXmm * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_NOISIEST_CELL_Y_MM_STR] =
        roundf(-(float)heightMapNoisiestCellRow * heightMapSpacingYmm * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_BASELINE_SAMPLES_STR] = heightMapBaselineSampleCount;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_BASELINE_SPREAD_MM_STR] =
        roundf(heightMapBaselineSpreadMm * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DATUM_FROM_CELLS_STR] = heightMapDatumFromCells;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DATUM_BIAS_MM_STR] =
        roundf(heightMapDatumBiasMm * 1000.0f) / 1000.0f;
    responseJsonBuffer[JSON_KEY_HEIGHT_MAP_DATUM_BIAS_MM_STR] =
      roundf(heightMapDatumBiasMm * 1000.0f) / 1000.0f;
#endif
      // The coverage verdict itself, so the frontend does not have to re-derive the geometry (and its
      // tolerance) from raw bounds and risk drifting from what startJob actually enforces. Omitted
      // when unknowable, so an absent key reads as "unknown", never as "does not cover".
      {
        bool coverageKnown = false;
        bool covers = doesHeightMapCoverOpenedJob(&coverageKnown);
        if (coverageKnown)
          responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COVERS_OPENED_JOB_STR] = covers;
      }
      if (heightMapBoundFile[0] != NULL_CHAR)
        responseJsonBuffer[JSON_KEY_HEIGHT_MAP_BOUND_FILE_STR] = heightMapBoundFile;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_HEIGHT_MAP_POINTS:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();

      if (!heightMapReady || heightMapTotalPoints == 0) {
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_HEIGHT_MAP_NOT_READY_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
        break;
      }

      int startIndex = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_START_INDEX_STR] | 0;
      int maxCount = receivedJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_MAX_COUNT_STR] | 25;

      if (maxCount < 1)
        maxCount = 1;
      if (maxCount > 40)
        maxCount = 40;

      if (startIndex < 0 || startIndex >= (int)heightMapTotalPoints) {
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_INVALID_JSON_VALUE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();
        break;
      }

      int endIndex = startIndex + maxCount;
      if (endIndex > (int)heightMapTotalPoints)
        endIndex = (int)heightMapTotalPoints;

      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_TOTAL_STR] = heightMapTotalPoints;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_START_INDEX_STR] = startIndex;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_RETURNED_STR] = endIndex - startIndex;
      responseJsonBuffer[JSON_KEY_HEIGHT_MAP_COORDINATE_SPACE_STR] = JSON_VALUE_COORDINATE_SPACE_RELATIVE_STR;

      JsonArray pointsArray = responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINTS_STR].to<JsonArray>();
      JsonArray pointCoordinatesArray = responseJsonBuffer[JSON_KEY_HEIGHT_MAP_POINT_COORDINATES_STR].to<JsonArray>();
      for (int idx = startIndex; idx < endIndex; ++idx) {
        float pointWorkX = 0.0f;
        float pointWorkY = 0.0f;
        getHeightMapPointTarget((uint16_t)idx, &pointWorkX, &pointWorkY);

        // Report coordinates RELATIVE to the scan-start corner (origin), not machine coords, for
        // easier debugging: col*spacing in X (0,5,10,...) and -row*spacing in Y (0,-5,-10,...).
        float roundedX = roundf((pointWorkX - heightMapOriginX) * 1000.0f) / 1000.0f;
        float roundedY = roundf((pointWorkY - heightMapOriginY) * 1000.0f) / 1000.0f;
        float value = roundf(heightMapRelativeMm[idx] * 1000.0f) / 1000.0f;

        pointsArray.add(value);

        JsonObject pointObject = pointCoordinatesArray.add<JsonObject>();
        pointObject[JSON_KEY_MOVE_X_STR] = roundedX;
        pointObject[JSON_KEY_MOVE_Y_STR] = roundedY;
        pointObject[JSON_KEY_VALUE_STR] = value;
      }

      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();

    }

    break;

  case GET_POINT_CLOUD_STATUS:

    if (jsonSettings.enableJsonResponse) {
      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_SCAN_IN_PROGRESS_STR] = pointCloudScanInProgress;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_READY_STR] = pointCloudReady;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_X_STR] = pointCloudGridPointsX;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_GRID_POINTS_Y_STR] = pointCloudGridPointsY;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_SCANNED_STR] = pointCloudNextPointIndex;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_POINTS_TOTAL_STR] = pointCloudTotalPoints;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_X_MM_STR] = pointCloudSizeXmm;
      responseJsonBuffer[JSON_KEY_POINT_CLOUD_SIZE_Y_MM_STR] = pointCloudSizeYmm;
      if (pointCloudScanInProgress)
        responseJsonBuffer[JSON_KEY_POINT_CLOUD_CURRENT_ROW_STR] = pointCloudCurrentRow;
      if (pointCloudDensityStr[0] != NULL_CHAR)
        responseJsonBuffer[JSON_KEY_POINT_CLOUD_DENSITY_STR] = pointCloudDensityStr;
      if (pointCloudFilename[0] != NULL_CHAR)
        responseJsonBuffer[JSON_KEY_POINT_CLOUD_FILENAME_STR] = pointCloudFilename;
      serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
      sendJsonResponse();
    }

    break;

  default:

    if(jsonSettings.enableJsonResponse){
    
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_JSON_TYPE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

    }

    break;
  }

}

// Fills in the error of a failed upload response: the specific cause, plus the measured job
// size against the work-area limits when the file was rejected for being too big.
void applyFileTransferFailureToResponse(){

  switch(fileTransferFailureReason){

    case FILE_TRANSFER_FAILURE_DIMENSIONS_EXCEEDED:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_DIMENSIONS_EXCEEDED_STR;
      // Same SIZEX -> width, SIZEY -> depth, SIZEZ -> height mapping the file listing uses.
      responseJsonBuffer[JSON_KEY_FILE_WIDTH_STR] = roundf(fileTransferFailureSizeX * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_FILE_DEPTH_STR] = roundf(fileTransferFailureSizeY * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_FILE_HEIGHT_STR] = roundf(fileTransferFailureSizeZ * 1000.0f) / 1000.0f;
      responseJsonBuffer[JSON_KEY_MAX_FILE_WIDTH_STR] = MAX_WORK_DIMENSION_XY_MM;
      responseJsonBuffer[JSON_KEY_MAX_FILE_DEPTH_STR] = MAX_WORK_DIMENSION_XY_MM;
      responseJsonBuffer[JSON_KEY_MAX_FILE_HEIGHT_STR] = MAX_WORK_DIMENSION_Z_MM;
      break;

    case FILE_TRANSFER_FAILURE_NOT_STARTED:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_TRANSFER_NOT_STARTED_STR;
      break;

    case FILE_TRANSFER_FAILURE_INVALID_CHUNK:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_MISSING_JSON_KEY_STR;
      break;

    case FILE_TRANSFER_FAILURE_SD_READ:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_READ_FAILED_STR;
      break;

    case FILE_TRANSFER_FAILURE_SD_WRITE:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_WRITE_FAILED_STR;
      break;

    case FILE_TRANSFER_FAILURE_FILE_CREATION:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_CREATION_FAILED_STR;
      break;

    case FILE_TRANSFER_FAILURE_HASH:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_HASH_FAILED_STR;
      break;

    default:
      responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_FINALIZE_FAILED_STR;
      break;

  }

}

void handleFileChunkJson(){

  if(transferFileLines > 0 && sdCardManager.isWriteFileOpen()){

        bool chunkProcessed = processTransferChunkPayload(receivedJsonBuffer[JSON_KEY_FILE_CHUNK_LINES_STR]);

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          if(chunkProcessed) {
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
            // Report how many lines the controller still expects, so the client can tell a
            // "chunk stored" ack (linesRemaining > 0) from "transfer complete" (linesRemaining == 0 + fileWritten).
            // This makes a line-count mismatch detectable by the client instead of only via the chunk-timeout abort.
            responseJsonBuffer[JSON_KEY_LINES_REMAINING_STR] = transferFileLines;
            if(transferFileLines == 0)
              responseJsonBuffer[JSON_VALUE_FILE_WRITTEN_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
          } else {
            responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
            applyFileTransferFailureToResponse();
          }
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

    }else{

    resetFileTransferState(true);

    if(debuggingSettings.debuggingJson) { serialManager.USBSerialPrintln("Problem with file chunk or file transfer not started"); }


      if(jsonSettings.enableJsonResponse){
  
        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_FILE_TRANSFER_NOT_STARTED_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    }
    

}

////////////////////////////////////////////////////////UTILITY METHODS////////////////////////////////////////////////////////////
void clearResponseJsonBuffer() {

  arrayUtil.clearCharArray(jsonResponseCharBuffer, JSON_BUFFER_SIZE);
  responseJsonBuffer.clear();
}

void resetCurrentPostions() {

  currentMachinePositionX = 0.0;
  currentMachinePositionY = 0.0;
  currentMachinePositionZ = 0.0;
  currentMachinePositionA = 0.0;

}

void writeMACROLine(const char _array[]) {

  /*
  //check first empty element in the bufferMacroLines
  //add the current macro line to that
  //the loop will take care of writing those to teensy waiting for the ok
  for (int C = 0; C < TOOL_MACRO_BUFFER_LINES; C++) {
    
    if (bufferMacroLines[C][0] == NULL_CHAR) {
      strcpy(bufferMacroLines[C], _array);
      return;
    }

    //if there is no space in the buffer we skip the macro line
    //we absolutely avoid that because other wise some commands for the tool changer will not be executed
  /
  }


  */
}


void resetMobileWebSocketMessageBuffer() {

  arrayUtil.clearCharArray(mobileWebSocketMessageBuffer, ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE);
  mobileWebSocketMessageBufferPos = 0;

}

void resetMobileBleScanState() {

  bleDeInitRequested = false;
  bleDeInitComplete = false;
  bleInitRequested = false;
  bleInitComplete = false;
  bleScanParamsRequested = false;
  bleScanParamsComplete = false;
  bleScanRequested = false;
  bleScanComplete = false;
  arrayUtil.clearCharArray(mobileServerName, ESP32C3_MOBILE_SERVER_NAME_BUFFER_SIZE);
  mobileWebSocketNextRetryMs = millis() + MOBILE_WEBSOCKET_RETRY_INTERVAL_MS;
  mobileWebSocketLastApiPingMs = 0;

  // Reset any pending AT command that may never have received a response,
  // so that the next sendATCommand() call is not silently blocked.
  esp32c3Manager.abortPendingCommand();

}

void restartWebSocketConnectionPursuitIfIdle() {

  if(isMobileWebSocketConnected)
    return;

  resetMobileBleScanState();
  mobileWebSocketNextRetryMs = 0;

}


void resetESP32WiFiConnection() {

  isClientWiFiConnected = false;
  isMobileWebSocketConnected = false;
  isMobileWebSocketConnecting = false;
  isBleActive = false;
  pendingForcedWebSocketCloseLinkId = -1;
  pendingForcedWebSocketCloseStartMs = 0;
  clientWiFiConnectionAttempted = false;

  // Resume the ESP32 setup flow at the WiFi join step so a disconnect
  // triggers a fresh AT+CWJAP attempt instead of leaving the state machine idle.
  initialESP32ResponsesReceived = initialESP32ExpectedResponses - 1;
  // When hotspot is disabled, CWJAP is sent at step 4 (gated by mobileWebsocketConfigured).
  // Reset that flag so the step fires again on reconnect.
  if(!wifiSettings.hotspotEnabled)
    mobileWebsocketConfigured = false;
  initialESP32C3Config = false;

  resetMobileBleScanState();

}

bool hasWebSocketPingTimedOut(unsigned long lastPingMs) {

  if(lastPingMs == 0)
    return false;

  return (millis() - lastPingMs) >= WEBSOCKET_API_PING_TIMEOUT_MS;

}

bool hasSerialPingTimedOut() {

  if(serialLastApiPingMs == 0)
    return false;

  return (millis() - serialLastApiPingMs) >= SERIAL_API_PING_TIMEOUT_MS;

}

void resetCurrentSerialPingWatchdog() {

  if(currentInputSource != INPUT_SOURCE_USB)
    return;

  isClientConnected = true;
  serialLastApiPingMs = millis();

}

void resetCurrentWebSocketPingWatchdog() {

  if(currentInputSource == INPUT_SOURCE_MOBILE_WEBSOCKET)
    mobileWebSocketLastApiPingMs = millis();

}

bool handleForcedWebSocketClose() {

  if(pendingForcedWebSocketCloseLinkId < 0)
    return false;

  if(pendingForcedWebSocketCloseStartMs == 0) {
    char closeCmd[32];
    arrayUtil.clearCharArray(closeCmd, sizeof(closeCmd));
    strcat(closeCmd, ESP32C3_CLOSE_WEBSOCKET);
    strcat(closeCmd, "1");

    if(esp32c3Manager.sendATCommand(closeCmd)) {
      pendingForcedWebSocketCloseStartMs = millis();
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Forced websocket close command sent: ");}
      if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln(closeCmd);}
    }

    return true;
  }

  if(millis() - pendingForcedWebSocketCloseStartMs > ESP32C3_WEBSOCKET_FORCE_CLOSE_TIMEOUT_MS) {
    esp32c3Manager.abortPendingCommand();
    finalizeForcedWebSocketClose();
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrintln("Forced websocket close command timed out; local state reset");}
    return true;
  }

  if(esp32c3Manager.checkForResponse() == ESP32C3_RESPONSE_READY) {
    int responseType = esp32c3Manager.getResponseType();
    char tempResponse[ESP32C3_RESPONSE_BUFFER_SIZE];
    arrayUtil.clearCharArray(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);
    esp32c3Manager.getResponse(tempResponse, ESP32C3_RESPONSE_BUFFER_SIZE);

    finalizeForcedWebSocketClose();

    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint(FROM_CPUB);}
    if(debuggingSettings.debuggingESP32){serialManager.USBSerialPrint("Forced websocket close completed with response: ");}
    if(debuggingSettings.debuggingESP32){
      if(responseType == ESP32C3_RESPONSE_TYPE_OK)
        serialManager.USBSerialPrintln("OK");
      else if(responseType == ESP32C3_RESPONSE_TYPE_ERROR)
        serialManager.USBSerialPrintln("ERROR");
      else
        serialManager.USBSerialPrintln(tempResponse);
    }
  }

  return true;

}

//Debug print of an AT command that carries a WiFi password (AT+CWSAP / AT+CWJAP, password in the second
//quoted field), with the password replaced by asterisks so it never ends up in console logs.
void printWiFiCommandMasked(const char* atCommand) {

  serialManager.USBSerialPrint(FROM_CPUB);

  int quoteCount = 0;

  for (int i = 0; atCommand[i] != NULL_CHAR; ++i) {

    if (atCommand[i] == '"') {
      ++quoteCount;
      if (quoteCount == 3)
        serialManager.USBSerialPrint("\"********");
      else if (quoteCount != 4)
        serialManager.USBSerialWrite((byte)atCommand[i]);
      else
        serialManager.USBSerialWrite((byte)'"');
      continue;
    }

    if (quoteCount != 3)
      serialManager.USBSerialWrite((byte)atCommand[i]);
  }

  serialManager.USBSerialPrintln(" sent to ESP32C3");
}

void finalizeForcedWebSocketClose() {

  isMobileWebSocketConnected = false;
  isMobileWebSocketConnecting = false;
  mobileWebSocketLastApiPingMs = 0;

  restartWebSocketConnectionPursuitIfIdle();

  pendingForcedWebSocketCloseLinkId = -1;
  pendingForcedWebSocketCloseStartMs = 0;

}

void resetCPUALine() {

  arrayUtil.clearCharArray(CPUALineArray, CPUALinePos);
  CPUALinePos = 0;
  bufferCharCPUA = -1;
}

void resetUSBLine() {

  arrayUtil.clearCharArray(USBLineArray, USBLinePos);
  USBLinePos = 0;
  bufferCharUSB = -1;
}



void setLEDColor(int red, int green, int blue) {

  analogWrite(RED_LED_OUTPUT_PIN, red);
  analogWrite(GREEN_LED_OUTPUT_PIN, green);
  analogWrite(BLUE_LED_OUTPUT_PIN, blue);

  // if(debuggingSettings.debuggingStatus) {

  //   serialManager.USBSerialPrint("LED color set to R:");
  //   serialManager.USBSerialPrint(red);
  //   serialManager.USBSerialPrint(" G:");
  //   serialManager.USBSerialPrint(green);
  //   serialManager.USBSerialPrint(" B:");
  //   serialManager.USBSerialPrintln(blue);
  
  // }

}

bool isProcedureBusy() {

  return heightMapScanInProgress
    || pointCloudScanInProgress
    || isLaserMaterialBoundsProbingActive
    || isLaserEdgeProbingActive;

}

// Door sensor reads HIGH when the door is open, LOW when closed (see Config.h).
bool isDoorOpen() {

  return digitalRead(DOOR_SENSOR_PIN) == DOOR_SENSOR_OPEN_STATE;

}

// True when the spindle is (or is being ramped) at or above the minimum running
// speed. Uses the same reference as the pause spindle-stop decision so the two stay
// consistent. NOT for the door interlock — see isSpindleOnAtAnySpeed().
bool isSpindleSpinning() {

  long spindleReferenceRPM = currentSpindleRPM;
  if (commandedSpindleRPM > spindleReferenceRPM)
    spindleReferenceRPM = commandedSpindleRPM;

  return spindleReferenceRPM >= MINIMUM_SPINDLE_RPM;

}

// True when the spindle is turning (or ramping up) at ANY speed. The door interlock has no
// "slow enough to be safe" threshold: a tool spinning at 500 rpm with the lid open is still
// a spinning tool, so the interlock uses this instead of isSpindleSpinning(), which applies
// MINIMUM_SPINDLE_RPM and drives the pause spindle-stop decision and the usage meter.
bool isSpindleOnAtAnySpeed() {

  if (isManualSpindleRampActive)
    return true;

  return currentSpindleRPM > 0 || commandedSpindleRPM > 0;

}

// Door interlock state used by the STREAMING gates (job lines, macros, passthrough): the
// housing is installed and the lid has read open for the full debounce window. Debounced so
// a brief electrical glitch can never drop a line or pause a running job.
bool isDoorInterlockActive() {

  return housingSettings.housingInstalled && doorOpenDebounced;

}

// Same interlock, but on the RAW pin, for one-shot API commands (startJob / resumeJob).
// Those are refused the instant the lid reads open: being strict costs nothing on a command
// the user just sent, whereas the streaming gates need the debounce.
bool isDoorInterlockActiveNow() {

  return housingSettings.housingInstalled && (doorOpenDebounced || isDoorOpen());

}

// True when the lid interlock forbids MOTION: the housing is installed, the lid is open, and
// the preference does not allow motion with the lid open. Distinct from the spindle-start gate
// — the spindle is blocked whenever the lid is open, regardless of this preference. Uses the
// raw pin (isDoorInterlockActiveNow) because it gates one-shot API commands.
bool isMotionBlockedByDoor() {

  return isDoorInterlockActiveNow() && !housingSettings.allowMotionWhenDoorOpen;

}

// True when the machine is doing something the lid interlock must be able to stop: a motion
// cycle, a jog, a homing cycle, a running job, or a turning spindle. Jog and Home are DISTINCT
// GRBL states — grbl reports `Jog` and `Home`, never `Run`, for those — so they must be listed
// explicitly. Without them, jogging and homing continued with the lid open even when
// allowMotionWhenDoorOpen was false, which contradicted the documented behavior.
bool isMachineActiveForDoorInterlock() {

  return currentMachineState == RUN
      || currentMachineState == JOG
      || currentMachineState == HOME
      || isJobRunning
      || isSpindleOnAtAnySpeed();

}

// Central spindle-start gate. Nothing that would make the spindle turn may reach GRBL when:
//  - the housing is installed and the lid is open (any RPM, no threshold), or
//  - the job is paused (the machine is parked; the spindle must only come back on resume).
bool isSpindleStartBlocked() {

  return isDoorInterlockActive() || isJobPaused;

}

// Mirrors CPUB's interlock DECISION into grblHAL as a real safety-door signal (CMD_VIRTUAL_DOOR_*).
// Deliberately NOT the raw lid state: grblHAL would then halt on every lid opening and
// `allowMotionWhenDoorOpen` would become unreachable. CPUB keeps the policy; grblHAL becomes the
// enforcement. While asserted, grblHAL refuses to start a parking restore at all, which removes
// the whole class of races that 0x84 alone suffers from (an event can be swallowed by
// state_await_resume(), or land mid-restore; a level cannot).
//
// The assertion LATCHES while the lid is open. The trigger is "spindle turning, or motion not
// permitted", but it must not clear the instant grblHAL stops the spindle as part of parking —
// that would release the interlock while the lid is still open and let a resume through.
void handleVirtualDoorSignal() {

  static bool virtualDoorSentOpen = false; // what CPUA currently believes
  static bool virtualDoorSynced   = false; // force one send at boot so CPUA starts in a known state

  if (!isDoorInterlockActive())
    virtualDoorLatched = false;            // lid closed, or no housing: never hold the machine
  else if (isSpindleOnAtAnySpeed() || !housingSettings.allowMotionWhenDoorOpen)
    virtualDoorLatched = true;             // must hold
  // else: lid open but motion permitted — leave the latch as it is (see the note above).

  const bool desired = virtualDoorLatched;

  if (virtualDoorSynced && desired == virtualDoorSentOpen)
    return;

  virtualDoorSentOpen = desired;
  virtualDoorSynced = true;

  serialManager.CPUASerialWrite(static_cast<byte>(desired ? GRBL_VIRTUAL_DOOR_OPEN : GRBL_VIRTUAL_DOOR_CLOSED));

  serialManager.USBSerialPrint(FROM_CPUB);
  serialManager.USBSerialPrintln(desired
    ? "Virtual safety door ASSERTED to GRBL: resume refused until released"
    : "Virtual safety door released to GRBL");

}

// Door/lid safety interlock. Only active when the housing is installed; otherwise the door
// sensor is ignored entirely. When the housing is installed and the lid is opened (door pin
// LOW, debounced) while the machine is doing something that must stop — a motion cycle is
// running, a job is running, or the spindle is turning at any speed — it triggers exactly the
// same action as an API pauseJob (grbl safety-door / parking: spindle off + Z lift), so the app
// sees it as a normal pause. Level-based: if the lid is still open it will re-pause after a
// resume, acting as an interlock; you must close the lid to run again. Closing the lid never
// auto-resumes. This is the REACTIVE half of the interlock; the preventive half is the
// spindle-start gate (isSpindleStartBlocked), which stops M3 from ever being forwarded while
// the lid is open, so the spindle cannot start and then be caught here.
void handleDoorSensor() {

  // Log the raw door pin + gate flags whenever the pin state changes. Always on (not gated on
  // debuggingBase) so wiring / polarity / housing-flag issues are visible on the console.
  {
    static int lastLoggedDoorRaw = -1;
    int raw = digitalRead(DOOR_SENSOR_PIN);
    if (raw != lastLoggedDoorRaw) {
      lastLoggedDoorRaw = raw;
      char dbg[96];
      snprintf(dbg, sizeof(dbg), "Door GPIO23=%s housing=%d active=%d paused=%d",
               raw == HIGH ? "HIGH(open)" : "LOW(closed)",
               housingSettings.housingInstalled ? 1 : 0,
               isMachineActiveForDoorInterlock() ? 1 : 0,
               isJobPaused ? 1 : 0);
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln(dbg);
    }
  }

  if (!housingSettings.housingInstalled) {
    doorSensorOpenSinceMs = 0;
    doorOpenDebounced = false;    // no housing: the sensor is ignored, the gates stay open
    return;
  }

  if (!isDoorOpen()) {            // lid closed (pin LOW) — nothing to do, reset debounce
    doorSensorOpenSinceMs = 0;
    doorOpenDebounced = false;
    return;
  }

  // Lid reads open (pin HIGH): require it to persist DOOR_SENSOR_DEBOUNCE_MS to reject glitches.
  if (doorSensorOpenSinceMs == 0)
    doorSensorOpenSinceMs = millis();
  if ((unsigned long)(millis() - doorSensorOpenSinceMs) < DOOR_SENSOR_DEBOUNCE_MS)
    return;

  // Confirmed lid-open: from here on every spindle-start gate (isSpindleStartBlocked) refuses
  // M3/S, so nothing can start the spindle again until the lid is closed.
  doorOpenDebounced = true;

  // Decide whether to pause:
  //  - spindle turning at ANY speed with the door open is always unsafe → pause regardless of
  //    the preference (no MINIMUM_SPINDLE_RPM threshold here — see isSpindleOnAtAnySpeed);
  //  - spindle off → pause only if the preference does NOT allow motion with the door open.
  bool machineActive = isMachineActiveForDoorInterlock();
  bool shouldPause = isSpindleOnAtAnySpeed() || !housingSettings.allowMotionWhenDoorOpen;

  // The pause is normally one-shot: isJobPaused latches so we do not spam 0x84 while grbl parks.
  // That latch must never become a blind spot. grblHAL can SWALLOW a safety-door byte: in
  // state_await_resume() the EXEC_SAFETY_DOOR branch only updates a status field when no physical
  // door input is configured (SAFETY_DOOR_ENABLE is off here), so a 0x84 racing a pending cycle
  // start is discarded and the machine resumes anyway. CPUB would then sit believing the job is
  // paused and stop feeding lines, while grbl runs on the planner blocks already queued — motion
  // for a few seconds with the lid open, then a starve-stop. If grbl is REPORTING Run while the
  // lid is open, the pause demonstrably did not take, so assert it again.
  bool pauseNotEffective = isJobPaused && currentMachineState == RUN;

  if (machineActive && shouldPause && (!isJobPaused || pauseNotEffective)) {

    if (pauseNotEffective) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Machine still running with the lid open while marked paused: re-asserting the pause");
    }


    // Sent immediately, including in the middle of a parking RESUME. grblHAL handles a
    // safety-door request during the restore by design (state_restore / state_await_resumed
    // call restart_retract(): spindle off + full retract to the park target), which is exactly
    // the response we want when the lid opens while the spindle is ramping back up. Deferring
    // it until the machine reaches Run was tried and is wrong — it lets the restore finish and
    // the tool descend before the pause lands.
    pauseJobInternal(true);

    if (debuggingSettings.debuggingBase) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("Lid opened while machine active (housing installed): pausing");
    }

  }

}

// Standard CRC-32 (IEEE 802.3, poly 0xEDB88320), bitwise — the usage record is tiny
// and written infrequently, so a lookup table is not worth the flash.
uint32_t computeCrc32(const uint8_t* data, size_t len) {

  uint32_t crc = 0xFFFFFFFFUL;

  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; ++bit)
      crc = (crc & 1) ? ((crc >> 1) ^ 0xEDB88320UL) : (crc >> 1);
  }

  return crc ^ 0xFFFFFFFFUL;

}

// Loads the usage meter from the hidden SD file. The file holds two records (slot
// A/B); the valid one (correct magic + CRC32) with the highest sequence wins. If
// none is valid (missing/corrupt/tampered), the meter starts from zero.
void loadMachineUsage() {

  usagePowerOnSeconds = 0;
  usageRunSeconds = 0;
  usageSpindleSeconds = 0;
  usageJobCount = 0;
  usageSequence = 0;
  usageDataValid = false;

  usageLastTickMs = millis();
  usageLastFlushMs = millis();

  if (!sdCardManager.connected)
    return;

  for (int slot = 0; slot < 2; ++slot) {

    UsageRecord rec;
    int bytesRead = sdCardManager.readBinaryAt(USAGE_LOG_FILENAME, (uint32_t)slot * sizeof(UsageRecord), (uint8_t*)&rec, sizeof(rec));

    if (bytesRead != (int)sizeof(rec))
      continue;
    if (rec.magic != USAGE_RECORD_MAGIC || rec.version != USAGE_RECORD_VERSION)
      continue;
    if (computeCrc32((const uint8_t*)&rec, sizeof(UsageRecord) - sizeof(uint32_t)) != rec.crc32)
      continue;

    if (!usageDataValid || rec.sequence >= usageSequence) {
      usagePowerOnSeconds = rec.powerOnSeconds;
      usageRunSeconds = rec.runSeconds;
      usageSpindleSeconds = rec.spindleSeconds;
      usageJobCount = rec.jobCount;
      usageSequence = rec.sequence;
      usageDataValid = true;
    }

  }

  if (debuggingSettings.debuggingSDCard) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln(usageDataValid
      ? "Machine usage meter loaded from SD"
      : "No valid machine usage record on SD, starting meter from zero");
  }

}

// Persists the current meter to the inactive SD slot (alternating A/B) so a torn
// write can never destroy the last good record.
bool saveMachineUsage() {

  if (!sdCardManager.connected)
    return false;

  UsageRecord rec;
  rec.magic = USAGE_RECORD_MAGIC;
  rec.version = USAGE_RECORD_VERSION;
  rec.reserved = 0;
  rec.sequence = ++usageSequence;
  rec.powerOnSeconds = usagePowerOnSeconds;
  rec.runSeconds = usageRunSeconds;
  rec.spindleSeconds = usageSpindleSeconds;
  rec.jobCount = usageJobCount;
  rec.crc32 = computeCrc32((const uint8_t*)&rec, sizeof(UsageRecord) - sizeof(uint32_t));

  uint32_t slot = rec.sequence % 2; // alternate slots; never overwrite the live one
  bool ok = sdCardManager.writeBinaryAt(USAGE_LOG_FILENAME, slot * sizeof(UsageRecord), (const uint8_t*)&rec, sizeof(rec));

  if (ok)
    usageDataValid = true;

  if (debuggingSettings.debuggingSDCard && !ok) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrintln("Machine usage meter SD write failed");
  }

  return ok;

}

// Accumulates the usage counters (1-second tick) and flushes to SD every
// USAGE_FLUSH_INTERVAL_MS. Uses unsigned millis() deltas so it is millis()-wrap safe.
void handleMachineUsage() {

  unsigned long now = millis();

  if ((now - usageLastTickMs) >= 1000UL) {

    uint32_t elapsedSeconds = (uint32_t)((now - usageLastTickMs) / 1000UL);
    usageLastTickMs += (unsigned long)elapsedSeconds * 1000UL;

    usagePowerOnSeconds += elapsedSeconds;
    if (currentMachineState == RUN)
      usageRunSeconds += elapsedSeconds;
    if (isSpindleSpinning())
      usageSpindleSeconds += elapsedSeconds;

  }

  if (sdCardManager.connected && (now - usageLastFlushMs) >= USAGE_FLUSH_INTERVAL_MS) {
    usageLastFlushMs = now;
    saveMachineUsage();
  }

}

// Builds the hidden log file name for a given 1-based job number, e.g. job 1 ->
// ".jobs_1-100", job 150 -> ".jobs_101-200". Each file holds exactly
// JOBS_PER_LOG_FILE jobs, so reads/writes touch only a small (<=~12 KB) file.
void jobLogFileNameForJob(uint32_t jobNumber, char* out, size_t outSize) {

  if (jobNumber < 1)
    jobNumber = 1;

  uint32_t block = (jobNumber - 1) / JOBS_PER_LOG_FILE;
  uint32_t rangeStart = block * JOBS_PER_LOG_FILE + 1;
  uint32_t rangeEnd = rangeStart + JOBS_PER_LOG_FILE - 1;

  snprintf(out, outSize, "%s%lu-%lu", JOB_LOG_FILENAME_PREFIX, (unsigned long)rangeStart, (unsigned long)rangeEnd);

}

// Appends one job-history line "<startTime>,<jobFileName>" to the hidden rotated log
// file for this job (full history is preserved across files). startTime is supplied
// by the client (no onboard RTC); it is stored verbatim and may be empty. The comma
// after startTime is the single field separator — readers split on the first comma so
// a comma in the file name is preserved. Called BEFORE usageJobCount is incremented,
// so usageJobCount + 1 is this job's 1-based number.
void logJobLaunch(const char* startTime) {

  if (!sdCardManager.connected)
    return;

  char fileName[JOB_LOG_FILENAME_CHARS];
  jobLogFileNameForJob(usageJobCount + 1, fileName, sizeof(fileName));

  if (!sdCardManager.openTextFileForAppending(fileName))
    return;

  sdCardManager.writeText(startTime != nullptr ? startTime : "");
  sdCardManager.writeText(",");
  sdCardManager.writeText(openedReadFileName);
  sdCardManager.writeText("\n");
  sdCardManager.closeWriteFile();

}

void handleRGBLed() {

    if (isProcedureBusy()) {
      setLEDColor(0, 255, 0);  // Public busy state: show the same green as RUN.
      return;
    }

    // Every pause/resume phase is a firmware-managed "Hold"-like state: paused, mid
    // pause-feed-drop, or mid resume ramp. Drive the LED yellow for all of them rather than
    // from the raw parsed GRBL state, which can read a stale/transient ALARM across these
    // transitions (status polling is gated out while paused, and on resume isJobPaused clears
    // before a fresh Run status arrives) and would otherwise flash the LED red even though the
    // job is correctly held/resuming.
    if (isJobPaused || isPauseFeedDropActive
        || isResumeSpindleOverrideRampActive || isResumeFeedRampActive) {
      setLEDColor(255, 255, 0);  // Yellow color (Hold / pausing / resuming).
      return;
    }

    // Control the LED based on the machine state
    switch (currentMachineState) {
      case IDLE:
        setLEDColor(0, 0, 255);  // Blue color
        break;
      case HOLD:
        setLEDColor(255, 255, 0);  // Yellow color
        break;
      case ALARM:
        setLEDColor(255, 0, 0);  // Red color
        break;
      case RUN:
      case JOG:
        setLEDColor(0, 255, 0);  // Green color (jogging shares the same green as RUN)
        break;
      case DOOR:
        setLEDColor(255, 165, 0);  // Orange color
        break;
      case UNKNOWN:
        setLEDColor(255, 255, 255);  // White color
        break;
    }
    
}
  

void blinkLed() {

  if(timerUtil.blinkLedTimer()) {

    if (ledStatus) {

      digitalWrite(BLINK_LED_PIN, HIGH);
      ledStatus = false;

    } else {

      digitalWrite(BLINK_LED_PIN, LOW);
      ledStatus = true;

    }
  }
}

void resetESP32InitialConfiguration() {

  initialESP32ExpectedResponses = wifiSettings.hotspotEnabled ? 6 : 5;

  // If an AT command is in-flight (e.g. BLE scan), abort it now
  // so sendATCommand() is not silently blocked throughout the re-init sequence.
  esp32c3Manager.abortPendingCommand();

  atRequested = false;
  wsLink1CloseRequested = false;
  accessPointModeRequested = false;
  startAccessPoint = false;
  mobileWebsocketConfigured = false;
  clientWiFiConnectionAttempted = false;
  initialESP32C3Config = false;
  isMobileWebSocketConnected = false;
  isMobileWebSocketConnecting = false;
  isClientWiFiConnected = false;
  isBleActive = false;

  resetMobileBleScanState();

  initialESP32ResponsesReceived = 0;

}
