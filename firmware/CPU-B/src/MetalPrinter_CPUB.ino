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
bool enableCPUAPassthrough = DEFAULT_ENABLE_CPUA_PASSTHROUGH;
bool initialHoming = DEFAULT_INITIAL_HOMING;

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
bool isRotaryAxisConnected = false;

//mobileWebsocketConnection status

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
unsigned long pauseHoldRequestedMs = 0;
// Gentle-pause feed-drop state: the API pause ramps the feed override down to the resume
// floor WHILE STILL MOVING (grblHAL honors it out of HOLD, and it persists through the hold),
// then issues the actual feed hold. See handlePauseFeedDrop.
bool isPauseFeedDropActive = false;
unsigned long pauseFeedDropLastStepMs = 0;
bool pauseWillStopSpindle = false;
unsigned long lastGRBLStatusReceivedMs = 0;
int pauseResumeOriginalSpindleOverride = GRBL_OVERRIDE_DEFAULT;
// Last spindle override percent GRBL actually reported in its "Ov:" status field, plus
// the millis() at which it was last updated. Used by the resume CONFIRM stage to close
// the loop (the ramp itself runs open-loop on the local counter). -1 = never reported.
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
unsigned long serialLastApiPingMs = 0;
unsigned long fileTransferLastChunkActivityMs = 0;
unsigned long fileDownloadLastChunkActivityMs = 0;
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
// Set only for a ramp triggered by an M3 read from a JOB. Such a ramp holds motion until GRBL's
// planner has drained, so the spindle comes up with the machine stopped and motion only restarts once
// it is at speed. Never set for a console M3 (nothing is streaming) nor for any other path.
// The final speed has been SENT but the spindle has not yet had time to reach it. The ramp stays
// active through this, so the job stays held and the reported state stays Run.

// Modal spindle state (mirrors GRBL's modal words so the ramp engages regardless
// of how the CAM splits M3/S across lines). commandedSpindleRPM is the RPM we
// last actually sent to GRBL — the ramp base is always derived from it, never
// from status reports (which lag during a job and would cause jump starts).
bool isSpindleOnCommanded = false;              // M3 seen, cleared by M5/M30/reset/job start+stop
long commandedSpindleRPM = 0;                   // last RPM actually commanded to GRBL
// A line carrying more than spindle words (e.g. "G1 X10 S9000") cannot be consumed
// by the ramp: it is held here and released once the ramp completes.
unsigned long downloadFileTotalLines = 0;

byte bufferByteUSB = 0;
byte bufferByteCPUA = 0;
byte bufferByteCPUC = 0;
char bufferCharCPUC = NULL_CHAR;
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

DebuggingSettings debuggingSettings;
JsonSettings jsonSettings;

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
InputSource probeZ0MacroInputSource = INPUT_SOURCE_USB;
bool isRotaryYLockBypassed = false;


// Selected corner → the two edge directions to probe (X first, then Y) and the
// offset moved INTO the material along each axis before the origin is set.
// Defaults describe the bottom-left corner with zero offset.


// which since the map can be anchored at job zero is no longer necessarily the map origin.
// The work offset that was in effect when the scan started. The map is anchored in job coordinates,
// so this is what tells us later whether those coordinates still mean the same place on the material.
// Work-coord target of the in-flight scan move; WAIT_IDLE gates sampling on the head reaching it.
// Legacy point-by-point sample accumulator (averaging): reads are spaced out and averaged.
// raw per-cell trimmed-mean DISTANCE during a line scan and is re-expressed relative to the origin

// ---- Line scan acquisition state ----
// Row geometry, in the TOOL frame. The G1s add the laser offset so the LASER rides the row while the
// map stays in the tool frame, exactly as the legacy per-point scan did.
// Samples arrive in X order along a row, so leaving a column means that column is finished. Only ONE
// cell's samples are ever held in RAM; the cell is collapsed to a trimmed mean on the way out.
// clear and are filled from their neighbours at the end of the scan instead of silently inheriting the
// previous cell's value.
// Scan quality figures reported through the API.
// noisy at all, so a bad scan can be located instead of merely detected.
// Extreme raw readings seen during the scan, to show how much sensor headroom it actually had.
// can report the reading that caused it. Reset when a scan starts instead.
// Z datum, measured standing still at job XY zero before the sweep. Like the reading extremes above,
// reset when a scan starts.


