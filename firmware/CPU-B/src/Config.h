#ifndef CONFIG_H
#define CONFIG_H

#include <stdint.h>

//PCB
// Installed PCB revision: set exactly one to true. Usable in #if (true/false evaluate to 1/0 in C++).
#define PCB_VERSION_4_7 false
#define PCB_VERSION_4_8 true

#if PCB_VERSION_4_7 == PCB_VERSION_4_8
  #error "Exactly one of PCB_VERSION_4_7 / PCB_VERSION_4_8 must be true"
#endif

//DEBUG
#define DEFAULT_DEBUGGING_BASE 0
#define DEFAULT_DEBUGGING_SPINDLE 0
#define DEFAULT_DEBUGGING_STATUS 0
#define DEFAULT_DEBUGGING_MACRO_BUFFER 0
#define DEFAULT_DEBUGGING_SD_CARD 1
#define DEFAULT_DEBUGGING_JSON 1
#define DEFAULT_DEBUGGING_NETWORK 1
#define DEFAULT_DEBUGGING_EEPROM 1
#define DEFAULT_DEBUGGING_JOB 0
#define DEFAULT_BENCHMARKING 0
#define DEFAULT_BENCHMARK_MINIMUM_MS 1


typedef struct {

  bool debuggingBase;
  bool debuggingSpindle;
  bool debuggingStatus;
  bool debuggingMacroBuffer;
  bool debuggingSDCard;
  bool debuggingJson;
  bool debuggingEEPROM;
  bool debuggingJob;
  bool benchmarking;
  int benchmarkMinimumMs;
  char checkChar = '\0'; //this char is used to check if the struct has been initialized or not

}DebuggingSettings;



//GRBL
#define GRBL_RESET_STRING "Grbl 1.1"
#define GRBL_DEFAULT_HOMING true
#define GCODE_RAPID_FEED_RATE_MM_PER_MIN 2000.0f
#define DRY_RUN_FEED_RATE_MM_PER_MIN 3000
#define MAX_WORK_DIMENSION_XY_MM 150.0f
#define MAX_WORK_DIMENSION_Z_MM 70.0f
#define SOFT_LIMIT_MIN_X_MM 0.0f
#define SOFT_LIMIT_MAX_X_MM 143.0f
#define SOFT_LIMIT_MIN_Y_MM -145.0f
#define SOFT_LIMIT_MAX_Y_MM 0.0f
#define SOFT_LIMIT_MIN_Z_MM -70.0f
#define SOFT_LIMIT_MAX_Z_MM 0.0f
#define AUTO_HOMING_ON_STARTUP false
#define AUTO_HOMING_DELAY_MS 1000

//SERIAL
#define SERIAL_CONNECTION_TIMEOUT 2000
#define SERIAL_BUFFER_SIZE 5000
#define USB_BAUD_RATE 2000000
#define CPUA_BAUD_RATE 115200
// 2 Mbaud. The link is a short PCB trace through the 10R series resistors R90/R91, so
// signal integrity is a non-issue at a 500 ns bit time, and the RP2350 PL011 divides
// clk_peri down to this cleanly (max is clk_peri/16, several Mbaud).
#define CPUC_BAUD_RATE 2000000

// CPU-C link: UART1 on GP4 (TX) and GP5 (RX), reaching CPU-C's GP1 (RX) and GP0 (TX)
// through the 10R series resistors R90/R91.
// Serial2 defaults to GP8/GP9 on the Arduino-Pico core, and on this board those are the
// RS-422 transceiver lines GM490_DI/GM490_DO -- driving them would put two drivers on one
// net -- so the pins MUST be set before begin().
// NOTE: the schematic marks GP36/GP37 (the SDS011 dust sensor) as UART1 as well. Only one
// of the two can be the hardware UART1; this link claims it, so the dust sensor will need
// a PIO software serial port.
#define CPUC_UART_TX_PIN 4
#define CPUC_UART_RX_PIN 5
// Serial2 drove an external WiFi module. Not fitted on this board:
// GP8/GP9 are the RS-422 transceiver GM490_DI/GM490_DO.
// The distance laser sensor is not fitted either, and its old UART pins
// GP4/GP5 are the serial link to CPU-C. Its direction pin is parked on a
// spare so nothing drives SPI1_SCK_B.
#define LASER_SENSOR_OLD 0
#define LASER_SENSOR_NEW 1

