# Metal 3D Printer CPU-B — JSON API Documentation

JSON API of CPU-B on the Metal 3D Printer Control Board V2. This document reflects the currently
implemented behaviour. CPU-B is the supervisor: it owns the host USB connection, the microSD card,
the UART link to grblHAL on CPU-A, and the UART link to CPU-C (galvo and fiber laser).

**Source of truth:** [src/MetalPrinter_CPUB.ino](src/MetalPrinter_CPUB.ino), [src/Constants.h](src/Constants.h), [src/util/JSonUtil.cpp](src/util/JSonUtil.cpp)

## Contents

- [1. Transport and framing](#1-transport-and-framing)
- [2. Common JSON envelope](#2-common-json-envelope)
  - [2.1 Routing: reaching CPU-C](#21-routing-reaching-cpu-c)
- [3. JSON request keys](#3-json-request-keys)
- [4. Top-level ping messages](#4-top-level-ping-messages)
  - [4.1 `serialPing`](#41-serialping)
  - [4.2 `websocketPing`](#42-websocketping)
- [5. COMMAND API (type = command)](#5-command-api-type--command)
  - [5.1 `enableCPUAPassthrough`](#51-enablecpuapassthrough)
  - [5.2 `disableCPUAPassthrough`](#52-disablecpuapassthrough)
  - [5.5 `moveToRotaryPosition`](#55-movetorotaryposition)
  - [5.5.1 `installRotary`](#551-installrotary)
  - [5.5.2 `frameJob`](#552-framejob)
  - [5.6 `returnJobXY0Macro`](#56-returnjobxy0macro)
  - [5.7 `openGcodeFile`](#57-opengcodefile)
  - [5.8 `closeGcodeFile`](#58-closegcodefile)
  - [5.9 `deleteGcodeFile`](#59-deletegcodefile)
  - [5.10 `renameGcodeFile`](#510-renamegcodefile)
  - [5.11 `clearEEPROM`](#511-cleareeprom)
  - [5.12 `startFileTransfer`](#512-startfiletransfer)
  - [5.13 `abortFileTransfer`](#513-abortfiletransfer)
  - [5.13.1 `startFileDownload`](#5131-startfiledownload)
  - [5.13.2 `abortFileDownload`](#5132-abortfiledownload)
  - [5.13.3 `sendFileToCPUC`](#5133-sendfiletocpuc)
  - [5.14 `startJob`](#514-startjob)
  - [5.15 `pauseJob`](#515-pausejob)
  - [5.16 `resumeJob`](#516-resumejob)
  - [5.17 `stopJobReset`](#517-stopjobreset)
  - [5.17.1 `grblSoftReset`](#5171-grblsoftreset)
  - [5.18 `grblHoming`](#518-grblhoming)
  - [5.19 `grblHomingX`](#519-grblhomingx)
  - [5.20 `grblHomingY`](#520-grblhomingy)
  - [5.21 `grblHomingZ`](#521-grblhomingz)
  - [5.22 `grblUnlock`](#522-grblunlock)
  - [5.23 `moveX`](#523-movex)
  - [5.24 `moveY`](#524-movey)
  - [5.25 `moveZ`](#525-movez)
  - [5.26 `moveXYZ`](#526-movexyz)
  - [5.26.1 `moveMachineX`](#5261-movemachinex)
  - [5.26.2 `moveMachineY`](#5262-movemachiney)
  - [5.26.3 `moveMachineZ`](#5263-movemachinez)
  - [5.26.4 `moveMachineXYZ`](#5264-movemachinexyz)
  - [5.27 `setFeedRateOverride`](#527-setfeedrateoverride)
  - [5.28 `setSpindleOverride`](#528-setspindleoverride)
  - [5.29 `enableLaserSensor`](#529-enablelasersensor)
  - [5.30 `disableLaserSensor`](#530-disablelasersensor)
  - [5.31 `moveA`](#531-movea)
  - [5.31.1 `jog` (press-and-hold continuous jogging)](#5311-jog-press-and-hold-continuous-jogging)
  - [5.31.2 `jogCancel`](#5312-jogcancel)
- [6. SET API (type = set)](#6-set-api-type--set)
  - [6.2 `setDebuggingBase`](#62-setdebuggingbase)
  - [6.3 `setDebuggingSpindle`](#63-setdebuggingspindle)
  - [6.4 `setDebuggingStatus`](#64-setdebuggingstatus)
  - [6.5 `setDebuggingMacroBuffer`](#65-setdebuggingmacrobuffer)
  - [6.6 `setDebuggingSDCard`](#66-setdebuggingsdcard)
  - [6.7 `setDebuggingJson`](#67-setdebuggingjson)
  - [6.9 `setDebuggingEEPROM`](#69-setdebuggingeeprom)
  - [6.10 `setDebuggingJob`](#610-setdebuggingjob)
  - [6.11 `setBenchmarking`](#611-setbenchmarking)
  - [6.12 `setBenchmarkMinimumMs`](#612-setbenchmarkminimumms)
  - [6.13 `setJsonSettings`](#613-setjsonsettings)
  - [6.14 `setJobX`](#614-setjobx)
  - [6.15 `setJobY`](#615-setjoby)
  - [6.16 `setJobZ`](#616-setjobz)
  - [6.17 `setJobXYZ`](#617-setjobxyz)
  - [6.17.1 `setJobA`](#6171-setjoba)
  - [6.20 `setDryRun`](#620-setdryrun)
  - [6.21 `setJobA`](#621-setjoba)
- [7. GET API (type = get)](#7-get-api-type--get)
  - [7.1 `status`](#71-status)
  - [7.1.1 `positions`](#711-positions)
  - [7.2 `machinePositionX`](#72-machinepositionx)
  - [7.3 `machinePositionY`](#73-machinepositiony)
  - [7.4 `machinePositionZ`](#74-machinepositionz)
  - [7.4.1 `machinePositionA`](#741-machinepositiona)
  - [7.5 `machinePositionXYZ`](#75-machinepositionxyz)
  - [7.6 `jobX`](#76-jobx)
  - [7.7 `jobY`](#77-joby)
  - [7.8 `jobZ`](#78-jobz)
  - [7.8.1 `jobA`](#781-joba)
  - [7.9 `jobXYZ`](#79-jobxyz)
  - [7.10 `deviceId`](#710-deviceid)
  - [7.11 `fileList`](#711-filelist)
  - [7.11.1 `gcodeFileHash`](#7111-gcodefilehash)
  - [7.12 `openedGcodeFileName`](#712-openedgcodefilename)
  - [7.13 `jobProgress`](#713-jobprogress)
  - [7.14 `openedGcodeFileTotalLines`](#714-openedgcodefiletotallines)
  - [7.14.1 `nextFileDownloadChunk`](#7141-nextfiledownloadchunk)
  - [7.16 `getDebuggingSettings`](#716-getdebuggingsettings)
  - [7.17 `currentSpindleRPM`](#717-currentspindlerpm)
  - [7.18 `currentFeedRate`](#718-currentfeedrate)
  - [7.19 `getCpuaPassthroughStatus`](#719-getcpuapassthroughstatus)
  - [7.21 `getJsonSettings`](#721-getjsonsettings)
  - [7.21.2 `firmwareVersion`](#7212-firmwareversion)
  - [7.22 `getFeedRateOverride`](#722-getfeedrateoverride)
  - [7.23 `getSpindleOverride`](#723-getspindleoverride)
  - [7.24 `electricalCurrent`](#724-electricalcurrent)
  - [7.26 `laserSensorEnabled`](#726-lasersensorenabled)
  - [7.29.2 `getMachineUsage`](#7292-getmachineusage)
  - [7.29.3 `getJobHistory`](#7293-getjobhistory)
  - [7.29.4 `getJobCount`](#7294-getjobcount)
  - [7.29.5 `getSpindleType`](#7295-getspindletype)
  - [7.29.5.1 `getOpenedFileBounds`](#72951-getopenedfilebounds)
  - [7.32 `getMaterialCenterPosition`](#732-getmaterialcenterposition)
- [8. File chunk upload API](#8-file-chunk-upload-api)
- [9. MsgPack API format (removed)](#9-msgpack-api-format-removed)
- [10. Unknown or malformed requests](#10-unknown-or-malformed-requests)
- [11. Practical examples](#11-practical-examples)
- [12. WebSocket transport](#12-websocket-transport)
  - [12.1 Overview](#121-overview)
  - [12.2 Network configuration](#122-network-configuration)
  - [12.3 ESP32-C3 initialization sequence](#123-esp32-c3-initialization-sequence)
  - [12.4 Incoming message framing](#124-incoming-message-framing)
  - [12.5 Message API over WebSocket](#125-message-api-over-websocket)
  - [12.6 WebSocket ping watchdog](#126-websocket-ping-watchdog)
  - [12.7 Binary frames over WebSocket](#127-binary-frames-over-websocket)
  - [12.8 Mobile server discovery via BLE](#128-mobile-server-discovery-via-ble)

---

## 1. Transport and framing

- Primary command channel is USB serial.
- JSON messages are line-based (newline terminated).
- If a line is valid JSON, it is handled by the CPUB API.
- If a line is not valid JSON, it is treated as passthrough payload (for example G-code to CPUA) when CPUA passthrough is enabled.
- Non-JSON spindle lines are handled specially: CPUB tracks `M3` and `S` as modal words (they may arrive on the same line or on separate lines, in either order) and ramps toward the requested RPM using repeated `M3 S...` steps, waiting for an `ok` from GRBL-HAL after each step. See the spindle ramping note in section [12.5](#125-message-api-over-websocket) for the full rules.
- JSON API responses are emitted as one JSON line, only when jsonSettings.enableJsonResponse is true.

File chunks are transferred as JSON lines with the "fcl" key on both USB serial and WebSocket (see section [8](#8-file-chunk-upload-api)).
WebSocket transport supports JSON messages and non-JSON text passthrough to CPUA (see section [12](#12-websocket-transport)).

**A single WebSocket connection is maintained:**
- Mobile WebSocket (link_id=1): connects to a server discovered via BLE advertisement (IP address encoded in the BLE UUID).

(The legacy PC WebSocket on link_id=0, previously discovered via the board device ID / mDNS, has been removed and is no longer used.)

Responses are routed back to the connection the request arrived on.

---

## 2. Common JSON envelope

**Top-level key:**
- `type`

**type values currently supported:**
- `command`
- `set`
- `get`
- `serialPing`
- `websocketPing`

**Special fast-path behavior:**
- Any JSON containing key "fcl" is treated as a file chunk message immediately, without checking type.

**Common response keys:**
- result: success | failure
- error: present for many (not all) failure cases

**Common response payload keys used by file/job progress APIs:**
- `openedFileName`
- `totalFileLines`
- `currentFileLine`
- `jobProgress`
- `procedureBusy`
- `fileName`
- `fileSize`
- `fileDepth`
- `fileWidth`
- `fileHeight`
- `maxFileDepth`
- `maxFileWidth`
- `maxFileHeight`
- `estimatedDurationSeconds`
- `fcl`
- `eof`
- `linesRemaining`

**Common error values emitted:**
- `unknownJsonType`
- `unknownTarget`
- `unknownJsonCommandType`
- `missingJsonKey`
- `invalidJsonValue`
- `fileNotFound`
- `fileDimensionsExceeded`
- `softLimitExceeded`
- `notHomed`
- `rotaryYLock`
- `fileCreationFailed`
- `fileRenameFailed`
- `fileAlreadyExists`
- `fileTransferInProgress`
- `fileTransferTimeout`
- `fileDownloadInProgress`
- `macroAlreadyRunning`
- `macroRunning`
- `jobAlreadyRunning`
- `jobRunning`
- `noFileOpened`
- `fileBoundsUnavailable`
- `noFileDownloadInProgress`
- `noJobRunning`
- `jobAlreadyPaused`
- `jobNotPaused`

---

### 2.1 Routing: reaching CPU-C

An optional **`target`** key selects which processor handles the message.

| `target` | Handled by |
| --- | --- |
| absent, or `"cpub"` | CPU-B itself — everything described in this document |
| `"cpuc"` | forwarded to CPU-C untouched |
| anything else | rejected with `unknownTarget` |

With `"target":"cpuc"`, CPU-B writes the **raw line** to the CPU-C UART and takes no further
part. It does not parse the payload, so CPU-C's command set can grow without CPU-B knowing
anything about it.

```json
{"target":"cpuc","type":"serialPing"}
```

CPU-C's reply travels back over the same link and CPU-B mirrors it to the host. Every CPU-C
response carries `"from":"cpuc"`, so a client reading one stream can tell the two processors
apart:

```json
{"from":"cpuc","result":"success"}
```

The link runs at 2 Mbaud, 8N1. CPU-C's own API — status, relays, galvo positioning — is
documented in **[../CPU-C/README.md](../CPU-C/README.md)**.


## 3. JSON request keys

**General:**
- `type`
- `commandType`
- `setType`
- `getType`

**File transfer:**
- `filename`
- `newFilename`
- `filelines`
- `fileDate`
- `fcl`
- `eof`
- crc (defined but not used)

**WiFi settings:**
- `apWifiSSID`
- `apWifiPassword`
- `clientWifiSSID`
- `clientWifiPassword`

**Debugging and benchmarking settings:**
- `debuggingBase`
- `debuggingSpindle`
- `debuggingStatus`
- `debuggingMacroBuffer`
- `debuggingSDCard`
- `debuggingJson`
- `debuggingESP32`
- `debuggingEEPROM`
- `benchmarking`
- `benchmarkMinimumMs`

**JSON settings:**
- `jsonResponse`

**Machine settings:**
- `machineName`
- `firmwareVersion`

**Job coordinate set payload keys:**
- `jobX`
- `jobY`
- `jobZ`
- `jobA`

---

## 4. Top-level ping messages

### 4.1 `serialPing`

**Request:**
```json
{"type":"serialPing"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- Over USB serial, resets the serial client watchdog and marks the serial client as connected.
- Any normal USB traffic also refreshes the same serial client watchdog.
- If no USB traffic and no `serialPing` are received over USB within `SERIAL_API_PING_TIMEOUT_MS` (currently 10000 ms), the firmware marks the serial client as disconnected. The window is generous because any incoming byte resets it, so it only trips on a genuinely silent link (e.g. an SD-card job with no polling).
- If a job is running **and the machine is actually in `RUN`** when that timeout occurs, the firmware pauses via grblHAL parking (`0x84`) exactly like a `pauseJob` (spindle stop + Z raise). The `RUN` guard prevents a spurious second pause during an in-progress pause/resume (when the machine sits in Hold for a few seconds).

### 4.2 `websocketPing`

**Request:**
```json
{"type":"websocketPing"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- Also resets the WebSocket watchdog for the mobile connection that sent the ping.
- Any normal traffic received on the WebSocket link also refreshes the same watchdog.

---

## 5. COMMAND API (type = command)

### 5.1 `enableCPUAPassthrough`

**Request:**
```json
{"type":"command","commandType":"enableCPUAPassthrough"}
```

**Response:**
```json
{"result":"success"}
```

### 5.2 `disableCPUAPassthrough`

**Request:**
```json
{"type":"command","commandType":"disableCPUAPassthrough"}
```

**Response:**
```json
{"result":"success"}
```

### 5.5 `moveToRotaryPosition`

**Request:**
```json
{"type":"command","commandType":"moveToRotaryPosition"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"macroAlreadyRunning"}
```

**Notes:**
- Loads the `MOVE_TO_ROTARY_POSITION` macro into the macro buffer and starts macro execution.
- Macro sequence: `G1 F1000`, `G53 Y<rotaryY>`.
- `<rotaryY>` is the machine-coordinate Y of the rotary position. It is selected at compile time from the installed spindle variant (`INSTALLED_SPINDLE_TYPE` in `Config.h`): `MOVE_TO_ROTARY_Y_24V` for the 24V spindle, `MOVE_TO_ROTARY_Y_48V` for the 48V spindle. Both currently default to -60.78 mm; set `MOVE_TO_ROTARY_Y_48V` to the real 48V value when known. The installed variant can be read at runtime via `getSpindleType` (see [7.29.5](#7295-getspindletype)).
- Fails with jobAlreadyRunning if a job is running or paused.
- Fails with macroAlreadyRunning if another macro is already active.

### 5.5.1 `installRotary`

**Request:**
```json
{"type":"command","commandType":"installRotary"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"macroAlreadyRunning"}
```

**Notes:**
- Loads the `INSTALL_ROTARY` macro into the macro buffer and starts macro execution.
- Macro sequence: `$HZ`, `G1 F1000`, `G53 X72.5`, `G53 Y0.5`, `G53 X72.5 Y63`.
- Homes Z first, then moves X to the rotary swap position (machine coordinate X72.5), moves Y to machine coordinate 0.5 (near home), then moves to the final rotary position (X72.5 Y63).
- Fails with jobAlreadyRunning if a job is running or paused.
- Fails with macroAlreadyRunning if another macro is already active.

### 5.5.2 `frameJob`

**Request:**
```json
{"type":"command","commandType":"frameJob"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"noFileOpened"}
{"result":"failure","error":"fileBoundsUnavailable"}
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"macroAlreadyRunning"}
{"result":"failure","error":"rotaryYLock"}
```

**Notes:**
- Traces the bounding box of the currently opened G-code file as a rectangle, so the operator can preview the area the job will occupy on the stock before running it.
- Requires a file opened via `openGcodeFile`; returns `noFileOpened` if no file is open.
- The rectangle comes from the file's stored bounds metadata (`MINX`/`MAXX`/`MINY`/`MAXY`), which are in work coordinates relative to the current job zero (the WCS origin). No offset math is applied — the frame is drawn wherever job zero currently is, so set job zero first.
- Macro sequence (values filled from the file bounds): `G90`, `G1 X0 Y0 F1000`, `G1 F1200 X<minX> Y<minY>`, `G1 X<maxX>`, `G1 Y<maxY>`, `G1 X<minX>`, `G1 X0 Y0` — it first moves to job zero (F1000) so the frame always begins from zero (the first edge is actually drawn, not a diagonal from wherever the tool was), then walks the bounding-box corners at F1200 and returns to job zero. When job zero is at a corner of the bounds (the usual case) the lead-in from zero draws the remaining edge, so this traces a clean closed rectangle that both starts and ends at X0 Y0.
- **Z is not moved**: the frame runs entirely at the current Z height. Raise Z to a safe clearance before framing if the tool is close to the stock.
- Fixed feed rate `G1 F1200`.
- Curved toolpaths are framed correctly: `G2`/`G3` arcs contribute their real extent to the stored bounds, not just their endpoints, so a circular pocket or a curve-heavy profile is enclosed by the rectangle rather than cut through it. See the bounds notes in section [5.12](#512-startfiletransfer).
- Because bounds are stored in the file at upload time, this applies to files uploaded by firmware that computes arc extents. A file uploaded by older firmware keeps its original (under-sized) bounds and must be re-uploaded to be framed accurately.
- Fails with `fileBoundsUnavailable` when the opened file has no usable bounds — a file with no motion at all (metadata written as `EMPTY=1`), or one uploaded with its own `;MINICNC_BOUNDS` header that carries no `MIN`/`MAX` fields.
- Fails with `jobAlreadyRunning` if a job is running or paused, `macroAlreadyRunning` if another macro is active, and `rotaryYLock` if the rotary axis is connected (the frame moves Y).
- Moves are sent through the macro buffer and are soft-limit checked; a corner outside the work envelope aborts the frame with `softLimitExceeded`.

### 5.6 `returnJobXY0Macro`

**Request:**
```json
{"type":"command","commandType":"returnJobXY0Macro"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"macroAlreadyRunning"}
```

**Notes:**
- Loads the `RETURN_JOB_XY0` macro into the macro buffer and starts macro execution.
- Fails with jobAlreadyRunning if a job is running or paused.
- Fails with macroAlreadyRunning if another macro is already active.

### 5.7 `openGcodeFile`

**Request:**
```json
{"type":"command","commandType":"openGcodeFile","filename":"job.nc"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"fileNotFound"}
{"result":"failure","error":"missingJsonKey"}
```

**Notes:**
- If the file starts with the prepended `;MINICNC_BOUNDS ...` metadata line generated during upload finalization, that metadata line is skipped during SD job execution and is not forwarded to CPUA.

### 5.8 `closeGcodeFile`

**Request:**
```json
{"type":"command","commandType":"closeGcodeFile"}
```

**Success:**
```json
{"result":"success"}
```

**Failure:**
```json
{"result":"failure","error":"fileNotFound"}
```

### 5.9 `deleteGcodeFile`

**Request:**
```json
{"type":"command","commandType":"deleteGcodeFile","filename":"job.nc"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"fileNotFound"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure"}
```

**Notes:**
- Fails with fileNotFound if the file does not exist on the SD card.
- Fails with missingJsonKey if the filename key is absent.
- Fails with result:failure (no error key) if the SD delete operation itself fails.
- When a matching sidecar hash file named `<filename>_hash` exists, it is deleted together with the main file.

### 5.10 `renameGcodeFile`

**Request:**
```json
{"type":"command","commandType":"renameGcodeFile","filename":"old-job.nc","newFilename":"new-job.nc"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"fileNotFound"}
{"result":"failure","error":"fileAlreadyExists"}
{"result":"failure","error":"fileRenameFailed"}
```

**Notes:**
- Only works when both `filename` and `newFilename` end with `.nc`.
- Fails with missingJsonKey if either filename key is absent.
- Fails with invalidJsonValue if either filename is not an `.nc` file.
- Fails with fileNotFound if the source file does not exist.
- Fails with fileAlreadyExists if the target filename already exists.
- When a matching sidecar hash file named `<filename>_hash` exists, it is renamed together with the main file.

### 5.11 `clearEEPROM`

**Request:**
```json
{"type":"command","commandType":"clearEEPROM"}
```

**Response:**
```json
{"result":"success"}
```

### 5.12 `startFileTransfer`

**Request:**
```json
{"type":"command","commandType":"startFileTransfer","filename":"job.nc","filelines":120,"fileDate":"2026-03-26 14:30:00"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"fileAlreadyExists"}
{"result":"failure","error":"fileCreationFailed"}
{"result":"failure","error":"missingJsonKey"}
```

**Notes:**
- Required keys: filename, filelines, fileDate
- fileDate format: YYYY-MM-DD HH:MM:SS
- Fails with jobAlreadyRunning if a job is running or paused.
- Fails with missingJsonKey if another transfer is active (transferFileLines is not zero).
- Transfer writes to a temporary filename first, then creates the final file by prepending a metadata comment and copying the uploaded G-code contents.
- Transfer finalization also computes a SHA-256 hash of the exact final saved file contents using the RP2350 SHA-256 hardware and writes it to a sidecar text file named `<filename>_hash`.
- The prepended metadata is computed during upload — but only if the uploaded file does not already start with a `;MINICNC_BOUNDS` line. If it does, the firmware treats the client's header as authoritative: it is copied through verbatim, no bounds are computed, and no second metadata line is added. Everything downstream that reads bounds (file listing, `frameJob`, the work-area size check) then depends entirely on what the client wrote.
- Bounds are computed from `G0`, `G1`, `G2` and `G3` moves. Arcs contribute their true extent rather than just their endpoints: the centre and swept angle are resolved from the `I`/`J`/`K` offsets, and whichever of the four cardinal extreme points the arc actually sweeps through are folded into the box. This is plane-aware (`G17`/`G18`/`G19` — a `G18` arc bulges into Z, not Y), and helical arcs get the matching height on the axis normal to the plane. Modal arcs (a `G2`/`G3` stated once, followed by bare coordinate lines) are measured the same way.
- `G53`, `G92` and unsupported coordinate-transform commands are excluded from the bounds and mark the stored metadata approximate (`EXACT=0`). Arcs also mark `EXACT=0`, but only when their centre cannot be resolved — `R`-format arcs (no `I`/`J`/`K`) or a degenerate radius — in which case just the endpoint is measured and the bulge is missing.
- Estimated duration is accumulated in whole seconds using a simple version 1 model: `G0` uses `GCODE_RAPID_FEED_RATE_MM_PER_MIN`, `G1` uses the modal feed, and `G2`/`G3` use a rough arc-length estimate.
- Finalization rejects files whose computed bounds exceed configured limits:
- XY upload bound (`MAX_WORK_DIMENSION_XY_MM`) = 150 mm, Z upload bound (`MAX_WORK_DIMENSION_Z_MM`) = 70 mm.
- On this rejection, upload fails with `fileDimensionsExceeded` and no final file is saved. The response also reports the measured job size and the limit for each axis, so the client can name the offending axis and by how much it overruns:
  `{"result":"failure","error":"fileDimensionsExceeded","fileWidth":180.5,"fileDepth":92.0,"fileHeight":12.4,"maxFileWidth":150,"maxFileDepth":150,"maxFileHeight":70}`
  Sizes use the same mapping as the file listing: `SIZEX` -> `fileWidth`, `SIZEY` -> `fileDepth`, `SIZEZ` -> `fileHeight`. `maxFileWidth` and `maxFileDepth` both carry the single XY limit; they are sent per-axis so the client can compare field by field.
- Caveat: the size check only runs when the firmware computed the bounds itself. A file uploaded with its own `;MINICNC_BOUNDS` header is trusted verbatim (see the metadata note above), so its bounds are never recomputed and the work-area limit is **not** enforced for it, at any size.
- After startFileTransfer succeeds, the firmware expects chunk activity within `FILE_TRANSFER_CHUNK_TIMEOUT_MS` (currently 5000 ms).
- Each received upload chunk refreshes that timer. If it expires, the upload is aborted and the temporary file is deleted.

### 5.13 `abortFileTransfer`

**Request:**
```json
{"type":"command","commandType":"abortFileTransfer"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- Deletes the temporary transfer file, clears transfer state.
- The same cleanup also happens automatically if upload chunk activity times out.

### 5.13.1 `startFileDownload`

**Request:**
```json
{"type":"command","commandType":"startFileDownload","filename":"job.nc"}
```

**Success:**
```json
{"result":"success","fileName":"job.nc","fileSize":12345,"totalFileLines":5420}
```

**Success (file larger than 32 KB, line count unknown):**
```json
{"result":"success","fileName":"scan.ply","fileSize":9400000,"totalFileLines":0}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"fileNotFound"}
{"result":"failure","error":"fileTransferInProgress"}
{"result":"failure","error":"fileDownloadInProgress"}
```

**Behavior:**
- Starts a dedicated file download session that is separate from openGcodeFile.
- Replies immediately: the reply never waits for a full-file scan, so `status` polls and other requests keep being answered on large files.
- `fileSize` is the file size in bytes. Use it with `downloadedBytes` from each `nextFileDownloadChunk` reply for progress (`downloadedBytes / fileSize`).
- `totalFileLines` is the number of lines the chunks will deliver (blank lines and `;` comments are skipped, the `;MINICNC_BOUNDS` metadata line is kept) — but only for files up to `DOWNLOAD_LINE_COUNT_MAX_FILE_SIZE_BYTES` (32 KB, in `Config.h`). For bigger files it is `0`, meaning **unknown**: counting reads the whole file (about 2 s per MB) and would block the controller. The download itself works the same either way.
- Does not change the execution file used for sending G-code to GRBL.
- Fails if an upload is already in progress.
- While a download is active, new uploads are rejected.
- After startFileDownload succeeds, the firmware expects chunk activity within `FILE_DOWNLOAD_CHUNK_TIMEOUT_MS` (currently 25000 ms).
- Each successful `nextFileDownloadChunk` request refreshes that timer. If it expires, the download session is closed automatically.

### 5.13.2 `abortFileDownload`

**Request:**
```json
{"type":"command","commandType":"abortFileDownload"}
```

**Success:**
```json
{"result":"success"}
```

**Failure:**
```json
{"result":"failure","error":"noFileDownloadInProgress"}
```

**Behavior:**
- Closes the active download session without affecting the execution file opened via openGcodeFile.
- The same cleanup also happens automatically if download chunk activity times out.

### 5.13.2.1 Routed jobs: the block dialect

The machine has two XY systems — the gantry (grblHAL on CPU-A) and the galvo mirrors (CPU-C) —
and `X`/`Y` mean different things in each. A job file says which one it is addressing, with
M-codes that CPU-B consumes and never forwards:

| Marker | Block goes to | What `X`/`Y` mean |
| --- | --- | --- |
| `M610` | CPU-A (grblHAL) | gantry, work coordinates, mm |
| `M611` | CPU-C | galvo scan field, mm |
| `M600` | ends a galvo block | layer break; the recoat follows |

A file with no marker at all is a stage file, which is what any ordinary 3-axis G-code already
is — so nothing that worked before changes.

Extra axis letters (`U`/`V` for the galvo) were considered and rejected. They would be legal
G-code, but they imply the gantry and the mirrors can move together in one coordinated line,
and they cannot: there are two controllers with no shared motion planner. Blocks describe what
the machine actually does — park the optics, scan the field, move on. (`XG`/`YG` is not an
option at all: a G-code word is one letter plus a number, so `XG175` parses as `X` followed by
a number that is not there, and the coordinate is silently dropped.)

**How a galvo block runs.** `startLayerTransfer` has to declare a byte count up front, which is
not known until the block has been read, so CPU-B measures the block, seeks back, streams it
ack-paced, sends `startLayer`, and then holds the job until CPU-C reports `layerComplete` or
`programComplete`. Nothing goes to CPU-A while the mirrors are working. The hold is not a busy
wait: the completion event is noticed by the CPU-C reader in the main loop.

**Uploads measure stage blocks only.** The bounds check that refuses a file bigger than the
work area skips galvo blocks: their coordinates are scan-field millimetres, and a 170 mm field
position would otherwise fail a 143 mm machine envelope for a move the gantry never makes.

`firmware/CPU-C/examples/blocks.nc` is a two-layer example in this dialect.

### 5.13.3 `sendFileToCPUC`

**Request:**
```json
{"type":"command","commandType":"sendFileToCPUC","fileName":"demo.nc","start":true}
```

**Success:**
```json
{"result":"success","bytes":596838,"lines":34505,"chunks":353,"started":true}
```

**Failure:**
```json
{"result":"failure","error":"fileNotFound"}
```

**Behavior:**
- Reads a G-code file off the SD card and pushes it into CPU-C's PSRAM over the 2 Mbaud link,
  using the same `startLayerTransfer` + `lcl` chunk protocol a host would use. The difference
  is that CPU-B both reads and sends, so the file never leaves the controller.
- `start` is optional and defaults to false. With `start:true` CPU-B issues `startLayer` once
  the last chunk is acknowledged, and the scan begins without another round trip to the host.
- Blank lines and `;` comments are dropped, so the bounds metadata CPU-B prepends on upload
  does not reach the interpreter. `bytes` counts what was actually sent, not the file size.
- Two passes over the file: the first counts the bytes CPU-C should expect, the second sends
  them. `startLayerTransfer` has to declare a total up front, and counting it the same way it
  is later sent is what keeps the two from disagreeing.
- Fails with `fileNotFound` if the card has no such file, and `fileEmpty` if nothing survives
  the filter.
- **The stream is paced by CPU-C's acknowledgements**, the same shape as `waitForOk` on the
  GRBL link. CPU-C answers `startLayerTransfer` and every `lcl` chunk with `bytesRemaining`;
  CPU-B waits for each one before sending the next, so the transfer cannot outrun the
  receiver and `{"result":"success",...}` here does mean CPU-C stored it.
- A refusal stops it at the point of refusal rather than streaming the rest into a receiver
  that has said no. `layerBusy` (a layer still `scanning` or `paused`), `layerTooLarge` and a
  silent link all come back as `{"result":"failure","error":"cpucTransferFailed"}` with
  CPU-C's own reply quoted in `cpucReply`. Measured: a transfer attempted against a scanning
  layer gives up after 1.4 s instead of sending 353 chunks.
- `CPUC_CHUNK_ACK_TIMEOUT_MS` bounds each wait.
- Measured: 600 KB off the card and into PSRAM in about 7 s, the link being the slow part.

### 5.14 `startJob`

**Request:**
```json
{"type":"command","commandType":"startJob","startTime":"2026-06-10 14:32:00"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"fileTransferInProgress"}
{"result":"failure","error":"macroRunning"}
{"result":"failure","error":"doorOpen"}
{"result":"failure","error":"jobAlreadyRunning"}
{"result":"failure","error":"noFileOpened"}
```

**Notes:**
- `startTime` is an optional client-supplied timestamp string (the firmware has no real-time clock). It is stored verbatim in the job-history log (see `getJobHistory`) — any format the client prefers (e.g. `YYYY-MM-DD HH:MM:SS`) works. If omitted, the job is still logged with an empty start time.
- On every launch the firmware appends `"<startTime>,<openedFileName>"` to the hidden job-history log and increments the lifetime job counter (`jobCount`, readable via `getMachineUsage` / `getJobCount`). The counter is persisted to SD immediately so it survives a power loss. Dry runs launched via startJob are counted too.
- Full job history is kept across multiple hidden, rotated SD files, each holding exactly 100 jobs and named by its 1-based job range (`.jobs_1-100`, `.jobs_101-200`, …). The leading dot keeps them out of the file-list / download / delete API. Keeping each file small bounds the SD append cost, so launches stay fast regardless of total lifetime job count. Read history back with `getJobHistory`.
- Fails with fileTransferInProgress while an upload is still active.
- Fails with macroRunning while a macro is active.
- Fails with `doorOpen` when the housing is installed and the lid is open — **unless `dryRunEnabled` is true**. A real job cuts, so it is refused up front rather than started and paused at the first `M3`; a dry run has its M/S/Z words stripped, so nothing spins and nothing plunges. This check reads the door pin directly (no debounce): the lid must be closed at the moment the command arrives. See the spindle-start gate in 6.24.
- When the opened file reaches EOF, the firmware stops the job automatically by clearing the running and paused flags.
- At EOF, CPUB rewinds the opened execution file automatically, so the same file can be started again without reopening.
- If the global `dryRunEnabled` flag is true (set via `setDryRun`), startJob behaves like startDryRun: homes Z via $HZ before execution and sanitises G-code lines to X/Y motion only.
- If `dryRunEnabled` is false (default), startJob runs the file normally and always forces dryRunActive to false.
- In dry-run mode, CPUB sets a fixed feed rate (`DRY_RUN_FEED_RATE_MM_PER_MIN`) before the first dry-run motion line, so speed is consistent across runs.
- Every launch starts from a clean spindle state: the modal spindle memory (M3 flag, memorized `S`, last commanded RPM, any line held for a ramp) is cleared, any leftover pause/resume spindle-restore state is discarded, and the GRBL spindle-override reset byte (0x99) is sent so the job always begins at 100% override. A new file can therefore never inherit a reduced override or stale spindle state from the console, a previous file, or an interrupted resume. Spindle speeds in the file are then applied via the modal `M3 S...` startup ramp (see the spindle ramping note in 12.5).

### 5.15 `pauseJob`

**Request:**
```json
{"type":"command","commandType":"pauseJob"}
```

**Success:**
```json
{"result":"success"}
```

**Behavior (offloaded to grblHAL parking):**
- Sends grblHAL's realtime safety-door byte (`0x84`, `CMD_SAFETY_DOOR`) and sets `currentMachineState = HOLD` for instant UI feedback. Any in-progress manual spindle startup ramp is cancelled (`resetManualSpindleRampState()`).
- grblHAL then performs the pause natively via its **parking** feature: it de-energizes the spindle and lifts Z to the park height (a short pull-out at `$57` then a fast retract to the parking target `$58`). The spindle stops right after the small pull-out, so it is off for essentially the whole raise.
- grblHAL reports the `Door` state during the pause; CPUB masks it to `Hold` so the app sees a normal pause (see the `STATUS` report). The physical safety-door input is **not** enabled on the grblHAL side (`SAFETY_DOOR_ENABLE` is off), so `0x84` is the only trigger.
- The connection watchdog is guarded so it never fires a second `0x84` while the machine is already Hold/resuming — it only auto-pauses when the machine is genuinely `RUN` (see [4.1](#41-serialping) serialPing).
- The old CPUB override-based pause (pre-hold feed-drop + deferred `0x9E` spindle-stop) is retained in the source under `#if 0` for reference but is no longer used.

### 5.16 `resumeJob`

**Request:**
```json
{"type":"command","commandType":"resumeJob"}
```

**Success:**
```json
{"result":"success"}
```

**Failure:**
```json
{"result":"failure","error":"doorOpen"}
```

**Behavior (offloaded to grblHAL parking):**
- Fails with `doorOpen` when the housing is installed and the lid is open (dry runs excepted — they have no spindle to restore). A resume **is** a spindle start: grblHAL's parking restore spins the spindle back up on `~` by itself, so no `M3` crosses the wire and CPUB cannot filter it — the only way to prevent it is to refuse the resume. Close the lid, then resume. Like `startJob`, this check reads the door pin directly, without debounce.
- Sends grblHAL realtime cycle-start (`~`), clears `isJobPaused`, and sets `isResuming = true`.
- grblHAL performs the resume natively via parking, **with the tool still at the park height**: it soft-starts the spindle with a gradual RPM ramp (default 1000 rpm / step, ~2 s — this avoids the 48V spindle's inrush/brown-out; see the grblHAL `DEFAULT_PARKING_SPINDLE_RAMP_*` config), waits the spindle-on settle (`$392`), then descends Z back to the cut in a single move and resumes motion. So the spindle reaches speed while clear of the work, then plunges.
- Because this takes a few seconds, the machine keeps reporting `Door` (masked to `Hold`) until it actually reaches `Run`. The `STATUS` report exposes **`jobResuming`** (boolean): true from the resume request until the machine leaves Hold, so a polling or reconnecting front-end can show a transient "Resuming" state during the spindle ramp + Z descent. It is also cleared if the job is paused again or stopped.
- `isJobPaused` is cleared immediately so `handleJob()` can queue file lines into the grblHAL planner buffer while the parking resume runs; grblHAL will not execute them until the resume completes.
- The old CPUB override-based resume (paced `0x9E`/override ramp + `resumeCompleted` event) is retained under `#if 0` for reference but is no longer used; **no `resumeCompleted` event is pushed** — the front-end should track completion via `jobResuming` going false (or the state reaching `Run`).

### 5.17 `stopJobReset`

**Request:**
```json
{"type":"command","commandType":"stopJobReset"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- If a pause left the spindle stopped via the 0x9E spindle-stop toggle (`pauseResumeSpindleRestorePending = true`), the toggle is cleared with a second 0x9E byte before the reset, so the next job does not inherit a stopped spindle output state.
- Sends GRBL soft reset (Ctrl-X) and stops the current job immediately.
- Clears pause/running state and rewinds the currently opened execution file, so a later `startJob` restarts from the beginning of that same opened file.

### 5.17.1 `grblSoftReset`

**Request:**
```json
{"type":"command","commandType":"grblSoftReset"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- Sends GRBL soft reset character (Ctrl-X) to CPUA.
- Resets isJobRunning, isDryRunActive, isJobPaused, isMacroRunning, waitForOk, and jobStartMs.

### 5.18 `grblHoming`

**Request:**
```json
{"type":"command","commandType":"grblHoming"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
```

**Async completion event (pushed when homing finishes):**
```json
{"type":"homingCompleted","result":"success"}
{"type":"homingCompleted","result":"failure"}
```

**Behavior:**
- Sends `$H` to GRBL with newline terminator.
- Rejected when a job is running or paused.
- Rejected with `rotaryYLock` when rotary axis is connected.
- Returns success immediately (command accepted). The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.
- Once GRBL begins homing the machine state transitions to `Home`. When homing completes, GRBL transitions to `Idle` (success) or `Alarm` (failure). At that point a `homingCompleted` event is pushed back to the same channel that sent the original request.
- To check whether homing has succeeded at least once since boot, call `{"type":"get","getType":"status"}` and read `homedSincePowerOn`.

**Startup note:**
- Automatic homing at firmware startup is disabled by default.

### 5.19 `grblHomingX`

**Request:**
```json
{"type":"command","commandType":"grblHomingX"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"rotaryYLock"}
```

**Async completion event (pushed when homing finishes):**
```json
{"type":"homingCompleted","result":"success"}
{"type":"homingCompleted","result":"failure"}
```

**Behavior:**
- Sends `$HX` to GRBL with newline terminator, homing the X axis only.
- Rejected when a job is running or paused.
- Rejected with `rotaryYLock` when rotary axis is connected.
- Returns success immediately (command accepted). The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.
- Once GRBL begins homing the machine state transitions to `Home`. When homing completes, GRBL transitions to `Idle` (success) or `Alarm` (failure). At that point a `homingCompleted` event is pushed back to the same channel that sent the original request.

### 5.20 `grblHomingY`

**Request:**
```json
{"type":"command","commandType":"grblHomingY"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"rotaryYLock"}
```

**Async completion event (pushed when homing finishes):**
```json
{"type":"homingCompleted","result":"success"}
{"type":"homingCompleted","result":"failure"}
```

**Behavior:**
- Sends `$HY` to GRBL with newline terminator, homing the Y axis only.
- Rejected when a job is running or paused.
- Rejected with `rotaryYLock` when rotary axis is connected.
- Returns success immediately (command accepted). The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.
- Once GRBL begins homing the machine state transitions to `Home`. When homing completes, GRBL transitions to `Idle` (success) or `Alarm` (failure). At that point a `homingCompleted` event is pushed back to the same channel that sent the original request.

### 5.21 `grblHomingZ`

**Request:**
```json
{"type":"command","commandType":"grblHomingZ"}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
```

**Async completion event (pushed when homing finishes):**
```json
{"type":"homingCompleted","result":"success"}
{"type":"homingCompleted","result":"failure"}
```

**Behavior:**
- Sends `$HZ` to GRBL with newline terminator, homing the Z axis only.
- Rejected when a job is running or paused.
- Allowed when rotary axis is connected (Z homing is always permitted).
- Returns success immediately (command accepted). The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.
- Once GRBL begins homing the machine state transitions to `Home`. When homing completes, GRBL transitions to `Idle` (success) or `Alarm` (failure). At that point a `homingCompleted` event is pushed back to the same channel that sent the original request.

### 5.22 `grblUnlock`

**Request:**
```json
{"type":"command","commandType":"grblUnlock"}
```

**Success:**
```json
{"result":"success"}
```

**Failure:**
```json
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `$X` to GRBL with newline terminator.
- Clears GRBL alarm state.
- Rejected when a job is running or paused.
- Returns success immediately. The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.

### 5.23 `moveX`

**Request:**
```json
{"type":"command","commandType":"moveX","x":10.5,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** x, feedRate
- **Optional keys:** relative (boolean, default false)

**RELATIVE MOVES (`moveX`, `moveY`, `moveZ`, `moveXYZ`, `moveA`):**
- With `"relative": true` the value is a DISTANCE to travel from wherever the machine is, not a
  target to travel to: CPUB sends `G91 G1 X<x> F<feedRate>` and puts the modal state straight back
  with a following `G90`.
- This is what a jog STEP wants. An absolute target has to be computed by the caller from a
  position it read some milliseconds earlier, and the machine may have moved since — a second step
  sent before the status catches up is worked out from where the head WAS, so the move arrives
  short by whatever the first step still had to travel. A delta has no such window, and successive
  steps queue in GRBL's planner as whole steps.
- The soft-limit check resolves the delta against the last reported machine position, so it is as
  fresh as the last status report (`ASK_STATUS_INTERVAL`, 200 ms). GRBL's own soft limits (`$20`,
  enabled on CPUA) are the backstop for a step queued while the machine is still moving.
- The reply is unchanged: `softLimitExceeded` when the resulting position would leave the envelope,
  in which case nothing is sent to GRBL and the modal state is left alone.
- The machine-coordinate moves (`moveMachine*`, 5.26.1+) are absolute only; they take no `relative`.

**Runtime soft-limit envelope used by movement checks:**
- X: 0 to 143 mm
- Y: -145 to 0 mm
- Z: -70 to 0 mm

**Behavior:**
- Sends `G90 G1 X<x> F<feedRate>` to GRBL, or `G91 G1 X<x> F<feedRate>` + `G90` when
  `relative` is true.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the commanded target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.

### 5.24 `moveY`

**Request:**
```json
{"type":"command","commandType":"moveY","y":20.0,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** y, feedRate
- **Optional keys:** relative (boolean, default false) — see [5.23](#523-movex)

**Behavior:**
- Sends `G90 G1 Y<y> F<feedRate>` to GRBL, or `G91 G1 Y<y> F<feedRate>` + `G90`
  when `relative` is true.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the commanded target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.

### 5.25 `moveZ`

**Request:**
```json
{"type":"command","commandType":"moveZ","z":-2.5,"feedRate":500}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** z, feedRate
- **Optional keys:** relative (boolean, default false) — see [5.23](#523-movex)

**Behavior:**
- Sends `G90 G1 Z<z> F<feedRate>` to GRBL, or `G91 G1 Z<z> F<feedRate>` + `G90`
  when `relative` is true.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the commanded target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.

### 5.26 `moveXYZ`

**Request:**
```json
{"type":"command","commandType":"moveXYZ","x":10.5,"y":20.0,"z":-2.5,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** x, y, z, feedRate
- **Optional keys:** relative (boolean, default false) — see [5.23](#523-movex)

**Behavior:**
- Sends `G90 G1 X<x> Y<y> Z<z> F<feedRate>` to GRBL, or `G91 G1 X<x> Y<y> Z<z> F<feedRate>`
  + `G90` when `relative` is true.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the commanded target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.

### 5.26.1 `moveMachineX`

**Request:**
```json
{"type":"command","commandType":"moveMachineX","x":10.5,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"macroBufferFull"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** x, feedRate

**Behavior:**
- Queues a two-line macro: `G1 F<feedRate>` followed by `G53 X<x>`.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the machine-coordinate target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.
- Both lines are sent through the macro buffer, so `waitForOk` is handled one line at a time by the existing macro mechanism.

### 5.26.2 `moveMachineY`

**Request:**
```json
{"type":"command","commandType":"moveMachineY","y":20.0,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"macroBufferFull"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** y, feedRate

**Behavior:**
- Queues a two-line macro: `G1 F<feedRate>` followed by `G53 Y<y>`.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the machine-coordinate target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.
- Both lines are sent through the macro buffer, so `waitForOk` is handled one line at a time by the existing macro mechanism.

### 5.26.3 `moveMachineZ`

**Request:**
```json
{"type":"command","commandType":"moveMachineZ","z":-2.5,"feedRate":500}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"macroBufferFull"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** z, feedRate

**Behavior:**
- Queues a two-line macro: `G1 F<feedRate>` followed by `G53 Z<z>`.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the machine-coordinate target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.
- Both lines are sent through the macro buffer, so `waitForOk` is handled one line at a time by the existing macro mechanism.

### 5.26.4 `moveMachineXYZ`

**Request:**
```json
{"type":"command","commandType":"moveMachineXYZ","x":10.5,"y":20.0,"z":-2.5,"feedRate":1000}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"macroBufferFull"}
{"result":"failure","error":"softLimitExceeded"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** x, y, z, feedRate

**Behavior:**
- Queues a two-line macro: `G1 F<feedRate>` followed by `G53 X<x> Y<y> Z<z>`.
- Rejected when a job is running or paused.
- Returns `softLimitExceeded` when the machine-coordinate target is outside configured runtime soft limits.
- Returns `rotaryYLock` when rotary axis is connected and the command would move Y.
- Both lines are sent through the macro buffer, so `waitForOk` is handled one line at a time by the existing macro mechanism.

### 5.27 `setFeedRateOverride`

**Request:**
```json
{"type":"command","commandType":"setFeedRateOverride","action":"<action>"}
```

**Success:**
```json
{"result":"success","feedRateOverride":110}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
```

**Valid action values:**
- reset            — restores feed rate override to 100% (GRBL-HAL byte 0x90)
- increaseCoarse   — +10%                                (GRBL-HAL byte 0x91)
- decreaseCoarse   — -10%                                (GRBL-HAL byte 0x92)
- increaseFine     — +1%                                 (GRBL-HAL byte 0x93)
- decreaseFine     — -1%                                 (GRBL-HAL byte 0x94)

**Notes:**
- Sends the corresponding GRBL-HAL real-time override byte directly to CPUA.
- The cached feedRateOverride value is updated locally (clamped to 10–200).
- The response includes the new cached feedRateOverride value.
- The cached value is an approximation; the authoritative value is reported by GRBL in its status output.

### 5.28 `setSpindleOverride`

**Request:**
```json
{"type":"command","commandType":"setSpindleOverride","action":"<action>"}
```

**Success:**
```json
{"result":"success","spindleOverride":90}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"spindleRpmBelowMinimum","spindleOverride":25,"minimumSpindleRPM":3000}
```

**Valid action values:**
- reset            — restores spindle override to 100%  (GRBL-HAL byte 0x99)
- increaseCoarse   — +10%                               (GRBL-HAL byte 0x9A)
- decreaseCoarse   — -10%                               (GRBL-HAL byte 0x9B)
- increaseFine     — +1%                                (GRBL-HAL byte 0x9C)
- decreaseFine     — -1%                                (GRBL-HAL byte 0x9D)

**Notes:**
- Sends the corresponding GRBL-HAL real-time override byte directly to CPUA.
- The cached spindleOverride value is updated locally (clamped to 10–200).
- The response includes the new cached spindleOverride value.
- The cached value is kept in sync with the authoritative override GRBL reports in its status output: CPUB parses the `Ov:feed,rapid,spindle` field and adopts the spindle value as the source of truth whenever it is present and no resume spindle-restore ramp is actively driving the override. This corrects any drift between the local counter and GRBL's real override.
- A DECREASE IS REFUSED IF IT WOULD TAKE THE SPINDLE BELOW `SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM`
  (3000 rpm). GRBL scales the PROGRAMMED S word by the override, so the limit is a percentage that
  moves with the job: at S12000 the override goes down to 25%, at S4000 only to 75%, and a job
  programmed at or below 3000 rpm cannot be reduced at all. The step is refused BEFORE the override
  byte is sent, so GRBL never sees the reduction; the reply carries `spindleRpmBelowMinimum`, the
  unchanged `spindleOverride`, and `minimumSpindleRPM` so a client can say why the button stopped.
  - Increases are never refused, including from below the floor — an override already under it (the
    job lowered its S while a reduction was in effect) must still be able to come back up.
  - `reset` is never refused: 100% is at or above the floor by construction.
  - This governs the OVERRIDE only. A job that programs S below 3000 still runs at what it asked
    for, clamped by GRBL's own `$31` (1500 rpm as shipped); the override simply cannot make it
    worse. Set `$31` to 3000 on CPUA if the spindle must never turn below it for ANY reason,
    including the job's own S word.

### 5.29 `enableLaserSensor`

**Request:**
```json
{"type":"command","commandType":"enableLaserSensor"}
```

**Success:**
```json
{"result":"success"}
```

**Behavior:**
- Starts a fresh laser sensor warm-up / initialization cycle and drives `LASER_SENSOR_ENABLE_PIN` HIGH.
- The first initialization attempt is scheduled after `DISTANCE_LASER_SENSOR_INIT_DELAY_MS` (1500 ms).
- After a successful initialization triggered by this API, the pin remains HIGH so the sensor stays enabled for use.
- The command response is immediate.
- The `laserSensorEnabled` field returned by the `status` API now mirrors the live GPIO state of `LASER_SENSOR_ENABLE_PIN`.
- Because this value is pin state, it can become `true` before initialization completes; use `getDistanceLaserMm` success to confirm read readiness.
- This pin is controlled only by the dedicated API and is no longer mirrored to the heartbeat blink LED.

### 5.30 `disableLaserSensor`

**Request:**
```json
{"type":"command","commandType":"disableLaserSensor"}
```

**Success:**
```json
{"result":"success"}
```

**Behavior:**
- Drives `LASER_SENSOR_ENABLE_PIN` LOW.
- Clears the current laser sensor initialized state and stops the automatic initialization loop until the sensor is enabled again.
- The `laserSensorEnabled` field returned by the `status` API becomes `false` when the pin is driven LOW.
- The command response is immediate.

### 5.31 `moveA`

**Request:**
```json
{"type":"command","commandType":"moveA","a":90.0,"feedRate":500}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"missingJsonKey"}
```

- **Required keys:** a, feedRate
- **Optional keys:** relative (boolean, default false) — see [5.23](#523-movex)

**Behavior:**
- Sends `G90 G1 A<a> F<feedRate>` to GRBL (rotary A axis absolute move), or
  `G91 G1 A<a> F<feedRate>` + `G90` with `"relative": true` (step by `a` degrees).
- Rejected when a job is running or paused.
- No soft limit check is applied to the A axis.

### 5.31.1 `jog` (press-and-hold continuous jogging)

**Request (single axis):**
```json
{"type":"command","commandType":"jog","x":1.0,"feedRate":800}
```

**Request (multiple axes / diagonal, all axis keys optional, at least one required):**
```json
{"type":"command","commandType":"jog","x":1.0,"y":-1.0,"z":0.5,"feedRate":800}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"doorOpen"}
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"notHomed"}
{"result":"failure","error":"rotaryYLock"}
```

- **Required keys:** feedRate, and at least one of x, y, z. For each axis key only the SIGN is
  used (direction of travel); the magnitude is ignored — the firmware moves toward the
  soft-limit in that direction. So x:1.0 and x:0.001 behave identically (jog +X), x:-1.0 jogs
  -X.

**Behavior:**
- Implements press-and-hold jogging as a single long "jog-to-limit" move, not a stream of
  small steps. On the first `jog` message of a press the firmware commits ONE native jog
  command that travels all the way to the soft-limit in the requested direction(s):
  `$J=G91 [X<dx>] [Y<dy>] [Z<dz>] F<feedRate>`, where each `d` is the distance remaining
  from the current machine position to that axis' soft-limit. GRBL accelerates once and
  holds feed for smooth, stutter-free continuous motion, and stops at the soft-limit on its
  own if the button is held that long.
- Fails with `doorOpen` when the housing is installed, the lid is open, and
  `allowMotionWhenDoorOpen` is `false` — the configuration in which the door interlock does not
  permit motion. The jog is refused before anything is sent, so the head never lurches and no
  pause latch has to be cleared afterwards. With `allowMotionWhenDoorOpen` set to `true`,
  jogging with the lid open is allowed exactly as before. This check is placed ahead of the
  keep-alive branch, so a press held across a lid-opening starts returning `doorOpen` instead
  of acking. It reads the door pin directly, without debounce.
- Motion is stopped on release by `jogCancel` (the GRBL `0x85` realtime byte), which flushes
  the remaining jog instantly. Stop is the default state: releasing the button, losing the
  link, or the keep-alive timeout all end the move.
- While the button stays held the app keeps sending `jog` messages as a keep-alive. Once a
  jog is already in motion these are NOT re-sent to GRBL — they only refresh the keep-alive
  watchdog and return `{"result":"success"}`. A direction change mid-hold without an
  intervening `jogCancel` is ignored; send `jogCancel` on release, then a fresh `jog` to
  change direction.
- The jog distance is clamped to the CPUB soft-limits (Config.h `SOFT_LIMIT_*`) using the
  live machine position, so the move never exceeds the work area. This does not rely on the
  grblHAL `$40` jog-limit setting. If the machine is already parked against the limit in the
  requested direction there is nothing to move; the command still returns success.
- Unlike the `move*` commands, the jog line bypasses the CPUB macro buffer and the CPUB modal
  tracker (the distance is pre-clamped instead).
- Rejected with `jobRunning` when a job is running or paused.
- Rejected with `notHomed` until the machine has been homed since power-on (the soft-limit
  distance is computed from the machine position, which is only valid after homing).
- Rejected with `rotaryYLock` when the rotary axis is connected and the jog includes a Y
  component (mirrors the `move*` commands).
- Recommended app behavior: send a `jog` on button press, then repeat it at a steady
  keep-alive interval while held (e.g. every ~100–150 ms), and send `jogCancel` on release.
  Keep the interval comfortably below `JOG_KEEPALIVE_TIMEOUT_MS` (currently 350 ms) so the
  watchdog does not stop the jog prematurely. Step sizing no longer matters — only the sign.

**Safety / keep-alive:**
- A jog keep-alive watchdog runs continuously. If no `jog` message is received for
  `JOG_KEEPALIVE_TIMEOUT_MS` (currently 350 ms) while a jog is active, the firmware
  automatically sends the GRBL real-time jog-cancel byte (`0x85`) and stops the jog. This
  protects against a lost `jogCancel` message or a dropped link mid-jog.

### 5.31.2 `jogCancel`

**Request:**
```json
{"type":"command","commandType":"jogCancel"}
```

**Success:**
```json
{"result":"success"}
```

**Behavior:**
- Sends the GRBL real-time jog-cancel byte (`0x85`) directly to CPUA, which immediately
  flushes any queued jog motion and stops the machine without entering alarm/reset state.
- Intended to be sent on jog-button release for a crisp, immediate stop.
- Always returns `{"result":"success"}`. Sending it when no jog is active is harmless
  (GRBL ignores a jog cancel while idle).
- The same cancel is issued automatically by the keep-alive watchdog if jog messages stop
  arriving (see `jog` above).

## 6. SET API (type = set)

**All successful set calls return:**
```json
{"result":"success"}
```

**Missing required key returns:**
```json
{"result":"failure","error":"missingJsonKey"}
```

**Unknown setType returns:**
```json
{"result":"failure","error":"unknownJsonType"}
```

### 6.2 `setDebuggingBase`

**Request:**
```json
{"type":"set","setType":"setDebuggingBase","debuggingBase":true}
```

### 6.3 `setDebuggingSpindle`

**Request:**
```json
{"type":"set","setType":"setDebuggingSpindle","debuggingSpindle":false}
```

### 6.4 `setDebuggingStatus`

**Request:**
```json
{"type":"set","setType":"setDebuggingStatus","debuggingStatus":true}
```

### 6.5 `setDebuggingMacroBuffer`

**Request:**
```json
{"type":"set","setType":"setDebuggingMacroBuffer","debuggingMacroBuffer":false}
```

### 6.6 `setDebuggingSDCard`

**Request:**
```json
{"type":"set","setType":"setDebuggingSDCard","debuggingSDCard":false}
```

### 6.7 `setDebuggingJson`

**Request:**
```json
{"type":"set","setType":"setDebuggingJson","debuggingJson":true}
```

### 6.9 `setDebuggingEEPROM`

**Request:**
```json
{"type":"set","setType":"setDebuggingEEPROM","debuggingEEPROM":false}
```

### 6.10 `setDebuggingJob`

**Request:**
```json
{"type":"set","setType":"setDebuggingJob","debuggingJob":false}
```

**Behavior:**
- Enables or disables job execution debug prints on USB serial.
- With debugging enabled, EOF now produces a single completion message instead of repeated "Reading line from SD card.." prints after the file is finished.

### 6.11 `setBenchmarking`

**Request:**
```json
{"type":"set","setType":"setBenchmarking","benchmarking":true}
```

### 6.12 `setBenchmarkMinimumMs`

**Request:**
```json
{"type":"set","setType":"setBenchmarkMinimumMs","benchmarkMinimumMs":10}
```

### 6.13 `setJsonSettings`

**Request:**
```json
{"type":"set","setType":"setJsonSettings","jsonResponse":true}
```

**Behavior note:**
- If jsonResponse is set to false, command still applies but no JSON confirmation is printed because responses are disabled.

### 6.14 `setJobX`

**Request:**
```json
{"type":"set","setType":"setJobX","jobX":12.345}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `G10 L20 P1 X<value>` to GRBL (persistent G54 work offset update).
- Returns success immediately. The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.

### 6.15 `setJobY`

**Request:**
```json
{"type":"set","setType":"setJobY","jobY":67.89}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `G10 L20 P1 Y<value>` to GRBL (persistent G54 work offset update).
- Returns success immediately. The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.

### 6.16 `setJobZ`

**Request:**
```json
{"type":"set","setType":"setJobZ","jobZ":-1.23}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `G10 L20 P1 Z<value>` to GRBL (persistent G54 work offset update).
- Returns success immediately. The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.

### 6.17 `setJobXYZ`

**Request:**
```json
{"type":"set","setType":"setJobXYZ","jobX":12.345,"jobY":67.89,"jobZ":-1.23}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `G10 L20 P1 X<value>`, `G10 L20 P1 Y<value>`, `G10 L20 P1 Z<value>` to GRBL in sequence.
- Returns success immediately. Each command is sent to GRBL as soon as the serial channel is idle; commands still pending acknowledgement are queued and delivered in order once GRBL responds.

### 6.17.1 `setJobA`

**Request:**
```json
{"type":"set","setType":"setJobA","jobA":0}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
{"result":"failure","error":"rotaryYLock"}
```

**Behavior:**
- Sets the A (rotary) work offset by sending `G10 L20 P1 A<value>` to GRBL. Send `jobA: 0` to reset the rotary axis work position to zero.
- The A axis is **not** soft-limit checked. The outgoing-line checker parses only `X`/`Y`/`Z` words and `Config.h` defines soft limits for those three axes only, so an `A` value is never range-checked and `G10 L20 P1 A<value>` is never rejected on limits.
- Unlike `setJobX`/`setJobY`/`setJobZ`, the command is issued before the response, so a line rejected on the way out is reported as a failure rather than answered with a success the machine never acted on. The reported error follows the actual reject reason (`rotaryYLock`, or `softLimitExceeded` for rules that could apply to other lines).
- Does not check whether a rotary axis is actually connected; the caller is expected not to offer the action when no A axis is present.

### 6.20 `setDryRun`

**Request:**
```json
{"type":"set","setType":"setDryRun","dryRunEnabled":true}
{"type":"set","setType":"setDryRun","dryRunEnabled":false}
```

**Success:**
```json
{"result":"success","dryRunEnabled":true}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
```

**Notes:**
- Sets the global dry-run flag. This flag persists in RAM until changed or the machine is restarted (it is not saved to EEPROM).
- When `dryRunEnabled` is true, a subsequent `startJob` command will execute the opened file in dry-run mode: Z axis is homed via $HZ first, and G-code lines are sanitised to X/Y motion only (Z, M, S words stripped; pure-Z/spindle/tool-change lines skipped).
- When `dryRunEnabled` is false (default), `startJob` runs the file normally.
- The current value of `dryRunEnabled` is returned in the `getStatus` response.

### 6.21 `setJobA`

**Request:**
```json
{"type":"set","setType":"setJobA","jobA":0.0}
```

**Success:**
```json
{"result":"success"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"jobRunning"}
```

**Behavior:**
- Sends `G10 L20 P1 A<value>` to GRBL (persistent G54 work offset update for rotary A axis).
- Returns success immediately. The command is sent to GRBL as soon as the serial channel is idle; if a previous command is still pending acknowledgement, it is queued and delivered once GRBL responds.

## 7. GET API (type = get)

**Unknown getType returns:**
```json
{"result":"failure","error":"unknownJsonType"}
```

### 7.1 `status`

**Request:**
```json
{"type":"get","getType":"status"}
```

**Response example:**
```json
{"result":"success","machineStatus":"Idle","probeTriggered":false,"laserSensorEnabled":false,"distanceLaserSensorConnected":true,"sdCardConnected":true,"rotaryAxisConnected":false,"homedSincePowerOn":true,"doorOpen":false,"fileOpened":true,"jobRunning":false,"dryRunEnabled":false,"dryRunActive":false,"procedureBusy":false,"jobPaused":false,"jobResuming":false,"fileTransferInProgress":false,"fileDownloadInProgress":false,"openedFileName":"job.nc"}
```

**Behavior:**
- `machineStatus` is GRBL's state as reported, with two deliberate substitutions:
  - `Door` is presented as `Hold`, since the pause runs through grblHAL's safety-door parking.
  - `Idle` is presented as `Run` for any momentary idle while a job is running and not paused. GRBL
    drains between lines under one-line-at-a-time flow control, and an `M3` read from a job stops the
    machine outright for a few seconds while the spindle ramps — both make GRBL correctly report
    `Idle` mid-job. Reported raw, a running job flickers to idle. A finished job clears `jobRunning`
    and a paused one sets `jobPaused`, so neither state is masked.
- `fileOpened` is true when an execution file is currently open.
- `jobRunning` and `jobPaused` reflect the execution state of the opened file.
- `jobResuming` is true from a `resumeJob` request until the machine actually leaves Hold and reaches `Run`. During that window grblHAL is running the parking resume (spindle soft-start ramp + Z descent, a few seconds) while the status still reads `Hold`, so use `jobResuming` to show a transient "Resuming" indication (it is poll-friendly and survives reconnects — no event needs to be caught). It also clears if the job is paused again or stopped. Note: `resumeCompleted` events are no longer pushed; track completion via this flag going false (or `machineStatus` reaching `Run`).
- `probeTriggered` reflects the latest probe pin state parsed from GRBL status reports (`Pn:` contains `P`).
- `laserSensorEnabled` in `status` reflects the live GPIO output state of `LASER_SENSOR_ENABLE_PIN`.
- `true` means the pin is currently driven HIGH; `false` means it is LOW.
- `laserSensorEnabled` in `status` now matches the dedicated `laserSensorEnabled` getter.
- `distanceLaserSensorConnected` is a cached presence flag for the API. It becomes `true` after the firmware successfully initializes the laser sensor at least once, and it is kept separate from the live enable pin state. `enableLaserSensor` restarts the warm-up / init sequence manually, while `disableLaserSensor` turns the pin off and stops the init loop, but does not clear the cached presence flag once the sensor has been detected successfully.
- `sdCardConnected` is `true` when the SD card was initialised successfully at boot. The card is only initialised once, at power-up, and never retried, so `false` means no card (or an unreadable one) was found at boot: insert a working card and power cycle. While it is `false`, `fileList` returns an empty list and every file command fails.
- `rotaryAxisConnected` reflects the latest rotary axis detection state based on current sensor hysteresis (< 60 mA: false, > 120 mA: true). Always `false` if current sensor is not initialized.
- `homedSincePowerOn` is `false` at boot and becomes `true` after at least one successful homing completion (`homingCompleted` with `result: success`). It is RAM-only and resets to `false` on every power cycle/reboot.
- `doorOpen` is the live door/lid sensor pin state (`true` = open), read fresh on every `getStatus` call. It reflects the **raw, undebounced** input regardless of `housingInstalled`; the interlock only acts on it when the housing is installed (see `setHousingSettings`). So `doorOpen` alone does not tell you whether the interlock is engaged — combine it with `housingInstalled` from `getHousingSettings` (a cached value, since it only changes when the app sets it) to know whether `startJob` / `resumeJob` will be refused with `doorOpen`.
- `dryRunEnabled` reflects the current value of the global dry-run flag (set via `setDryRun`). When true, the next `startJob` will execute in dry-run mode.
- `dryRunActive` is true while a dry-run job is executing (either startDryRun was used, or startJob was called while dryRunEnabled was true, and EOF has not been reached yet).
- `procedureBusy` is a public/UI busy flag. It becomes `true` while one of these guided procedures is active: height-map scan, point-cloud scan, laser material-center detection, or laser edge / XY0 detection.
- `procedureBusy` does not replace `machineStatus`: `machineStatus` remains the raw GRBL state (`Idle`, `Run`, `Hold`, `Alarm`, `Home`, `Jog`, `Door`, ...), while `procedureBusy` is the stable high-level flag intended for UI consumers. During continuous jogging `machineStatus` is `Jog`.
- The same derived `procedureBusy` signal is also used by the RGB status LED, so the LED shows a stable public busy indication during those procedures instead of flickering with short internal motion/idle transitions.
- `fileTransferInProgress` is true during upload-to-SD sessions.
- `fileDownloadInProgress` is true during download-from-SD sessions.
- `openedFileName` is included only when an execution file is currently open.

### 7.1.1 `positions`

**Request:**
```json
{"type":"get","getType":"positions"}
```

**Response example:**
```json
{"result":"success","machinePositionX":12.345,"machinePositionY":67.89,"machinePositionZ":-1.23,"machinePositionA":180.0,"jobPositionX":12.345,"jobPositionY":67.89,"jobPositionZ":-1.23,"jobPositionA":0.0}
```

**Behavior:**
- Returns machine and job positions for X, Y, Z, and A in a single response.
- Intended as the low-overhead position polling endpoint.

### 7.2 `machinePositionX`

**Request:**
```json
{"type":"get","getType":"machinePositionX"}
```

**Response example:**
```json
{"result":"success","machinePositionX":12.345}
```

### 7.3 `machinePositionY`

**Request:**
```json
{"type":"get","getType":"machinePositionY"}
```

**Response example:**
```json
{"result":"success","machinePositionY":67.89}
```

### 7.4 `machinePositionZ`

**Request:**
```json
{"type":"get","getType":"machinePositionZ"}
```

**Response example:**
```json
{"result":"success","machinePositionZ":-1.23}
```

### 7.4.1 `machinePositionA`

**Request:**
```json
{"type":"get","getType":"machinePositionA"}
```

**Response example:**
```json
{"result":"success","machinePositionA":180.0}
```

### 7.5 `machinePositionXYZ`

**Request:**
```json
{"type":"get","getType":"machinePositionXYZ"}
```

**Response example:**
```json
{"result":"success","machinePositionX":12.345,"machinePositionY":67.89,"machinePositionZ":-1.23}
```

### 7.6 `jobX`

**Request:**
```json
{"type":"get","getType":"jobX"}
```

**Response example:**
```json
{"result":"success","jobPositionX":12.345}
```

### 7.7 `jobY`

**Request:**
```json
{"type":"get","getType":"jobY"}
```

**Response example:**
```json
{"result":"success","jobPositionY":67.89}
```

### 7.8 `jobZ`

**Request:**
```json
{"type":"get","getType":"jobZ"}
```

**Response example:**
```json
{"result":"success","jobPositionZ":-1.23}
```

### 7.8.1 `jobA`

**Request:**
```json
{"type":"get","getType":"jobA"}
```

**Response example:**
```json
{"result":"success","jobPositionA":0.0}
```

### 7.9 `jobXYZ`

**Request:**
```json
{"type":"get","getType":"jobXYZ"}
```

**Response example:**
```json
{"result":"success","jobPositionX":12.345,"jobPositionY":67.89,"jobPositionZ":-1.23}
```

### 7.10 `deviceId`

**Request:**
```json
{"type":"get","getType":"deviceId"}
```

**Response example:**
```json
{"result":"success","deviceId":"4YzQa3T8pKm"}
```

**Notes:**
- deviceId is the RP2350 board unique ID (8 bytes) encoded as Base62 (0-9, A-Z, a-z), yielding up to 11 characters.
- Falls back to hex string (16 chars) if Base62 encoding fails.

### 7.11 `fileList`

**Request:**
```json
{"type":"get","getType":"fileList"}
```

**Response example:**
```json
{"result":"success","fileList":[{"fileName":"A.NC","fileSize":12345,"fileDate":"2026-03-26 14:30:00","fileDepth":120.5,"fileWidth":80.0,"fileHeight":8.0,"estimatedDurationSeconds":742},{"fileName":"B.nc","fileSize":67890,"fileDate":"2026-03-25 09:12:44","fileDepth":0,"fileWidth":0,"fileHeight":0,"estimatedDurationSeconds":0}]}
```

**Notes:**
- Each element in fileList is a JSON object with seven keys:
  - fileName: filename string
  - fileSize: file size in bytes (long)
  - fileDate: file modification timestamp in format YYYY-MM-DD HH:MM:SS
  - fileWidth: X-axis job size (`SIZEX`) from the prepended metadata line, or 0 if unavailable
  - fileDepth: Y-axis job size (`SIZEY`) from the prepended metadata line, or 0 if unavailable
  - fileHeight: Z-axis job size from the prepended metadata line, or 0 if unavailable
  - estimatedDurationSeconds: estimated execution duration in whole seconds from the prepended metadata line, or 0 if unavailable

**Performance note:**
- fileList intentionally does not count file lines, because scanning every file on each list request can block the main loop for too long.
- fileList reads only the first line of each NC/CNC/GCODE file to inspect the prepended metadata. Older files without that metadata return 0 for depth, width, height, and estimated duration.
- PLY point cloud files (`.ply`) are included in the list with fileDepth, fileWidth, fileHeight, and estimatedDurationSeconds all set to 0.
- Sidecar SHA-256 files named `<filename>_hash` are intentionally hidden from file listings.
- To read line counts, use the dedicated opened-file APIs after opening a specific file.

### 7.11.1 `gcodeFileHash`

**Request:**
```json
{"type":"get","getType":"gcodeFileHash","filename":"job.nc"}
```

**Success:**
```json
{"result":"success","fileName":"job.nc","fileSha256":"4a44dc15364204a80fe80e9039455cc1608281820fe2b24f1e5233ade6af1dd5"}
```

**Failures:**
```json
{"result":"failure","error":"missingJsonKey"}
{"result":"failure","error":"invalidJsonValue"}
{"result":"failure","error":"fileNotFound"}
{"result":"failure","error":"fileHashNotFound"}
```

**Notes:**
- Requires the `filename` key and only accepts `.nc` filenames.
- Returns the stored SHA-256 of the exact final saved file contents, read from the sidecar text file `<filename>_hash`.
- The hash is returned as a lowercase hexadecimal string.
- `fileHashNotFound` means the main G-code file exists but no readable sidecar hash is currently available, which can happen for older files saved before hash generation was added.

### 7.12 `openedGcodeFileName`

**Request:**
```json
{"type":"get","getType":"openedGcodeFileName"}
```

**Success:**
```json
{"result":"success","openedFileName":"job.nc"}
```

**Failure:**
```json
{"result":"failure","error":"fileNotFound"}
```

### 7.13 `jobProgress`

**Request:**
```json
{"type":"get","getType":"jobProgress"}
```

**Success:**
```json
{"result":"success","currentFileLine":123,"jobProgress":42.37,"estimatedRemainingSeconds":184}
```

**Failure:**
```json
{"result":"failure","error":"noFileOpened"}
```

**Behavior:**
- currentFileLine is the current line index sent from the opened file to GRBL.
- jobProgress is the current progress percentage, rounded to 2 decimal places.
- estimatedRemainingSeconds is computed from elapsed wall-clock time and current progress: `elapsed / progress * (100 - progress)`. Returns 0 when the job is not running or progress is 0%.
- estimatedRemainingSeconds reflects actual machine speed and automatically accounts for pauses and feed-rate overrides.

### 7.14 `openedGcodeFileTotalLines`

**Request:**
```json
{"type":"get","getType":"openedGcodeFileTotalLines"}
```

**Success:**
```json
{"result":"success","totalFileLines":5420}
```

**Failure:**
```json
{"result":"failure","error":"noFileOpened"}
```

**Behavior:**
- Returns the total number of lines in the currently opened file.

### 7.14.1 `nextFileDownloadChunk`

**Request:**
```json
{"type":"get","getType":"nextFileDownloadChunk"}
```

**Success:**
```json
{"result":"success","fcl":"G0 X0 Y0|G1 X10 Y10|","currentFileLine":2,"downloadedBytes":20,"eof":false}
```

**Final chunk example:**
```json
{"result":"success","fcl":"M30|","currentFileLine":3,"downloadedBytes":24,"eof":true}
```

**Failure:**
```json
{"result":"failure","error":"noFileDownloadInProgress"}
{"result":"failure","error":"fileNotFound"}
```

**Behavior:**
- Returns the next chunk from the active download session.
- Uses the same `fcl` line separator format as file upload chunks.
- `currentFileLine` is the total number of lines delivered so far in the download session. It only advances once the chunk has been confirmed sent, so it reflects confirmed progress.
- `downloadedBytes` is the byte position in the SD file after this chunk (it counts skipped comment/blank lines and line endings too, so it reaches `fileSize` on the last chunk). Progress = `downloadedBytes / fileSize`. This works for every file size, unlike `totalFileLines`, which is `0` for files over 32 KB. A re-sent chunk (see below) reports the same `downloadedBytes` as the failed attempt.
- When `eof` is true, the firmware closes the download session automatically after sending the response.
- The SD read is retried up to `DOWNLOAD_CHUNK_READ_MAX_ATTEMPTS` (currently 3, compile-time configurable in `Config.h`) times before giving up, so a transient SD-read glitch does not kill the transfer. The read only fails before any line is appended, so retrying re-reads the same file position without duplicating or skipping data. If every attempt fails, the response is `failure`/`fileNotFound` and the session is closed. Read retries are logged when `debuggingJson` is enabled.
- The WebSocket send is never re-issued in place. `sendWebSocketMessage` runs a stateful, non-idempotent AT handshake (`AT+WSSEND` → `>` → data → `SEND OK`); re-issuing it back-to-back after an ambiguous failure desyncs the AT command/data stream and breaks the link for the rest of the session. Instead, on a send failure the firmware **rewinds the download file to the start of the failed chunk and keeps the session open**, so the client can simply re-request the next chunk and receive the exact same chunk again — a fresh AT command issued after the ESP has settled. The `currentFileLine` progress counter is only advanced once a send succeeds, so a re-requested chunk reports the same `currentFileLine` as the failed attempt (clients should treat that as the authoritative resume point rather than appending blindly).
  - A consecutive-failure cap, `DOWNLOAD_CHUNK_SEND_MAX_FAILURES` (currently 5, compile-time configurable), bounds this: after that many failures on the same chunk the transfer is abandoned and the session closed, so a genuinely dead link does not loop forever. The counter resets on every successful chunk. Send failures are logged when `debuggingJson` is enabled.
- Because each re-request is a separate request/response round-trip (never a back-to-back resend), a single transient WiFi/ESP hiccup mid-download is recovered without restarting the whole file. The per-send AT timeout (`ESP32C3_WEBSOCKET_SEND_TIMEOUT_MS`) is 4000 ms.
- Client requirement: when a send fails the client receives **no response at all** (the failure happens below the JSON layer), so the recovery relies on the client. The client should treat a missing chunk response as a timeout and simply re-issue `nextFileDownloadChunk`; it will receive the rewound chunk. Dedupe by `currentFileLine` so a re-requested chunk (same `currentFileLine`) is not appended twice. After `DOWNLOAD_CHUNK_SEND_MAX_FAILURES` consecutive misses the session is closed and the client must restart the download from `startFileDownload`.

### 7.16 `getDebuggingSettings`

**Request:**
```json
{"type":"get","getType":"getDebuggingSettings"}
```

**Response example:**
```json
{"result":"success","debuggingBase":true,"debuggingSpindle":false,"debuggingStatus":false,"debuggingMacroBuffer":false,"debuggingSDCard":false,"debuggingJson":false,"debuggingESP32":false,"debuggingEEPROM":false,"debuggingJob":false,"benchmarking":false,"benchmarkMinimumMs":0}
```

### 7.17 `currentSpindleRPM`

**Request:**
```json
{"type":"get","getType":"currentSpindleRPM"}
```

**Response example:**
```json
{"result":"success","currentSpindleRPM":12000}
```

**Behavior:**
- currentSpindleRPM is updated from the second value in the GRBL `FS:feed,spindle` field whenever a status report is received.

### 7.18 `currentFeedRate`

**Request:**
```json
{"type":"get","getType":"currentFeedRate"}
```

**Response example:**
```json
{"result":"success","currentFeedRate":1500}
```

**Behavior:**
- currentFeedRate is updated from the first value in the GRBL `FS:feed,spindle` field whenever a status report is received.

### 7.19 `getCpuaPassthroughStatus`

**Request:**
```json
{"type":"get","getType":"getCpuaPassthroughStatus"}
```

**Response:**
```json
{"result":"success","cpuaPassthroughStatus":true}
```

### 7.21 `getJsonSettings`

**Request:**
```json
{"type":"get","getType":"getJsonSettings"}
```

**Response:**
```json
{"result":"success","enableJsonResponse":true}
```

### 7.21.2 `firmwareVersion`

**Request:**
```json
{"type":"get","getType":"firmwareVersion"}
```

**Response example:**
```json
{"result":"success","firmwareVersion":"0.8.52"}
```

**Notes:**
- Returns the firmware version string (read-only).
- Value is initialized at boot from the compiled default.
- Default value is `0.7.1.0`.
- This is a read-only configuration value and cannot be modified via the API.

### 7.22 `getFeedRateOverride`

**Request:**
```json
{"type":"get","getType":"getFeedRateOverride"}
```

**Response example:**
```json
{"result":"success","feedRateOverride":100}
```

**Notes:**
- Returns the locally cached feed rate override percentage (10–200).
- Initialised to 100 on boot. Updated each time setFeedRateOverride is called.

### 7.23 `getSpindleOverride`

**Request:**
```json
{"type":"get","getType":"getSpindleOverride"}
```

**Response example:**
```json
{"result":"success","spindleOverride":100}
```

**Notes:**
- Returns the locally cached spindle override percentage (10–200).
- Initialised to 100 on boot. Updated each time setSpindleOverride is called.
- The reachable low end depends on the programmed speed: `setSpindleOverride` refuses a decrease
  that would take the spindle below `SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM` (3000 rpm). See [5.28](#528-setspindleoverride).

### 7.24 `electricalCurrent`

**Request:**
```json
{"type":"get","getType":"electricalCurrent"}
```

**Success:**
```json
{"result":"success","electricalCurrentmA":123.45}
```

**Failure:**
```json
{"result":"failure","error":"currentSensorNotInitialized"}
```

**Notes:**
- Returns the instantaneous electrical current measured by the INA220/INA219-compatible sensor.
- `electricalCurrentmA` is expressed in milliampere (mA).
- The sensor is initialized at boot on I2C pins SDA=GPIO 12 and SCL=GPIO 13 using address `0x40`.

### 7.26 `laserSensorEnabled`

**Request:**
```json
{"type":"get","getType":"laserSensorEnabled"}
```

**Success:**
```json
{"result":"success","laserSensorEnabled":false}
```

**Notes:**
- Returns the current GPIO output state of `LASER_SENSOR_ENABLE_PIN`.
- `true` means the pin is driven HIGH; `false` means it is driven LOW.
- This reports the live enable pin state only.
- It is independent from `distanceLaserSensorConnected`, which is the cached API presence flag.
- It matches `status.laserSensorEnabled` (both now report the same live pin state).
- A `true` value here does not guarantee that the laser sensor has already completed initialization and is ready for live reads or edge probing.

### 7.29.2 `getMachineUsage`

**Request:**
```json
{"type":"get","getType":"getMachineUsage"}
```

**Response:**
```json
{"result":"success","powerOnHours":123.45,"runHours":67.8,"spindleHours":42.1,"jobCount":57,"usageDataValid":true}
```

**Notes:**
- Read-only usage meter. There is intentionally no API to set or reset these counters.
- `powerOnHours` accumulates whenever the machine is powered; `runHours` accumulates while `machineStatus` is `Run`; `spindleHours` accumulates while the spindle is spinning (RPM ≥ `MINIMUM_SPINDLE_RPM`). All are reported in hours (stored internally as seconds).
- `jobCount` is the lifetime number of jobs launched (incremented by `startJob`).
- The counters are persisted to a hidden file on the SD card (`.usage`) every `USAGE_FLUSH_INTERVAL_MS` (currently 5 minutes), and `jobCount` is additionally persisted immediately on each launch. The file is named with a leading dot so it is excluded from the file-list / download / delete API.
- The file stores two records (A/B slots), each protected by a CRC32 and selected by sequence number, so a torn write during a flush cannot corrupt the meter. `usageDataValid` is `true` when a valid record was loaded at boot; if the file is missing or fails its CRC32, the meter starts from zero and `usageDataValid` is `false`.

### 7.29.3 `getJobHistory`

**Request:**
```json
{"type":"get","getType":"getJobHistory","rangeStart":101,"offset":0}
```

**Response:**
```json
{"result":"success","rangeStart":101,"rangeEnd":200,"offset":0,"jobCount":257,"returned":40,"hasMore":true,"jobHistory":[{"startTime":"2026-06-10 14:32:00","fileName":"part_a.nc"}, ...]}
```

**Notes:**
- Read-only. Full job history is stored across rotated hidden files of 100 jobs each (`.jobs_1-100`, `.jobs_101-200`, …); this call returns the entries of one such range, oldest-to-newest. There is no API to modify or clear the history.
- `rangeStart` (optional) selects which 100-job file to read. Any value is snapped down to the block start (e.g. 150 → 101). If omitted, the latest (current) range is returned. `rangeEnd` echoes `rangeStart + 99`.
- `jobCount` is the lifetime total of jobs launched, so the client can derive how many ranges exist (ranges `1-100` … up to the block containing `jobCount`).
- Paging within a range: a single response is bounded by the ~4 KB JSON buffer, so a full 100-job range with long file names may not fit at once. `returned` is how many entries this response contains and `hasMore` is `true` if more remain in this range; re-request the same `rangeStart` with `offset` advanced by `returned` to fetch the rest. With typical (short) file names a whole range usually fits in one call (`hasMore:false`).
- Each entry has `startTime` (the verbatim client-supplied value from `startJob`, possibly empty) and `fileName` (the job file that was launched).
- A range that has no jobs yet (file missing) returns an empty `jobHistory` with `returned:0`.

### 7.29.4 `getJobCount`

**Request:**
```json
{"type":"get","getType":"getJobCount"}
```

**Response:**
```json
{"result":"success","jobCount":57}
```

**Notes:**
- Read-only. Returns just the lifetime job counter (number of jobs launched), without the rest of the usage meter. The same value is also included in `getMachineUsage`.
- Incremented by `startJob` and persisted to the hidden `.usage` SD file. There is no API to set or reset it.

### 7.29.5 `getSpindleType`

**Request:**
```json
{"type":"get","getType":"getSpindleType"}
```

**Response:**
```json
{"result":"success","spindleType":"24V"}
```

**Notes:**
- Read-only. Reports which spindle variant the firmware was built for: `"24V"` or `"48V"`.
- The value is a compile-time constant (`INSTALLED_SPINDLE_TYPE` in `Config.h`, of enum type `SpindleType`); it never changes at runtime and there is no set API. Building a firmware for the other spindle requires changing that define and reflashing.
- Intended for the calling application to adapt its behavior (UI, limits, workflows) to the installed spindle. The firmware itself uses it to pick the rotary Y target of the `moveToRotaryPosition` macro (see [5.5](#55-movetorotaryposition)).

### 7.29.5.1 `getOpenedFileBounds`

**Request:**
```json
{"type":"get","getType":"getOpenedFileBounds"}
```

**Response example:**
```json
{"result":"success","openedFileName":"Top_Traces.nc","fileMinXmm":-88.0,"fileMaxXmm":0.0,"fileMinYmm":-55.0,"fileMaxYmm":0.0}
```

**Failures:**
```json
{"result":"failure","error":"fileNotFound"}
```

**Notes:**
- Returns the opened file's toolpath extent in job coordinates, read from its `MINX`/`MAXX`/`MINY`/`MAXY`
  metadata. Unlike the width/depth figures in `fileList`, these say WHERE the toolpath sits, not only
  how big it is.
- Fails with `fileNotFound` when no file is open, or when the file was written before MIN/MAX metadata
  existed. Both cases mean the same thing for a caller: the toolpath's position is unknown, and it
  must not be guessed from the sizes.
- A flipped board reads as negative X, e.g. `fileMinXmm: -88.0, fileMaxXmm: 0.0` with job zero at the
  top-right corner. Nothing about that is exceptional; the map follows it.
- To answer "does the map cover this job", prefer `heightMapCoversOpenedJob` from `getHeightMapStatus`
  over deriving it from these numbers — it is the same check `startJob` enforces, so it cannot drift.

### 7.32 `getMaterialCenterPosition`

**Request:**
```json
{"type":"get","getType":"getMaterialCenterPosition"}
```

**Response example (available):**
```json
{"result":"success","materialCenterValid":true,"materialCenterX":-2.3,"materialCenterY":0.0}
```

**Response example (not available yet):**
```json
{"result":"success","materialCenterValid":false}
```

**Notes:**
- Returns only the material center detected by the latest successful `probeLaserMaterialBounds` routine.
- When a new `probeLaserMaterialBounds` starts, the previous stored center is cleared immediately.
- `materialCenterValid` is `false` until the current detection finishes successfully.

## 8. File chunk upload API

File chunks are sent as a JSON line with key "fcl" (USB serial and WebSocket).

**Prerequisite:**
- startFileTransfer must have completed successfully.

**Chunk format / sender contract (important):**
- G-code lines inside the "fcl" value MUST be separated by "|" (LINE_CHUNK_SEPARATOR), never by literal newlines. Each chunk message is therefore one single-line JSON object.
- The declared `filelines` (from startFileTransfer) MUST exactly equal the number of "|"-separated tokens the firmware will parse across all chunks. Two consequences of the token-based counting:
  - Blank lines: the splitter collapses empty tokens, so a blank line ("A||B") is NOT counted. Either drop blank lines before computing `filelines` and before chunking (recommended), or do not emit empty tokens. A mismatch here means the file never finalizes and the upload aborts on the chunk-timeout watchdog.
  - A line must not itself contain "|", or it will be over-split (counted as multiple lines), finalizing the file truncated.
- Send one chunk at a time and wait for its {"result":"success"} response before sending the next. The controller's chunk-activity watchdog aborts the upload if no chunk is processed within FILE_TRANSFER_CHUNK_TIMEOUT_MS (currently 5000 ms).
- Keep each chunk's full JSON message under ~7 KB. Over WebSocket the controller reassembles fragmented +WS_DATA frames (see [12.4](#124-incoming-message-framing)), but the reassembled message must fit in the 8192-byte buffer.

**Write behavior:**
- fcl payload is split by "|" (LINE_CHUNK_SEPARATOR).
- Each parsed line is written to the transfer temp file.
- While each chunk is processed, the firmware also computes job bounds metadata and an estimated duration in whole seconds.
- transferFileLines decrements per written line.
- When transferFileLines reaches 0:
  - write file is closed
  - a metadata comment is prepended to the final file
  - the temp upload file is removed

**Responses during chunk transfer:**
- Intermediate success (chunk stored, more expected):
  `{"result":"success","linesRemaining":N}`
  N is how many lines the controller still expects. The client can watch this to confirm the count is converging to 0; if it sends its last chunk and N is still > 0, there is a filelines mismatch (see sender contract).
- Final success (last line written, file finalized):
  `{"result":"success","linesRemaining":0,"fileWritten":"success"}`
- Failure. The `error` value identifies the cause, so a rejected job can be told apart from an SD/IO problem:
  - `fileDimensionsExceeded` — the job is larger than the work area. Carries the measured size and the per-axis limits (see section [5.12](#512-startfiletransfer)):
    `{"result":"failure","error":"fileDimensionsExceeded","fileWidth":180.5,"fileDepth":92.0,"fileHeight":12.4,"maxFileWidth":150,"maxFileDepth":150,"maxFileHeight":70}`
  - `fileWriteFailed` — an SD write failed, either while storing a chunk or while copying the temp file into the final one. Typically a full or failing card.
  - `fileReadFailed` — the temporary upload file could not be reopened during finalization.
  - `fileCreationFailed` — the final file or its `_hash` sidecar could not be created.
  - `fileHashFailed` — SHA-256 hashing of the final file failed.
  - `fileTransferNotStarted` — a chunk arrived with no upload in progress (no `startFileTransfer`, or the transfer was already aborted).
  - `missingJsonKey` — the chunk message carried no usable `fcl` payload.
  - `fileFinalizeFailed` — finalization failed for a cause not covered above (defensive fallback; not expected in normal operation).
  On any of these, no final file is saved and the temporary file is removed.
- Chunk-timeout watchdog abort (no chunk processed within the timeout; pushed to the channel that started the upload):
  `{"result":"failure","error":"fileTransferTimeout","linesRemaining":N}`
  N > 0 indicates the controller was still waiting for lines — almost always a filelines/line-count mismatch.

---

## 9. MsgPack API format (removed)

The MsgPack/COBS binary file-chunk transport has been removed. File chunks are now
transferred exclusively as JSON lines with the "fcl" key on both USB serial and
WebSocket (see section [8](#8-file-chunk-upload-api)). Earlier firmware reserved a binary MsgPack framing path
for USB serial, but it was never enabled and has been deleted.

---

## 10. Unknown or malformed requests

- Unknown/missing top-level type:
  `{"result":"failure","error":"unknownJsonType"}`
- Unknown/missing commandType for type=command:
  `{"result":"failure","error":"unknownJsonCommandType"}`
- Unknown/missing setType for type=set:
  `{"result":"failure","error":"unknownJsonType"}`
- Unknown/missing getType for type=get:
  `{"result":"failure","error":"unknownJsonType"}`

---

## 11. Practical examples

**Health checks:**
```json
{"type":"serialPing"}
{"type":"websocketPing"}
```

**Open and start a job:**
```json
{"type":"command","commandType":"openGcodeFile","filename":"demo.nc"}
{"type":"command","commandType":"startJob"}
```

**Start upload + JSON chunks:**
```json
{"type":"command","commandType":"startFileTransfer","filename":"demo.nc","filelines":3,"fileDate":"2026-03-26 14:30:00"}
{"fcl":"G0 X0 Y0|G1 X10 Y10|"}
{"fcl":"M30|"}
```

**Rename a G-code file:**
```json
{"type":"command","commandType":"renameGcodeFile","filename":"old-job.nc","newFilename":"new-job.nc"}
```

**Laser sensor and offset zero:**
```json
{"type":"command","commandType":"enableLaserSensor"}
{"type":"command","commandType":"disableLaserSensor"}
{"type":"command","commandType":"probeLaserMaterialBounds"}
{"type":"command","commandType":"confirmMaterialCenterAndSetZero"}
{"type":"command","commandType":"moveToLaserOffsetZero"}
{"type":"set","setType":"setLaserOffsetZeroSettings","laserOffsetZeroXBackMm":40.3,"laserOffsetZeroYBackMm":6.2}
{"type":"get","getType":"getLaserOffsetZeroSettings"}
{"type":"get","getType":"getMaterialCenterPosition"}
```

**Brushless motor accessory:**
```json
{"type":"set","setType":"setBrushlessMotorSettings","brushlessMotorDirectionReversed":true}
{"type":"get","getType":"getBrushlessMotorSettings"}
{"type":"get","getType":"getOpenedFileBounds"}
```

**Height map:**
```json
{"type":"command","commandType":"startHeightMapScan","heightMapSizeXmm":120.0,"heightMapSizeYmm":80.0,"heightMapDensity":"coarse"}
{"type":"command","commandType":"startHeightMapScan","heightMapUseOpenedFileSize":true,"heightMapDensity":"fine"}
{"type":"get","getType":"getHeightMapStatus"}
{"type":"get","getType":"getHeightMapPoints"}
{"type":"get","getType":"getHeightMapPoints","heightMapPointsStartIndex":80,"heightMapPointsMaxCount":40}
{"type":"command","commandType":"enableHeightMapCompensation"}
{"type":"command","commandType":"disableHeightMapCompensation"}
{"type":"command","commandType":"clearHeightMap"}
{"type":"command","commandType":"abortHeightMapScan"}
```

**Point cloud scan:**
```json
{"type":"command","commandType":"startPointCloudScan","filename":"part_top","pointCloudSizeXmm":80.0,"pointCloudSizeYmm":60.0,"pointCloudDensity":"medium","fileDate":"2026-06-04 14:30:00"}
{"type":"get","getType":"getPointCloudStatus"}
{"type":"command","commandType":"abortPointCloudScan"}
// After scan completes, download the PLY file:
{"type":"command","commandType":"startFileDownload","filename":"part_top.ply"}
{"type":"get","getType":"nextFileDownloadChunk"}
// Repeat nextFileDownloadChunk until eof:true
```

**Set job coordinates:**
```json
{"type":"set","setType":"setJobX","jobX":12.3}
{"type":"set","setType":"setJobY","jobY":45.6}
{"type":"set","setType":"setJobZ","jobZ":-1.2}
{"type":"set","setType":"setJobA","jobA":0.0}
{"type":"set","setType":"setJobXYZ","jobX":12.3,"jobY":45.6,"jobZ":-1.2}
```

**Move machine:**
```json
{"type":"command","commandType":"moveX","x":10.5,"feedRate":1000}
{"type":"command","commandType":"moveY","y":20.0,"feedRate":1000}
{"type":"command","commandType":"moveZ","z":-2.5,"feedRate":500}
{"type":"command","commandType":"moveXYZ","x":10.5,"y":20.0,"z":-2.5,"feedRate":1000}
{"type":"command","commandType":"moveMachineX","x":10.5,"feedRate":1000}
{"type":"command","commandType":"moveMachineY","y":20.0,"feedRate":1000}
{"type":"command","commandType":"moveMachineZ","z":-2.5,"feedRate":500}
{"type":"command","commandType":"moveMachineXYZ","x":10.5,"y":20.0,"z":-2.5,"feedRate":1000}
{"type":"command","commandType":"moveA","a":90.0,"feedRate":500}
```

**Settings:**
```json
{"type":"set","setType":"setWifiSettings","apWifiSSID":"PiccolaCNC_AP","apWifiPassword":"12345678","clientWifiSSID":"OfficeWiFi","clientWifiPassword":"officePass"}
{"type":"set","setType":"setDebuggingJson","debuggingJson":true}
{"type":"set","setType":"setBenchmarkMinimumMs","benchmarkMinimumMs":10}
{"type":"set","setType":"setJsonSettings","jsonResponse":true}
{"type":"set","setType":"setMachineName","machineName":"Akkura #2"}
{"type":"set","setType":"setBedSettings","vacuumBed":true,"spindle500W":true}
{"type":"set","setType":"setToolLengthReferences","ref500WStandard":-48.5,"ref500WVacuum":-46.0,"ref120WStandard":-52.0,"ref120WVacuum":-49.5}
```

**Reads:**
```json
{"type":"get","getType":"status"}
{"type":"get","getType":"positions"}
{"type":"get","getType":"machinePositionXYZ"}
{"type":"get","getType":"machinePositionA"}
{"type":"get","getType":"jobXYZ"}
{"type":"get","getType":"jobA"}
{"type":"get","getType":"jobProgress"}
{"type":"get","getType":"openedGcodeFileTotalLines"}
{"type":"get","getType":"nextFileDownloadChunk"}
{"type":"get","getType":"deviceId"}
{"type":"get","getType":"getWifiSettings"}
{"type":"get","getType":"getDebuggingSettings"}
{"type":"get","getType":"getJsonSettings"}
{"type":"get","getType":"machineName"}
{"type":"get","getType":"electricalCurrent"}
{"type":"get","getType":"laserSensorEnabled"}
{"type":"get","getType":"distanceLaserMm"}
{"type":"get","getType":"getToolLength"}
{"type":"get","getType":"getHeightMapStatus"}
{"type":"get","getType":"getBedSettings"}
{"type":"get","getType":"getToolLengthReferences"}
```

**Probe tool length:**
```json
{"type":"command","commandType":"probeToolLengthMacro"}
// Event:
{"type":"probeToolLengthCompleted","result":"success","toolLength":12.5}
```

**Soft reset:**
```json
{"type":"command","commandType":"grblSoftReset"}
```

**Download:**
```json
{"type":"command","commandType":"startFileDownload","filename":"demo.nc"}
{"type":"get","getType":"nextFileDownloadChunk"}
{"type":"command","commandType":"abortFileDownload"}
```

---

## 12. WebSocket transport

> **Not available on Control Board V2.** This section describes the ESP32-C3 WiFi
> co-processor used by the Piccola CNC. This board has no radio of any kind — networking
> is a wired W5500 Ethernet controller on CPU-B's SPI0, which is not yet ported. The
> section is kept for reference because parts of the websocket plumbing still exist in the
> firmware, but none of it has a transport underneath it today.

### 12.1 Overview

The CPUB communicates with the outside world over WebSocket via an ESP32-C3 co-processor using AT commands (Espressif ESP-AT firmware). The ESP32-C3 acts as a WiFi bridge:
- It optionally creates a WiFi Access Point (AP) for direct device connections (only when the hotspot is enabled).
- It joins a client WiFi network.
- It opens a single WebSocket connection to a mobile client.

**The connection uses ESP-AT link ID 1:**
- link_id=1: Mobile WebSocket (mobileWebsocketConnection) — server address discovered via BLE advertisement (see section [12.8](#128-mobile-server-discovery-via-ble)).

Note: the legacy PC WebSocket on link_id=0 (server address resolved via mDNS using the board device ID) has been removed. Only link_id=1 is used now.

Once connected, any JSON message received on the WebSocket link is processed using the same JSON API described in sections [2](#2-common-json-envelope)–[8](#8-file-chunk-upload-api) above. Non-JSON text received on the WebSocket link is forwarded to CPUA when CPUA passthrough is enabled. Firmware responses are routed back to the link that originated the request.

### 12.2 Network configuration

WiFi and WebSocket settings are persisted in EEPROM. Defaults are defined in Config.h:

| Setting                          | Default value                      |
|----------------------------------|------------------------------------|
| AP SSID                          | `PiccolaCNC_<boardDeviceId>`       |
| AP Password                      | `minicnc123`                       |
| Client WiFi SSID                 | `Client SSID`                      |
| Client WiFi Password             | `Client Password`                  |
| Mobile WebSocket server URL      | `ws://<IP from BLE>:8765`          |

The `setWifiSettings` command that changed these at runtime has been removed, along with the WiFi
credential record in EEPROM; this board has no radio to configure.

**Mobile WebSocket server URL:**
- Discovered at runtime via BLE scan (see section [12.8](#128-mobile-server-discovery-via-ble)). Not configurable via setWifiSettings.
- The suffix is defined by DEFAULT_ESP32C3_MOBILE_WEBSOCKET_SERVER_SUFFIX in Config.h (default: ":8765").

### 12.3 ESP32-C3 initialization sequence

After boot (or after a setWifiSettings command), the CPUB runs the following AT command sequence against the ESP32-C3. The exact steps depend on whether the hotspot (AP) is enabled.

**With hotspot enabled (6 steps):**

| Step | AT command | Purpose |
|------|------------|---------|
| 1 | `AT` | Verify the module is alive |
| 2 | `AT+WSCLOSE=1` | Close any stale mobile WebSocket from a previous session — ERROR is expected and tolerated |
| 3 | `AT+CWMODE=3` | Dual mode: AP + Station |
| 4 | `AT+CWSAP="<ssid>","<pw>",1,3` | Start access point |
| 5 | `AT+WSCFG=1,3,2,8192` | Configure mobile WebSocket: link 1, 8192-byte buffer |
| 6 | `AT+CWJAP="<ssid>","<pw>"` | Connect to client WiFi — failure is retried |

**With hotspot disabled (5 steps):** the `AT+CWSAP` step is skipped and `AT+CWMODE=1` (Station only) is used instead of `AT+CWMODE=3`:

| Step | AT command | Purpose |
|------|------------|---------|
| 1 | `AT` | Verify the module is alive |
| 2 | `AT+WSCLOSE=1` | Close any stale mobile WebSocket |
| 3 | `AT+CWMODE=1` | Station only |
| 4 | `AT+WSCFG=1,3,2,8192` | Configure mobile WebSocket: link 1, 8192-byte buffer |
| 5 | `AT+CWJAP="<ssid>","<pw>"` | Connect to client WiFi |

Each step waits for an OK before proceeding. The AT+WSCLOSE=1 cleanup step tolerates an ERROR (it just means no link was open). An ERROR on the client-WiFi join step (AT+CWJAP) is retried automatically. Any other ERROR resets the sequence from the beginning.

The response reader drains all available serial bytes in a single pass per loop iteration, so multi-byte responses (e.g. "OK\r\n") are consumed in one call rather than one byte per timer tick. This significantly reduces the time to complete the init sequence and open the WebSocket connection.

After successful initialization the CPUB continuously pursues the single mobile WebSocket connection (link_id=1):

```
AT+WSOPEN=1,"<mobile_websocket_server_url>"
```

The mobile server address is discovered via a BLE scan first (see section [12.8](#128-mobile-server-discovery-via-ble)), then `AT+WSOPEN=1` is issued. Only one AT command is sent at a time. While a connection attempt is in progress, `checkForResponse()` retains ownership of live ESP32 serial bytes until the final `OK` or `ERROR` arrives. Any intermediate WebSocket lines received during that window are buffered and then processed by the normal WebSocket handler on subsequent loop iterations.

**Current timing:**
- Each mobile `AT+WSOPEN` attempt is allowed up to `MOBILE_WEBSOCKET_CONNECTING_TIMEOUT_MS` (currently 10000 ms).
- Retry scheduling while idle uses `MOBILE_WEBSOCKET_RETRY_INTERVAL_MS` (currently 4000 ms) in `Config.h`.

After any WebSocket disconnection or watchdog-forced close, the firmware returns to the idle state and resumes the BLE scan + mobile `AT+WSOPEN` pursuit. The firmware continues to scan for the mobile BLE advertisement and attempt the mobile connection even while a USB/serial client is connected.

### 12.4 Incoming message framing

The ESP32-C3 delivers received WebSocket frames with the following prefix (ESP-AT notation):

```
+WS_DATA:<link_id>,<data_len>,<data>\r\n
```

Only link_id=1 (Mobile WebSocket) is used.

The firmware receive path is length-prefix-aware: it parses `<data_len>` and reads exactly that many payload bytes for each +WS_DATA frame, regardless of any commas or newlines inside the data.

Frame reassembly: a single application message (e.g. a large file-chunk JSON) may be split by the ESP32-C3 across several consecutive +WS_DATA frames. The firmware reassembles them into one message buffer and only delivers the message once it is complete — defined as a balanced top-level JSON object (string-aware), or a single non-JSON line. This prevents partial fragments of a chunk from being mis-parsed (and, before reassembly, from leaking to CPUA as malformed G-code). The reassembled message must fit within ESP32C3_WEBSOCKET_MESSAGE_BUFFER_SIZE (8192 bytes); larger messages are discarded. Senders should therefore keep each WebSocket message comfortably under ~7 KB.

A disconnection event from the server arrives as:

```
+WS_CLOSED:<link_id>\r\n
```

For link_id=1 (mobile): sets isMobileWebSocketConnected = false, clears the discovered mobile endpoint, and resumes the BLE scan + mobile reconnect flow.

If the ESP32 reports `WIFI DISCONNECT`, the firmware tears down the WebSocket state, clears BLE/mobile discovery state, and re-enters the ESP32 setup flow at the client WiFi join step so a fresh `AT+CWJAP="<ssid>","<pw>"` retry is issued. This `WIFI DISCONNECT` detection runs continuously, even while no mobile WebSocket is connected.

### 12.5 Message API over WebSocket

The WebSocket channel supports the same JSON message API as USB serial (sections [2](#2-common-json-envelope)–[8](#8-file-chunk-upload-api)). Both channels share the same handler code.

If a WebSocket line is not valid JSON and CPUA passthrough is enabled, that text line is forwarded to CPUA the same way as non-JSON USB input. Two exceptions are never forwarded to CPUA: lines beginning with `{` (a JSON message that failed to deserialize), and any line received while a file upload is in progress (it can only be a stray chunk fragment, not real G-code).

**Spindle ramping note:**
- CPUB mirrors GRBL's modal spindle words, so the ramp engages regardless of how the CAM splits `M3` and `S` across lines (same line, `S` first then `M3`, or `S` alone while the spindle is already running). Both non-JSON passthrough lines and G-code lines read from SD jobs go through the same interception.
- Modal rules:
  - `S` received while the spindle is off: the value is memorized for the next `M3` and the line is forwarded untouched (harmless with the spindle off).
  - `M3` (with or without an `S` on the same line): CPUB ramps from the last commanded RPM to the attached or memorized `S` value using repeated `M3 S...` steps. An `M3` with no `S` ever seen behaves like `M3 S0` and is forwarded as-is.
  - `S` received while the spindle is on: an increase is ramped; a decrease (or equal value) is forwarded directly — instant, no ramp — and aborts any in-flight ramp. Upward bumps smaller than one ramp increment (`MANUAL_SPINDLE_RAMP_RPM_INCREMENT`) are also forwarded directly, so small speed tweaks never cause a motion dwell.
  - `M5`/`M30`: clears ALL modal spindle state, including the memorized `S` (it does not survive M5). `M4` is not supported by the ramp and passes through untouched — but it *is* recognised as a spindle start by the door interlock's gate below, so it is blocked when that gate is closed.
- Spindle-start gate: none of the above runs while a job is paused. (The housing/lid half of this gate was removed with the door interlock; `isSpindleStartBlocked()` now keys on job-paused alone.) In that state no `M3`/`M4` (and no `S` that would raise a running spindle) reaches GRBL from any source — job file, passthrough, or macro — and no ramp is started. `M5`/`M30` always pass.
- Lines that carry more than spindle words (e.g. `G1 X10 S9000`) are never consumed: the line is held, the ramp runs first (motion waits for the spindle, up to `MANUAL_SPINDLE_RAMP_TOTAL_TIME_MS`), then the held line is released through the normal pipeline (height-map compensation, soft limits, line counting) exactly once. Pure spindle lines (`M3`/`S`/`N` words only) are consumed outright and replaced by the ramp steps.
- The ramp base is always the last RPM CPUB actually commanded, never the status-report RPM (that figure is GRBL's override-adjusted `rpm_overridden` and it lags a poll behind, so using it would cause the exact jump start the ramp is meant to prevent).
- CPUB polls GRBL for status every `ASK_STATUS_INTERVAL` (200 ms) throughout a job. The `?` is a real-time command — no line slot, no `ok` — so it is sent regardless of whether CPUB is waiting for an `ok`. It used to be sent only between an `ok` and the next line, which froze the reported state and position for as long as GRBL withheld an `ok`: a `G4` dwell, an `M3`/`M5` synchronising with the end of motion, a tool change, or any HOLD.
- CPUB waits for a GRBL-HAL `ok` after each ramp step before sending the next one; steps are additionally paced so the full ramp spans `MANUAL_SPINDLE_RAMP_TOTAL_TIME_MS`. Both values are compile-time configurable in `Config.h` (`MANUAL_SPINDLE_RAMP_RPM_INCREMENT`, `MANUAL_SPINDLE_RAMP_TOTAL_TIME_MS`).
- State hygiene: the modal spindle memory (M3 flag, memorized `S`, last commanded RPM, any held line) is cleared on `M5`/`M30`, on GRBL reset, on job stop and on job start — `startJob` additionally sends the spindle-override reset byte (0x99) so a new file always starts at 100% override and can never inherit spindle state from the console, a previous file, or an interrupted resume.
- For now this does not apply to JSON API commands, because there is no dedicated spindle start/set-speed JSON API yet. The only spindle-related JSON API currently implemented is spindle override.
- A separate paced ramp governs the spindle restore after a `resumeJob` (see [5.16](#516-resumejob)): it steps the spindle override in coarse ±10% increments, one real-time byte per `RESUME_SPINDLE_OVERRIDE_STEP_INTERVAL_MS` (currently 250 ms), configurable in `Config.h`. This override recycle is used ONLY for resume — the job-start and mid-job ramps above are always plain `M3 S...` commands, never override bytes.

The only WebSocket-specific ping type is `websocketPing`.

**Request:**
```json
{"type":"websocketPing"}
```

**Response:**
```json
{"result":"success"}
```

**Behavior:**
- Resets the WebSocket API ping watchdog for the mobile connection that sent the ping.
- Any normal traffic received on the WebSocket link also refreshes that same watchdog.
- The generic {"type":"serialPing"} also works over WebSocket and returns the same response, but it does not refresh the USB serial client watchdog.

### 12.6 WebSocket ping watchdog

A watchdog tracks activity on the mobile WebSocket connection. Any normal traffic on the link or an explicit `websocketPing` refreshes the timer. If no activity is received within `WEBSOCKET_API_PING_TIMEOUT_MS` (currently 5000 ms), the firmware issues `AT+WSCLOSE=1` to force-close the WebSocket and then resumes the BLE scan + mobile reconnect flow used after any other disconnect.

To keep a long-lived connection alive, send a periodic ping:
```json
{"type":"websocketPing"}
```

### 12.7 Binary frames over WebSocket

NOT supported. The WebSocket message handler processes only plain text lines:
JSON API requests and non-JSON text passthrough to CPUA when CPUA passthrough
is enabled. Any binary data sent via WebSocket will be silently discarded.

Use the JSON file-chunk API (fcl key, section [8](#8-file-chunk-upload-api)) to transfer files over WebSocket.

### 12.8 Mobile server discovery via BLE

The mobile WebSocket server address is not stored in EEPROM. Instead it is discovered
at runtime by scanning for a specific BLE advertisement emitted by the mobile device.

**BLE advertisement format:**

The mobile device advertises a BLE service whose local name (UUID) follows this pattern:

```
minicnc_<IP_with_underscores>
```

For example: `minicnc_192_168_178_106`

**Discovery sequence:**
1. The firmware initiates a BLE scan using `AT+BLESCAN=1` (duration defined by `ESP32C3_BLE_SCAN_DURATION_S`, currently 3 seconds), preceded by a BLE de-init/init/scan-params setup. If any BLE setup step fails, the scan state is reset and retried after the normal retry interval.
2. Scan results are parsed for a local name beginning with the prefix `minicnc_`.
3. The suffix after the prefix (e.g. `192_168_178_106`) is captured into `mobileServerName`.
4. Underscores in `mobileServerName` are replaced with dots to form the raw IP address: `192.168.178.106`.
5. The mobile WebSocket URL is assembled as `ws://<IP>:8765` (using `DEFAULT_ESP32C3_MOBILE_WEBSOCKET_SERVER_SUFFIX` = `":8765"`).
6. `AT+WSOPEN=1,"ws://192.168.178.106:8765"` is sent to open link_id=1.

**Retry behaviour:**
- If no matching BLE advertisement is found the BLE scan state is reset
  and a new scan is scheduled after MOBILE_WEBSOCKET_RETRY_INTERVAL_MS (currently 4000 ms).
- On mobile WebSocket disconnection (`+WS_CLOSED:1`) the same retry timer is started,
  triggering a fresh BLE scan once the timer expires.
- The retry delay paces the reconnect loop so it does not flood the ESP32 serial channel with back-to-back scans.