//COUNTERS
int USBLinePos = 0;
int CPUALinePos = 0;
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
EEPROMManager eepromManager;
Adafruit_INA219 currentSensor(CURRENT_SENSOR_I2C_ADDRESS);





// The pin is an output held at a static level: the accessory reads it, nothing toggles it per move.

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
// rather than failing, so an uncovered job runs to completion on edge values.
//
// `known` receives false when the answer cannot be determined (no map, no file open, or no MIN/MAX
// metadata). A false return with known == false means UNKNOWN, not "does not cover".









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

// ─── Height map cell statistics ─────────────────────────────────

// order: row 0 runs +X, row 1 runs -X, and so on, which is also the order the line scan fills them in.

// Median of up to 9 floats (used by the height-map outlier filter). Sorts the array in place.




// Trimming BOTH ends is what makes a cell stable on reflective stock: a specular glint reads short and
// a dropout reads long, both land at an extreme, and both are discarded outright -- where a plain mean
// would drag the cell toward them by enough to lift the tool clear of a shallow cut. spreadOut gets the
// range of the values that survived the trim, i.e. a per-cell noise figure.

// crosses into the next column and once more when the row ends.

// Fills cells with no real measurement from their valid neighbours, propagating over several passes so
// a hole closes from its rim inwards. Returns how many cells were filled. This replaces the legacy
// "carry the previous point's value" behaviour, which silently produced a flat patch that looked
// entirely plausible in the 3D view while the tool rode over uncut material.

// Turns the raw per-cell distances left by the line scan into the finished map. Returns false, having
// already aborted the scan, if the result cannot be trusted.


// ==================== Point Cloud Scan ====================