// Settle time around the RS485 direction switch. SoftwareSerial writes block, so the last bit is
// already out when postTransmission runs; on a HardwareSerial port a flush() would be required there
// first or the CRC bytes get cut off.
// Laser edge probe — coarse scan (streaming G90 increments)
// Laser edge probe — fine scan (single continuous slow pass with feed-hold stop)

// Height map (laser-based, relative measurements)
//
// LINE SCAN is the active acquisition mode: one G1 per grid row while the laser is polled
// reinstate the old point-by-point scan (stop, settle, average 3 reads); both code paths are kept in

// Coarse/fine now set the ROW PITCH (Y) and the cell width (X). The sample interval ALONG a row is
// set by the Modbus read rate, not by this: ~0.075 mm at 300 mm/min with a ~15 ms read, so every cell
// collapses tens of independent reads rather than the 3 the legacy scan took.
// Sized for the machine envelope (SOFT_LIMIT_MAX_X_MM 143 mm, -SOFT_LIMIT_MIN_Y_MM 145 mm) at the
// finest pitch: ceil(145 / 2) + 1 = 74 points per axis. 96 leaves headroom down to a ~1.5 mm pitch and
// have) cost 156 KB of permanently reserved RAM.
// Positioning feed rate between rows and for the return move.
// Row scan feed rate. Deliberately slow: the along-row sample spacing is (read time * feed), so this
// is what buys the samples each cell averages over. At 200 mm/min and a ~19 us Modbus read that is
// ~0.065 mm between samples, about 30 per 2 mm cell. Lower it further to trade scan time for a
// quieter map; the scan duration scales inversely.
// Per-cell statistics. Samples binned into one grid cell are sorted, trimmed by
// makes a cell stable: a specular glint or a dropout lands at an extreme and is discarded outright,
// where a plain mean would drag the cell toward it and lift the tool off the work.
// valid neighbours over this many propagation passes. The legacy scan instead copied the previous
// point's value, which silently produced a flat patch that looked perfectly plausible in the 3D view;
// How far the job XY zero may move before the map is dropped as stale. The map is anchored in job
// coordinates, so any real re-zero slides it off the material; this only exists to absorb the noise
// floor of re-parsing the same WCO out of consecutive GRBL status reports, which is none in practice.
// How far the job's toolpath may poke outside the scanned rectangle before startJob refuses to run
// with compensation. Only absorbs float rounding: when the map is anchored on the file's own bounds
// the two rectangles match exactly, and any real gap means the map is over the wrong area.
// A scan refuses to start unless its first reading sits this close to the middle of the sensor's
// range. The sensor spans about +/-30 mm and the reading tracks Z about 1:1, so requiring
// |reading| <= 25 leaves ~5 mm of headroom at BOTH ends for the surface's own variation. Scanning
// near a range limit is what makes isolated cells read wildly wrong while the rest of the map looks
// perfectly fine -- with Z homed the sensor already reads -25.4, i.e. straight at the edge.
// To satisfy it, jog Z until the laser reads near 0 (roughly 25 mm down from Z home) before scanning.
// A cell whose post-trim spread exceeds this is counted as noisy and reported. A cell spans only a
// couple of mm, so real surface variation inside one is microns; anything this wide is the sensor
// failing to resolve that spot. The COUNT is what matters diagnostically: a handful of noisy cells
// clustered at the scan edge is the scan overhanging the stock, while noisy cells spread across the
// middle is the sensor struggling with the material itself.
// The Z datum is measured STANDING STILL at job XY zero before the sweep starts, this many reads
// It used to be taken from the map's own origin cell, which is a half-width row end carrying about
// half the samples of any other cell -- the weakest cell in the map, acting as the reference for
// every value in it. Measuring at job zero also means Z is touched off at job zero, where the
// operator naturally would, instead of at a corner they have to look up.

// Position tolerance (mm) for confirming the head has ARRIVED before sampling / before starting a row.
// Guards against acting on a stale "Idle" status; in line scan mode a stale Idle would start the row
// timing clock before the head is at the row start and skew every dead-reckoned X in that row.
// Post-scan outlier filter: a cell that deviates from its 8-neighbour median by more than this is
// treated as a spike (reflective copper, trace edge, bad read) and replaced with that median.
// Deliberately loose. Spike rejection has moved INTO the cell: every cell is already a trimmed mean of
// tens of readings, which discards glints and dropouts at the source, where this filter can only
// second-guess a finished cell against its neighbours and would flatten real surface features
// (steps, edges, wood grain, a warped board's curvature) if set near the noise floor. At 0.5 mm it
// only catches a catastrophically wrong cell that survived the per-cell trim. Lower it towards
// ~0.05 mm to have it police the map aggressively; set to 0 to disable the filter entirely.

// Point cloud scan (laser-based, absolute work coordinates saved to SD as PLY)
// Row sweep feed rate. Sets the along-row point spacing, since that is (Modbus read time * feed):
// ~0.065 mm at 200 mm/min. Lowered 300 -> 200 to match the height map sweep and give a denser, quieter
// cloud; scan duration scales inversely.

//timers
// Watchdog window for the USB serial client. ANY incoming serial byte resets it
// (readFromUSB refreshes serialLastApiPingMs on every byte), so gcode/status polls/
// commands all count as keep-alive, not just SERIAL_PING. Raised 2500->10000 so a
// quiet gap (SD-card job with no polling, or a slow move while the streamer waits for
// buffer space) no longer trips a spurious safety pause.
#define SERIAL_API_PING_TIMEOUT_MS 10000
#define WEBSOCKET_API_PING_TIMEOUT_MS 5000
// Press-and-hold jogging keep-alive: the app streams incremental "jog" steps while the
// button is held and sends "jogCancel" on release. If no jog activity is seen for this
// long (lost release message or dropped link), the firmware auto-cancels the jog (0x85).
// Keep this comfortably above the app's jog-step streaming interval to avoid false stops.
#define JOG_KEEPALIVE_TIMEOUT_MS 350
#define FILE_TRANSFER_CHUNK_TIMEOUT_MS 5000
#define FILE_DOWNLOAD_CHUNK_TIMEOUT_MS 25000
// startFileDownload only counts the file's lines (for totalFileLines) when the file is at most this
// big. Counting reads the whole file in one blocking loop (about 2 s per MB), during which USB and
// WebSocket are not serviced; 32 KB keeps that under ~70 ms. Bigger files reply with
// totalFileLines = 0 (unknown) and clients use downloadedBytes / fileSize for progress instead.
#define DOWNLOAD_LINE_COUNT_MAX_FILE_SIZE_BYTES 32768L
// How many times a download chunk's SD read is retried before the transfer is
// aborted. This covers transient SD glitches and is safe because readDownloadChunk
// only fails before advancing the file position.
#define DOWNLOAD_CHUNK_READ_MAX_ATTEMPTS 3
// On a WebSocket send failure the chunk is NOT re-sent in place (that handshake is
// non-idempotent and re-issuing it back-to-back desyncs the link). Instead the
// download file is rewound to the start of the chunk so the client can re-request
// it on a fresh call. This caps how many consecutive send failures are tolerated for
// the same chunk before the whole transfer is abandoned, to bound a dead link.
#define DOWNLOAD_CHUNK_SEND_MAX_FAILURES 5