// Returns the scan-order (serpentine) index for physical grid position (row, col)




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
  debuggingSettings.debuggingEEPROM = DEFAULT_DEBUGGING_EEPROM;
  debuggingSettings.debuggingJob = DEFAULT_DEBUGGING_JOB;
  debuggingSettings.benchmarking = DEFAULT_BENCHMARKING;
  debuggingSettings.benchmarkMinimumMs = DEFAULT_BENCHMARK_MINIMUM_MS;
  debuggingSettings.checkChar = EEPROM_CHECK_CHAR; //this char is used to check if the struct has been initialized or not, since we will read it from the EEPROM and if it's not initialized it will be filled with null chars
  
  //init default JsonSettings
  jsonSettings.enableJsonResponse = DEFAULT_ENABLE_JSON_RESPONSE;
  jsonSettings.checkChar = EEPROM_CHECK_CHAR; //this char is used to check if the struct has been initialized or not, since we will read it from the EEPROM and if it's not initialized it will be filled with null chars

  arrayUtil.clearCharArray(firmwareVersion, FIRMWARE_VERSION_CHARS);
  arrayUtil.clearCharArray(jsonResponseCharBuffer, JSON_BUFFER_SIZE); //clear the buffer for the json responses that we will send to the websocket clients, this is needed to avoid sending old data in case we have a json response that is smaller than the previous one stored in the buffer, since we will only overwrite the portion of the buffer corresponding to the new json response, if the new json response is smaller than the previous one we need to clear the buffer to avoid sending old data in the portion of the buffer that is not overwritten by the new json response
  



  //default probe height — overwritten by EEPROM if a value was saved

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
    pinMode(LASER_SENSOR_ENABLE_PIN, OUTPUT);
    digitalWrite(ENABLE_X_PIN, LOW);
    digitalWrite(ENABLE_Y_PIN, LOW);
    digitalWrite(ENABLE_Z_PIN, LOW);
    digitalWrite(ENABLE_A_PIN, LOW);
    digitalWrite(LASER_SENSOR_ENABLE_PIN, LOW);

    //Current sensor I2C init
    Wire.setSDA(CURRENT_SENSOR_SDA_PIN);
    Wire.setSCL(CURRENT_SENSOR_SCL_PIN);
    Wire.begin();
    isCurrentSensorInitialized = currentSensor.begin(&Wire);

  //Serial
  serialManager.init();

  // The distance laser sensor is not fitted on the Control Board V2 and its
  // UART pins GP4/GP5 carry the serial link to CPU-C, so it is never started
  // here. The probing routines that use it stay in place but idle.

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


  if(serialManager.connectCPUC())
    if (debuggingSettings.debuggingBase) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("CPU C Serial connected");
    }
  else
    if (debuggingSettings.debuggingBase) {
      serialManager.USBSerialPrint(FROM_CPUB);
      serialManager.USBSerialPrintln("CPU C Serial connection failed");
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
  handleSerialClientWatchdog();
  handleJogWatchdog();
  handleFileTransferTimeouts();

  readFromUSB();
  handleRGBLed();
  readFromCPUA();
  readFromCPUC();
  handleAutoHoming();
  handlePauseFeedDrop();
  handleResumeFeedRamp();
  handleMachineUsage();
  handleSyncActions();
  handleMacros();
  blinkLed();
  askGRBLStatus();
  handleJob();
  updateRotaryAxisDetection();

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

// Drains the CPU-C link and mirrors it to USB.
//
// Transport only: every byte is forwarded verbatim, with no framing or parsing, because the
// CPU-B <-> CPU-C protocol is not decided yet. CPU-A's reader is the model to follow once it
// is -- that one parses grbl's responses and drives the job state machine.
void readFromCPUC(){

  while(serialManager.availableFromCPUC() > 0){

    int readCPUC = serialManager.readFromCPUC();

    if (readCPUC == -1)
      break;

    bufferByteCPUC = (byte)readCPUC;
    bufferCharCPUC = (char)bufferByteCPUC;

    if (debuggingSettings.debuggingBase) {
      serialManager.USBSerialPrint(FROM_CPUC);
      serialManager.USBSerialPrintln(bufferByteCPUC);
    }

    serialManager.USBSerialWrite(bufferByteCPUC);

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


  }
    

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



  // If pause/hold toggled spindle stop (0x9E), untoggle it before reset so the
  // next job does not inherit a stopped spindle output state.
  if (pauseResumeSpindleRestorePending)
    serialManager.CPUASerialWrite(static_cast<byte>(GRBL_OVERRIDE_SPINDLE_STOP_TOGGLE));

  // A stop ABANDONS the job, so drop the virtual-door latch and push the release BEFORE the soft
  // reset. The latch exists to survive grblHAL stopping the spindle during parking, but it must
  // not survive a stop: grblHAL re-evaluates the door signal as it restarts, and with the door
  // still asserted it comes back up in STATE_SAFETY_DOOR ([MSG:Check Door]) instead of Idle — the
  // job would never stop. Releasing first means grbl restarts with the door closed.
  // re-asserts on the next pass — correct, since motion is not permitted in that configuration.

  serialManager.CPUASerialWrite(GRBL_STOP_RESET);

  isJobRunning = false;
  isDryRunActive = false;
  pendingDryRunFeedRate = false;
  isJobPaused = false;
  isResuming = false;
  pauseResumeSpindleRestorePending = false;
  pauseHoldRequestedMs = 0;
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
  isRotaryYLockBypassed = false;
  waitForOk = 0;
  jobStartMs = 0;
  jobLineIndex = 0;
  // Keep the height map on stop: the file stays open (rewound below), so the map remains valid and
  // bound to it. Only clear the "used" flag so compensation re-applies when this same file is run again.
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
//
// GRBL scales the PROGRAMMED S word by the override (spindle_set_rpm: `rpm *= 0.01f * override`),
// so the floor is a percentage of whatever the job asked for and moves with it.
//
// Read from commandedSpindleRPM -- what was last actually sent to GRBL, which is the same number
// GRBL scales. Deliberately NOT currentSpindleRPM: that is GRBL's already-overridden report coming
// back in the status line, so using it would compound the override with itself, and it lags during
// a job (status polling is throttled while streaming).

// Where a spindle-override step lands, or -1 when it is refused for taking the spindle below the
// floor. `delta` is signed: -GRBL_OVERRIDE_COARSE_STEP for a coarse decrease, +1 for a fine
// increase. Increases are never refused -- the floor is a floor, not a range.

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
  serialManager.CPUASerialWrite(static_cast<byte>(GRBL_SAFETY_DOOR));
  currentMachineState = HOLD;     // snappy UI; grbl reports Door, masked to Hold on parse
  isJobPaused = true;
  isResuming = false;             // a new pause supersedes any in-progress resume


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



// Starts the resume spindle "recycle". The caller resumes motion ('~') and
// restarts the spindle at full (untoggle 0x9E) FIRST — GRBL-HAL applies spindle
// overrides only out of HOLD — then this runs as a paced, single real-time-byte-
// per-step state machine driven by handleResumeSpindleOverrideRamp:
//                            override ends explicitly driven to 100%.
// Real-time override bytes are NEVER sent back-to-back: GRBL-HAL coalesces identical
// override requests that arrive within one realtime-processing pass, so a tight burst
// silently collapses into a single step and the local counter drifts. Hence the paced

// Coordinates the single resumeCompleted event across the two parallel resume machines
// (spindle recycle + feed ramp). Fires exactly once, only after BOTH have gone idle.
void maybeCompleteResume() {
  if (resumeCompletionPending && !isResumeFeedRampActive) {
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


// True when the line consists ONLY of spindle words (M3 and/or S, plus optional
// N line numbers, whitespace and comments). Such a line can be consumed outright —
// the ramp's own "M3 S..." steps replace it. Any other content (motion, feed,
// other M codes) means the line must be forwarded, or held and released after the
// ramp so nothing is lost.

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

// Releases a CONSOLE line that was held while the spindle ramped (job-held lines
// are released inside handleJob so they go through the full job pipeline). Sent
// through the same soft-limit check as any other passthrough line.

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

  // Spindle-start gate: never forward a line that would make the spindle turn while the job
  // is paused. The whole line is
  // dropped rather than stripped — a console line mixing motion with M3 is unsafe as a unit.
  if (isSpindleStartBlocked() && lineCommandsSpindleOn(lineBuffer)) {

    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrint("Spindle-on line dropped (lid open or job paused): ");
    serialManager.USBSerialPrintln(lineBuffer);
    return;

  }


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




// ─── Height map Z compensation ─────────────────────────────────────────────


// Post-scan spike removal: any cell that deviates from its 8-neighbour median by more than
// is replaced with that median. This stops the compensation from lifting the tool off the work at
// those cells while leaving the real (smooth) surface untouched. In-place, single pass — fine for
// isolated spikes, which is the case on a flat PCB. Disabled when the threshold is 0.


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


// Emit the next G1 sub-move for a segmented linear G1 command.

// Emit the next G1 sub-move for a segmented G2/G3 arc command.

// Check whether the given line starts a long G1 or a G2/G3 arc, and if so
// activate the corresponding segmentation state for height-map Z compensation.
// Returns true when segmentation starts; caller must NOT also send the original

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

    return;
  
  if(waitForOk > 0)
    return;


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
      ";OISK_BOUNDS EXACT=%d UNITS=MM MINX=%.3f MAXX=%.3f SIZEX=%.3f MINY=%.3f MAXY=%.3f SIZEY=%.3f MINZ=%.3f MAXZ=%.3f SIZEZ=%.3f DURATION_SEC=%lu",
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
      ";OISK_BOUNDS EXACT=0 UNITS=MM EMPTY=1 DURATION_SEC=%lu",
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

// Single point where a JSON response leaves CPU-B. The transport is chosen
// from currentInputSource, so adding the W5500 Ethernet path later means
// adding a branch here and nothing else.
bool sendJsonResponse() {

  switch (currentInputSource) {

    case INPUT_SOURCE_USB:
    default:
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

      wantedSpindleRPM = 0;

  }

  subString = NULL;
  subString = strstr(USBLineArray, S);
  
  if (subString != NULL)
    sCommandPresent = true;

  if (subString != NULL && spindleEnabled) {

    ++subString;
    wantedSpindleRPM = atoi(subString);

      wantedSpindleRPM = 0;

  }

  //set here the start of the ramping if a command is received!
    startRamping = true;

  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrint("Is spindle enabled: "); }
  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrintln(spindleEnabled); }

  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrint("Set spindle RPM: "); }
  if (DEBUGGING_SPINDLE) { serialManager.USBSerialPrintln(wantedSpindleRPM); }


  */
  
  
  
}


void handleCPUALine() {

  if (debuggingSettings.debuggingBase) { serialManager.USBSerialPrintln("Line read from CPUA"); }

  //first of all we check if the line contains a reset report, in that case we reset wait for ok
  if (grblUtil.isResetReport(CPUALineArray)){

    waitForOk = 0;
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

// Sends a host JSON line on to CPU-C untouched.
//
// The raw text is forwarded rather than a re-serialised copy, so CPU-C sees exactly what the
// host wrote and CPU-B needs no knowledge of CPU-C's command set. CPU-C's reply travels back
// through readFromCPUC(), which mirrors it to USB; every CPU-C response carries
// "from":"cpuc" so the host can tell the two processors apart.
void forwardJsonToCPUC(const char* line){

  serialManager.CPUCSerialPrintln(line);

  if (debuggingSettings.debuggingJson) {
    serialManager.USBSerialPrint(FROM_CPUB);
    serialManager.USBSerialPrint("forwarded to CPU-C: ");
    serialManager.USBSerialPrintln(line);
  }

}

void handleJson(){

    //routing: decide whether this message is ours at all, before any other parsing
    if(!receivedJsonBuffer[JSON_KEY_TARGET_STR].isNull()){

      const char* target = receivedJsonBuffer[JSON_KEY_TARGET_STR];

      if(strcmp(target, JSON_VALUE_TARGET_CPUC_STR) == 0){

        forwardJsonToCPUC(USBLineArray);
        return;

      }

      if(strcmp(target, JSON_VALUE_TARGET_CPUB_STR) != 0){

        if(jsonSettings.enableJsonResponse){

          clearResponseJsonBuffer();
          responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
          responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_TARGET_STR;
          serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
          sendJsonResponse();

        }

        return;

      }

      //"cpub" falls through to the local handling below

    }


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


      // Clear any leftover outstanding-ok count from a prior operation. If
      // waitForOk leaked > 0 (e.g. an aborted probe/scan or a lost status ok),
      // handleJob() would stall at the start
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
      // starts from a clean override. The modal spindle memory (M3 flag,
      // memorized S, held line) is also cleared so nothing leaks from the
      // console or a previous file into this job.
      resetSpindleModalState(false);
      pauseResumeSpindleRestorePending = false;
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
  

  // Release grblHAL's virtual safety door before the cycle start — while it is asserted grblHAL
  // will (correctly) refuse to restore. The door check above already guarantees the lid is closed,
  // so this only forwards a state change the next loop pass would have sent anyway; doing it here
  // removes the one-loop window between closing the lid and pressing resume.

  // ── RESUME offloaded to grblHAL (safety-door parking). ──
  // grblHAL is parked; a cycle-start ('~') restarts the spindle (with a spin-up dwell) and
  // plunges Z back to the resume position. The old CPUB resume (spindle recycle + feed ramp)
  // is disabled below.
  serialManager.CPUASerialWrite(GRBL_RESUME);


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


    if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_RESET_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_RESET;
      spindleOverride = GRBL_OVERRIDE_DEFAULT;
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_COARSE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_INCREASE_COARSE;
      spindleOverride = min(GRBL_OVERRIDE_MAX, spindleOverride + GRBL_OVERRIDE_COARSE_STEP);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_COARSE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_DECREASE_COARSE;
      spindleOverride = max(GRBL_OVERRIDE_MIN, spindleOverride - GRBL_OVERRIDE_COARSE_STEP);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_INCREASE_FINE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_INCREASE_FINE;
      spindleOverride = min(GRBL_OVERRIDE_MAX, spindleOverride + 1);
    }else if(strcmp(action, JSON_VALUE_OVERRIDE_ACTION_DECREASE_FINE_STR) == 0){
      overrideByte = GRBL_OVERRIDE_SPINDLE_DECREASE_FINE;
      spindleOverride = max(GRBL_OVERRIDE_MIN, spindleOverride - 1);
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


  default:

      if(jsonSettings.enableJsonResponse){

        clearResponseJsonBuffer();
        responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_FAILURE_STR;
        responseJsonBuffer[JSON_KEY_ERROR_STR] = JSON_VALUE_ERROR_UNKNOWN_JSON_COMMAND_TYPE_STR;
        serializeJson(responseJsonBuffer, jsonResponseCharBuffer, JSON_BUFFER_SIZE);
        sendJsonResponse();

      }

    break;

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
    // SD card initialised at boot (it is not retried later, so false means "power cycle with a card in").
    responseJsonBuffer[JSON_KEY_SD_CARD_CONNECTED_STR] = sdCardManager.connected;
    responseJsonBuffer[JSON_KEY_ROTARY_AXIS_CONNECTED_STR] = isRotaryAxisConnected;
    responseJsonBuffer[JSON_KEY_HOMED_SINCE_POWER_ON_STR] = homedSincePowerOn;

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



  case GET_FIRMWARE_VERSION:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_FIRMWARE_VERSION_STR] = firmwareVersion;
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



  case GET_SPINDLE_TYPE:

    if(jsonSettings.enableJsonResponse) {

      clearResponseJsonBuffer();
      responseJsonBuffer[JSON_KEY_RESULT_STR] = JSON_VALUE_RESULT_SUCCESS_STR;
      responseJsonBuffer[JSON_KEY_SPINDLE_TYPE_STR] =
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

  // Every procedure this tracked (height map, point cloud, laser edge/bounds probing)
  // was removed with the rangefinder stack, so no long-running procedure exists on CPU-B.
  return false;

}

// Door sensor reads HIGH when the door is open, LOW when closed (see Config.h).

// True when the spindle is (or is being ramped) at or above the minimum running
// speed. Uses the same reference as the pause spindle-stop decision so the two stay
// consistent. NOT for the door interlock — see isSpindleOnAtAnySpeed().
bool isSpindleSpinning() {

  long spindleReferenceRPM = currentSpindleRPM;
  if (commandedSpindleRPM > spindleReferenceRPM)
    spindleReferenceRPM = commandedSpindleRPM;

  // No minimum-RPM floor any more: that was the 48 V BLDC spindle. On this board the
  // grblHAL spindle output is Laser#1, where any non-zero S is genuinely "on".
  return spindleReferenceRPM > 0;

}

// True when the spindle is turning (or ramping up) at ANY speed. The door interlock has no
// "slow enough to be safe" threshold: a tool spinning at 500 rpm with the lid open is still
// a spinning tool, so the interlock uses this instead of isSpindleSpinning(), which applies
bool isSpindleOnAtAnySpeed() {

    return true;

  return currentSpindleRPM > 0 || commandedSpindleRPM > 0;

}

// Central spindle-start gate: nothing that would make the spindle (Laser#1) turn on may reach
// GRBL while the job is paused -- the machine is parked and it must only come back on resume.
// The lid/housing half of this gate went away with the door interlock.
bool isSpindleStartBlocked() {

  return isJobPaused;

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
 || isResumeFeedRampActive) {
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