//BUFFERS
#define LINE_BUFFER_CHARACTERS 5000
// Longest line expected from CPUA. grblHAL status reports with 4 axes go past 100 characters when
// the periodic WCO:/Ov: fields are appended, so keep plenty of margin.
#define CPUA_LINE_BUFFER_CHARACTERS 256
#define MACRO_BUFFER_LINES 100
#define MACRO_LINE_BUFFER_CHARS 200
#define MACRO_SPEED_SLOW "G1 F250"
#define MACRO_SPEED_MEDIUM "G1 F500"
#define MACRO_SPEED_FAST "G1 F1000"
#define FILE_CHUNK_LINES_BUFFER 5000
#define DOWNLOAD_FILE_CHUNK_BUFFER 1500



//EEPROM
#define EEPROM_SIZE 2048

//NETWORK
// This machine has no radio. The network transport is the W5500 Ethernet
// controller on SPI0, which is not implemented yet.
//
// runtime: every EEPROM region is addressed as a running sum that starts with
// block and invalidate what is already stored on a board. It is the natural
// place for the Ethernet settings (DHCP, static IP, port) when those land.
#define NETWORK_CHECK_DELAY 100



//Sync actions
//definition of the struct that will keep track of the sync actions
#define SYNC_ACTION_CHARS 50

typedef struct {

  char type[SYNC_ACTION_CHARS];
  float positionX;
  float positionY;
  float positionZ;
  float positionRange;
  int pollingInterval;

} SyncAction;

//CPUA
#define DEFAULT_ENABLE_CPUA_PASSTHROUGH true
#define DEFAULT_INITIAL_HOMING false

//IO - Control Board V2 CPU-B.
// The RGB LED is on GP31/GP32/GP33 here (LED_R, LED_G, LED_B).
#define RED_LED_OUTPUT_PIN 31
#define GREEN_LED_OUTPUT_PIN 32
#define BLUE_LED_OUTPUT_PIN 33
#define BLINK_LED_PIN 25
// The axis enable mirrors, the laser sensor enable and the input monitor
// belong to CNC router hardware that this machine does not have. They are
// parked on pins the Control Board V2 leaves spare (BGPn / GPIOn_B) so the
// code that still writes them cannot fight the Ethernet, SPI1 or RS-485
// nets that sit on their original pins.
#define ENABLE_X_PIN 41
#define ENABLE_Y_PIN 3
#define ENABLE_Z_PIN 10
#define ENABLE_A_PIN 11
#define LASER_SENSOR_ENABLE_PIN 43
#define SPINDLE_RLY_MIRROR_PIN 7
#define POWER_RLY_MIRROR_PIN 6
#define INPUT_MONITOR_PIN 45


// Door/lid sensor logic levels (active-high on OPEN): the input reads LOW when the door
// is CLOSED and HIGH when the door is OPEN. Configured as INPUT_PULLUP: a closed door holds
// the pin LOW to GND (default/idle state); opening releases it and the internal pull-up
// drives it HIGH (the trigger).
// Debounce (ms): the door input must read OPEN this long before it counts, to reject brief
// electrical glitches (e.g. spindle inrush) that previously caused spurious pauses on this pin.

//Spindle
// see enum SpindleType in Constants.h). Exposed read-only through the JSON API
// (getSpindleType); the calling application takes further actions based on it.

// Read-only G53 Y target used by the move-to-rotary-position macro, per spindle variant.
#define MOVE_TO_ROTARY_Y_24V -60.78f
#define MOVE_TO_ROTARY_Y_48V -55.442f  // Brushed 48V

//Current sensor (INA220/INA219 compatible)
#define CURRENT_SENSOR_SDA_PIN 12
#define CURRENT_SENSOR_SCL_PIN 13
#define CURRENT_SENSOR_I2C_ADDRESS 0x40

//TIMINGS
#define START_DELAY 1500 //this delay is needed to allow other devices of the PCB to boot correctly
#define LASER_SENSOR_ENABLE_RESPONSE_DELAY_MS 1500
#define LED_BLINK_INTERVAL 750
#define ASK_STATUS_INTERVAL 200

//Probe Z0 macro (probeSetJobZ0Macro)
//Default touch-point offset: the probed surface is defined as this work Z (touch
//this define is only the factory default used when nothing is stored yet.
//After probing, the macro retracts to (offset + this margin) mm.

//Spindle
// Floor for the spindle OVERRIDE, in RPM of actual spindle speed.
//
// GRBL scales the programmed S word by the override percentage and then clamps the result to $31
// (1500 rpm as this machine's CPUA is built), which is well under the speed this spindle holds
// cleanly. It does not stall politely down there -- it loses torque, and the first place that shows
// is the cut. An override step that would land under this is therefore refused outright, so the
// override cannot take the spindle below it whatever S the job asked for.
//
// The floor is a percentage that MOVES with the job: at S12000 the override may go down to 25%, at
// S4000 only to 75%, and at or below this RPM it may not be reduced at all.
//
// This governs the OVERRIDE only. A job that programs S below this still runs at what it asked for
// -- that is the job's decision and GRBL's $31 clamp; the override simply cannot make it worse.
// Spindle recycle step pacing: the DROP (down to floor) uses a shorter interval, the
// RAMP (back up to target) a longer one.
// On resume: motion restarts ('~') and the spindle restarts at full at once,
// then the override is paced DOWN to this floor and back UP to the captured
// target ("recycle"), so the override ends explicitly driven to 100%.
// After the recycle ramp reaches the target override, the resume enters a closed-loop
// CONFIRM stage: it verifies GRBL actually reports the spindle override at target (the
// "Ov:" status field) and, if GRBL is short/over by a full coarse step (a coalesced or
// dropped override byte), nudges it back toward target. Once GRBL is read at target, the
// resume waits this settle time before completing, so it ends reliably at max speed.
// Safety bound so CONFIRM can never hang if GRBL never reports the target (e.g. it stops
// emitting the "Ov:" field). After this long the resume completes anyway.
// Resume feed (motion) ramp: the spindle takes time to reach RPM after a resume, so motion
// must not cut at full feed meanwhile. grblHAL does NOT honor feed-override changes during
// HOLD, but it DOES honor them while moving, and the value persists through a hold. So the
// API pause ramps the feed override DOWN to the floor BEFORE holding; motion then resumes at
// that low feed and this ramp brings it back up while the spindle spins up. (If feed is not
// already low at resume — e.g. a door/watchdog pause that held immediately — the resume ramp
// drops it first, then ramps up, as a fallback.)
#define RESUME_FEED_START_PERCENT 10     // feed override floor; the API pause drops to this BEFORE holding
#define RESUME_FEED_DROP_STEP_MS 20      // fast pacing of the feed-override drop (one -10% per step)
#define RESUME_FEED_RAMP_TOTAL_MS 6000   // time to ramp feed override from floor back up to target
// A spindle ramp triggered by an M3 IN A JOB first lets GRBL's planner drain, so the machine is
// stopped while the spindle comes up and motion only restarts once it is at speed. An "ok" means a
// line was accepted, not executed, so without this the queued moves keep running through the ramp.
// Bounded: the job is held while it waits, so a machine that never reports Idle (alarm, stalled
// status feed) must not strand it -- after this long the ramp proceeds regardless.

// After the LAST ramp step is sent, hold the job for this long before letting motion resume. GRBL
// acknowledges an "M3 S..." as soon as it accepts it, but the spindle still needs physical time to
// reach that speed -- so without this the job restarted the instant the final S was queued, with the
// spindle still climbing. Raise it if the spindle is slow to settle at its top speed.


//Json
#define DEFAULT_ENABLE_JSON_RESPONSE 1
#define JSON_BUFFER_SIZE 8192

//Machine name
#define MACHINE_NAME_MAX_CHARS 30

//Tool length probe
// Reference Z: machine Z coordinate of the probe plate top surface when resting on the bed,
// measured with a zero-length reference (bare collet).

typedef struct {

  bool enableJsonResponse;
  char checkChar; //this char is used to check if the struct has been initialized or not

} JsonSettings;







//Machine usage tracking (read-only meter persisted to a hidden SD file)
// Hidden via a leading-dot filename — the SD listing API already skips dot files,
// so it never appears in the user's file list / download / delete API.
#define USAGE_LOG_FILENAME ".usage"
#define USAGE_FLUSH_INTERVAL_MS 300000UL   // persist to SD every 5 minutes
#define USAGE_RECORD_MAGIC 0x414B5553UL    // "AKUS"
#define USAGE_RECORD_VERSION 2

// Persisted usage record. The file stores two of these back-to-back (slot A/B);
// each flush writes the inactive slot so a torn write can never destroy the last
// good record. On boot the valid record (CRC32 ok) with the highest sequence wins.
typedef struct {

  uint32_t magic;            // USAGE_RECORD_MAGIC
  uint16_t version;          // USAGE_RECORD_VERSION
  uint16_t reserved;         // padding / future use
  uint32_t sequence;         // monotonic write counter; higher = newer
  uint32_t powerOnSeconds;   // total time powered on
  uint32_t runSeconds;       // total time machineStatus == Run
  uint32_t spindleSeconds;   // total time the spindle was spinning
  uint32_t jobCount;         // total number of jobs launched (lifetime)
  uint32_t crc32;            // CRC32 over all preceding bytes of this struct

} UsageRecord;

//Job history log — full history kept across multiple hidden, rotated SD files.
// Each file holds exactly JOBS_PER_LOG_FILE jobs and is named by its 1-based job
// range, e.g. ".jobs_1-100", ".jobs_101-200", ... (leading dot => hidden, excluded
// from the file-list API). Each line is "<startTime>,<jobFileName>". Keeping each
// file small bounds the FAT append cost so launches stay fast no matter how many
// jobs have run in total.
#define JOBS_PER_LOG_FILE 100
#define JOB_LOG_FILENAME_PREFIX ".jobs_"
#define JOB_LOG_FILENAME_CHARS 40
// Upper bound for one full log file: JOBS_PER_LOG_FILE * (~24 start time + ',' +
// 99 max file name + '\n'). Used to size the read buffer for getJobHistory.
#define JOB_LOG_FILE_MAX_BYTES 13000

//Sync actions, those are the actions that need to be executed in sync specific machine positions
#define SYNC_ACTION_AMOUNT 20

//SD Card - on SPI1 on the Control Board V2, with a card detect line.
// SPI0 is the W5500 Ethernet controller, so the card must not go there.
#define SD_CS_PIN 29
#define SD_SCK_PIN 26
#define SD_MOSI_PIN 27
#define SD_MISO_PIN 28
#define SD_CARD_DETECT_PIN 30
#define SD_SPI_SPEED 18000000  // 18MHz
#define SD_BUFFER_SIZE 16384    // 16KB buffer
#define SD_MAX_FILES 1000
#define SD_MAX_FILE_NAME_LENGTH 100

//Files
#define GCODE_FILE_EXTENSION "gcode"
#define NC_FILE_EXTENSION "nc"
#define CNC_FILE_EXTENSION "cnc"
#define PLY_FILE_EXTENSION "ply"


#define SUPPORTED_GCODE_EXTENSIONS_AMOUNT 4
#define SUPPORTED_GCODE_EXTENSION_CHARS 10 //including the null char at the end of the string
const char SUPPORTED_GCODE_EXTENSIONS[SUPPORTED_GCODE_EXTENSIONS_AMOUNT][SUPPORTED_GCODE_EXTENSION_CHARS] = {

  GCODE_FILE_EXTENSION,  
  NC_FILE_EXTENSION,
  CNC_FILE_EXTENSION

};

#endif