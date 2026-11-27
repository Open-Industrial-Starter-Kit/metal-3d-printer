"""
connection.py
Central communication hub for the Piccola CNC desktop app.

Owns SerialWorker + WsServer and translates raw JSON lines from either
transport into typed Qt signals that individual UI panels can connect to.
Background polling (status, positions, file list) is also managed here.

All signals are delivered on the Qt main thread (auto-queued).
"""

import hashlib
import json
import os
import re
import shutil
import socket
import threading
import datetime
import time
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal

from app_config import SerialConfig
from serial_worker import SerialWorker
from ws_server import WsServer
from ble_advertiser import BleAdvertiser

WS_PORT = 8765

# How long to wait for additional WiFi machines to announce themselves before
# resolving the selection (mobile-app parity). 0 machines → keep waiting;
# 1 → auto-connect; 2+ → show the chooser. A newly-seen machine restarts the
# window so a slow announcer is not missed.
MACHINE_DISCOVERY_WINDOW_MS = 5000

# While a WiFi machine is connected but not yet *selected* (the discovery window
# above, and the whole time the chooser sits open), the firmware sees no traffic
# from us and force-closes the link on its own app-level ping watchdog
# (WEBSOCKET_API_PING_TIMEOUT_MS, 5 s in firmware) — then reconnects, which churns
# the machine list and bounces the splash. So we heartbeat every candidate with
# {"type":"websocketPing"} on this interval until one is selected (after which the
# 200 ms _poll keeps the link alive). Must stay well under the firmware's 5 s
# watchdog. 1:1 with the Android app's HEARTBEAT_INTERVAL_MS / !wsConnected
# heartbeat (App.tsx ~9764).
WS_KEEPALIVE_INTERVAL_MS = 1000

# After the last upload chunk the firmware copies the temporary file to its
# final name and hashes it (finalizeTransferredFile) in one blocking loop, and
# only then acks the chunk. It reads nothing from USB or WiFi meanwhile — a
# 9 MB file takes long enough that resuming the poll after the usual 5 s chunk
# wait filled the serial buffer and the write timeouts dropped the link. So the
# last chunk gets this much longer wait, with the link kept quiet throughout.
TRANSFER_FINALIZE_TIMEOUT_S = 300.0

# Serial is the preferred transport: give it a brief head-start at launch so a
# cabled machine connects directly and the WiFi multi-machine discovery never
# engages. WiFi fallback (BLE beacon + WS discovery) only switches on if serial
# hasn't connected by the time this elapses. A cabled CPUB answers the 2 Mbaud
# serialPing in well under this window (the probe is 0.25 s per port).
SERIAL_HEADSTART_MS = 2500

# A bare {"result":"failure","error":"fileNotFound"/"noFileOpened"} is the
# firmware's normal answer to the openedGcodeFileName / jobProgress polls while
# no file is open (firmware README §7.12 / §7.13) — an expected polling miss,
# not an error. It is kept out of the raw log unless a user-initiated request
# (send()/send_raw()) is still awaiting its reply, in which case the miss is
# genuinely that request's answer and is shown. 1:1 with the Android app's
# pendingConsoleRequests / isExpectedPollingMiss gate (App.tsx ~9775). Pending
# requests older than this window stop shielding misses — their reply was
# consumed by a typed handler (or never came).
USER_REQ_REPLY_WINDOW_S = 3.0

# Firmware error → (title, message) popup shown to the user when a move/jog is
# refused (mobile-app parity, App.tsx ~9491). Throttled per-error in _route.
_MOTION_ERROR_NOTICES = {
    "softLimitExceeded": (
        "Soft Limit Triggered",
        "Soft limit has been triggered, the move has not been sent."),
    # The same soft limit, reported the GRBL way rather than as a JSON refusal:
    # keys of our own, set by _note_grbl_limit from the raw line. ALARM:2 is
    # GRBL/grblHAL's soft-limit alarm -- the motion was cut short and the
    # machine is now locked in Alarm. error:15 is the jog form: the target was
    # past travel, so nothing moved.
    "grblSoftLimitAlarm": (
        "Soft Limit Triggered",
        "The machine stopped because a move would have gone past its travel "
        "limits (soft limit). It is now in alarm. Check the work zero and that "
        "the job fits inside the machine, then unlock it before continuing."),
    "grblJogTravelExceeded": (
        "Soft Limit Triggered",
        "That move would have gone past the machine's travel limits (soft "
        "limit), so it was not run. Check the work zero and that the job fits "
        "inside the machine."),
    # A macro (Frame, Go to XY0, …) whose move the firmware's own soft-limit
    # check blocked. Reported only as raw "CPUB: Soft-limit blocked command:
    # …" / "Macro aborted by soft limits …" lines, with the machine left Idle --
    # no alarm, no JSON error. _note_grbl_limit builds the message with the
    # blocked position; this is its wording when the line carries none.
    "macroSoftLimitBlocked": (
        "Outside Machine Travel",
        "The move was not run: it would have gone past the machine's travel "
        "limits (soft limits). When framing, this means the job does not fit "
        "where the work zero is set — move the work zero, or the part, so the "
        "whole job lies inside the machine's travel."),
    "notHomed": (
        "Homing Required",
        "Please home the machine before jogging."),
    "rotaryYLock": (
        "Y Axis Locked",
        "The Y axis is locked while the rotary axis is connected."),
    # setSpindleOverride refused: the step would have taken the spindle below
    # SPINDLE_OVERRIDE_MIN_EFFECTIVE_RPM. The app disables the button before
    # this can happen, so seeing it means our view of the override or the
    # programmed speed was stale — or another client pressed it.
    "spindleRpmBelowMinimum": (
        "Spindle Override Limit",
        "The spindle override will not take the spindle below its minimum "
        "working speed. Below it the spindle loses torque instead of cutting "
        "slower — lower the speed in the job itself if you need less."),
}

# Firmware upload rejection → (title, message) popup. The firmware names the
# cause on every failed upload (README 5.12 for the start, section 8 for the
# chunk stream), and the causes are not interchangeable: a job that does not
# fit the work area is the operator's problem, an SD write failure is the
# machine's. Without this the app showed neither — see _fail_transfer.
#
# Every one of these leaves nothing saved on the machine (the firmware removes
# the temporary file), which _transfer_error_notice appends to each message.
_TRANSFER_ERROR_NOTICES = {
    # fileDimensionsExceeded is built per-axis below, from the measurements the
    # firmware sends with it; this is its fallback wording.
    "fileDimensionsExceeded": (
        "Job Too Large",
        "The toolpath does not fit the machine's work area."),
    "fileWriteFailed": (
        "Storage Write Failed",
        "The machine could not write the file to its SD card. That usually "
        "means the card is full or failing."),
    "fileReadFailed": (
        "Upload Failed",
        "The machine could not read back the uploaded data while saving it."),
    "fileCreationFailed": (
        "Upload Failed",
        "The machine could not create the file on its SD card."),
    "fileHashFailed": (
        "Upload Failed",
        "The machine could not compute the file's checksum."),
    "fileFinalizeFailed": (
        "Upload Failed",
        "The machine could not finish saving the file."),
    "fileTransferNotStarted": (
        "Upload Failed",
        "The machine was not expecting file data — the upload had already "
        "been cancelled or had timed out."),
    "fileTransferTimeout": (
        "Upload Timed Out",
        "The machine stopped receiving data and cancelled the upload."),
    "fileAlreadyExists": (
        "File Already Exists",
        "A file with this name is already on the machine. Delete it there "
        "first, or upload this one under a different name."),
    "jobAlreadyRunning": (
        "Job Running",
        "A job is running or paused on the machine. Stop it before uploading."),
    "missingJsonKey": (
        "Upload Refused",
        "The machine refused the upload request. Another transfer may still "
        "be finishing — wait a moment and try again."),
}

#: Axis label → (measured key, limit key) in a fileDimensionsExceeded payload.
#: The firmware maps SIZEX/SIZEY/SIZEZ onto width/depth/height and sends the XY
#: limit twice, once per axis, so each can be compared field by field.
_FILE_DIM_AXES = (("X", "fileWidth", "maxFileWidth"),
                  ("Y", "fileDepth", "maxFileDepth"),
                  ("Z", "fileHeight", "maxFileHeight"))


def _transfer_error_notice(err: str, payload: dict) -> tuple[str, str]:
    """(title, message) for a firmware upload rejection.

    fileDimensionsExceeded carries the measured job size and the per-axis
    limit, so it is worth spelling out which axis overruns and by how much —
    "Job Too Large" on its own leaves the operator to guess whether to rotate
    the part, re-origin it, or give up on it."""
    title, message = _TRANSFER_ERROR_NOTICES.get(
        err, ("Upload Failed", f"The machine rejected the upload ({err})."))

    if err == "fileDimensionsExceeded":
        over = []
        for axis, got_key, max_key in _FILE_DIM_AXES:
            got, lim = payload.get(got_key), payload.get(max_key)
            if (isinstance(got, (int, float)) and isinstance(lim, (int, float))
                    and got > lim):
                over.append(f"{axis} is {got:g} mm, the machine allows {lim:g} mm")
        if over:
            # One line per offending axis, no bullet glyph: InfoDialog centres
            # its message label, and centred bullets read as debris.
            message = ("The toolpath does not fit the machine's work area:\n"
                       + "\n".join(over))
    elif err == "fileTransferTimeout":
        left = payload.get("linesRemaining")
        if isinstance(left, int) and left > 0:
            message += f" It was still waiting for {left} more lines."

    return title, message + "\n\nNothing was saved on the machine."


#: Which way an axis moving in + goes, in the operator's words, for the
#: end-of-travel notice: (towards +, towards -).
_AXIS_WAYS = {"X": ("right", "left"), "Y": ("back", "forward"),
              "Z": ("higher", "lower"), "A": ("further forward", "further back")}


def _macro_soft_limit_notice(command: str,
                             current: dict | None = None,
                             job_running: bool = False) -> tuple[str, str]:
    """(title, message) for a macro move the firmware's soft-limit check blocked.

    *command* is the G-code line the firmware quoted (``G1 F1200 X-69.569
    Y-61.715``). Its axis words are the position the move was heading for, in
    work coordinates, and naming it tells the operator which corner of the job
    is outside -- the reason the notice exists. Without axis words the generic
    wording is used.
    """
    title, message = _MOTION_ERROR_NOTICES["macroSoftLimitBlocked"]
    axes = re.findall(r"\b([XYZA])\s*(-?\d+(?:\.\d+)?)", command.upper())
    if not axes:
        return title, message
    where = ", ".join(f"{a} {float(v):g}" for a, v in axes)

    # One axis means a manual move -- a jog step, the Z buttons, a
    # single-axis macro -- that ran into the end of that axis's travel. That
    # is the machine protecting itself, not a job that does not fit, so it
    # says which way the axis is stuck instead of talking about framing.
    # (Seen: Z up from the top of travel, blocked "at Z 0.1", and the popup
    # told the operator to move the work zero.)
    if len(axes) == 1:
        axis, target = axes[0][0], float(axes[0][1])
        now = (current or {}).get("j" + axis.lower())
        up_way, down_way = _AXIS_WAYS.get(axis, ("further", "further"))
        if now is None or abs(target - now) < 1e-9:
            way, other = "further", "the other way"
        elif target > now:
            way, other = up_way, down_way
        else:
            way, other = down_way, up_way
        end = ("top of its travel" if axis == "Z" and way == "higher"
               else "end of its travel")
        if job_running:
            # Mid-job it is not "nothing wrong": the program asked for a move
            # the zero it was started from cannot reach, and the job stopped.
            fix = ("set the Z zero lower so the job's highest move fits"
                   if axis == "Z" and way == "higher" else
                   f"move the {axis} zero so the whole job fits")
            return "Job Outside Travel", (
                f"The job needed {axis} at {target:g}, but {axis} cannot go "
                f"{way} from the current zero — it is at the {end}. The move "
                f"was not run.\n\n"
                f"Before running it again, {fix}. The check before Start "
                f"now tells you this in advance.")
        return "End of Travel", (
            f"{axis} cannot go {way}: it is already at the {end}, so the move "
            f"to {axis} {target:g} was not run.\n\n"
            f"Nothing is wrong — the machine stops itself here. "
            f"Move {axis} {other} instead.")

    return title, (
        "The move was not run: it would have gone past the machine's travel "
        f"limits (soft limits) at {where} (work coordinates). The machine "
        "stopped before that move.\n\nWhen framing, this means the job does "
        "not fit where the work zero is set — move the work zero, or the "
        "part, so the whole job lies inside the machine's travel.")


def _is_unsolicited_grbl_push(line: str) -> bool:
    """True for raw GRBL/grblHAL lines the controller emits on its own — not as a
    reply to a request the app sent: alarms (``ALARM:1``), status reports
    (``<Idle|...>``), bracketed feedback (``[MSG:...]``, ``[GC:...]``) and the
    startup/reset banner (``GrblHAL ...``). These must NOT be paired with the
    oldest pending user request in the console, otherwise an async event looks
    like the reply to whatever the user last clicked (e.g. an ALARM appearing to
    answer a feed-override click). ``ok`` / ``error:N`` are genuine replies to a
    sent G-code line and are intentionally excluded here so they still pair."""
    if not line:
        return False
    up = line.upper()
    return (up.startswith("ALARM:")
            or (line.startswith("<") and line.endswith(">"))    # status report
            or (line.startswith("[") and line.endswith("]"))    # [MSG:..]/[GC:..]
            or up.startswith("GRBL"))                            # reset banner


#: A stock GRBL setting line, as printed by `$$` (``$100=800.000``).
_GRBL_SETTING_RE = re.compile(r"^\$(\d+)=(-?\d+(?:\.\d*)?)$")


# The firmware sends at most 8192 bytes per reply and cuts off whatever is
# left, so once the SD card holds about 47 files the `fileList` reply arrives
# truncated mid-entry and is not valid JSON. Dropping it left the file
# manager empty -- all files "gone", new uploads never listed. Instead the
# complete entries are kept, plus the name (and any whole fields) of the entry
# that was cut, and the list is flagged as truncated so the page can say so.
_FILE_LIST_PREFIX_RE = re.compile(
    r'^\s*\{\s*"result"\s*:\s*"success"\s*,\s*"(?:gcodeF|f)ileList"\s*:\s*\[')
_PARTIAL_FIELD_RE = re.compile(
    r'"(\w+)"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?)(?=\s*[,}])')


def _salvage_file_list(line: str) -> dict | None:
    """Recover a truncated ``fileList`` reply, or None if *line* is not one."""
    m = _FILE_LIST_PREFIX_RE.match(line)
    if not m:
        return None
    dec = json.JSONDecoder()
    files: list = []
    pos = m.end()
    while True:
        while pos < len(line) and line[pos] in " \t,":
            pos += 1
        if pos >= len(line) or line[pos] != "{":
            break
        try:
            entry, end = dec.raw_decode(line, pos)
        except json.JSONDecodeError:
            # The entry the firmware cut off: keep the fields that arrived whole.
            entry = {}
            for k, v in _PARTIAL_FIELD_RE.findall(line[pos:]):
                try:
                    entry[k] = json.loads(v)
                except json.JSONDecodeError:
                    pass
            if isinstance(entry.get("fileName"), str) and entry["fileName"]:
                files.append(entry)
            break
        if isinstance(entry, dict):
            files.append(entry)
        pos = end
    return {"result": "success", "fileList": files, "_truncated": True}


# Local cache of G-code files (uploaded or downloaded) used by Live Preview.
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".piccola-cnc", "gcode-cache")


#: Extensions treated as machine-bound G-code text for line-ending repair.
#: Mirrors ui/pages/cam.py's _GCODE_EXT; anything else (a Kiri .zip export, an
#: SVG preview, a .ply cloud) is left byte-for-byte alone.
_TEXT_GCODE_EXT = {".nc", ".gcode", ".ngc", ".tap", ".cnc", ".txt"}


def normalize_crlf(path: str) -> bool:
    """Rewrite *path* with CRLF line endings. True if it changed.

    Controllers are not all equal about this: some senders and controllers
    refuse a program whose lines end in a bare LF and want CRLF, which is what
    the G-code convention has always been. The tools disagree with each other —
    Kiri:Moto already joins its output with `\r\n` (measured: its exports are
    100% CRLF), EasyTrace writes bare LF (measured: 889 LF, 0 CRLF in a PCB
    isolation export) — so this normalises whatever leaves the app rather than
    trusting the tool that produced it.

    Only files whose extension is in _TEXT_GCODE_EXT and whose bytes decode as
    UTF-8 are touched, so a binary download can never be corrupted by it. The
    file is rewritten only when it actually differs, which keeps it a no-op
    (and leaves the mtime alone) for a tool that was already correct.

    The two-step replace normalises mixed input as well, and is idempotent:
    CRLF is collapsed to LF first, so an already-converted line cannot become
    CR CR LF on a second pass.
    """
    if os.path.splitext(path)[1].lower() not in _TEXT_GCODE_EXT:
        return False
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False                      # not text — leave it alone
    fixed = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if fixed == data:
        return False
    try:
        with open(path, "wb") as fh:
            fh.write(fixed)
    except OSError:
        return False
    return True


def _local_ip() -> str:
    """Best-effort probe to find this host's LAN IPv4 address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""

# ── Keys served silently (not forwarded to the raw log) ──────────────────────
_SILENT_KEYS = frozenset({
    "machineStatus",
    "machinePositionX", "machinePositionY", "machinePositionZ",
    "jobPositionX",     "jobPositionY",     "jobPositionZ",
    "currentFeedRate",  "currentSpindleRPM",
    "feedRateOverride", "spindleOverride",
    "gcodeFileList", "fileList",
    "openedFileName", "openedGcodeFileName",
    "cpuaPassthroughStatus",
    "esp32PassthroughStatus",
    "currentFileLine", "totalFileLines", "jobProgress",
    "estimatedRemainingSeconds",
    "toolLength", "toolLengthReferenceZ",
    "rotaryAxisConnected", "dryRunEnabled", "procedureBusy",
    "machineName", "firmwareVersion", "deviceId",
    "powerOnHours", "runHours", "jobCount",
    "openedGcodeFileTotalLines", "fcl", "eof", "probeHeight",
    "clientWifiSSID", "clientWifiPassword",
    "apWifiSSID", "apWifiPassword", "hotspotEnabled",
    "housingInstalled", "allowMotionWhenDoorOpen", "doorOpen", "jobHistory",
    "toolLengthReferenceZ",
})

# ── Which reply answers which request ────────────────────────────────────────
#
# _SILENT_KEYS keeps the 500 ms poll out of the raw log. It did so by KEY, which
# also hid the answer to the same getter typed by hand in the console: ask for
# a settings getter and the reply arrives, updates the machine info,
# and is dropped without a line — indistinguishable from the machine never
# answering at all.
#
# A user-typed request is already tracked (see USER_REQ_REPLY_WINDOW_S), so the
# reply to one can be shown. What stops that being enough on its own is the
# poll: it sends half a dozen gets per cycle, and any of their replies would
# otherwise arrive while the typed request is pending and claim it — the typed
# request's own answer would then find nothing pending and be dropped again,
# some of the time, which is the worst of both.
#
# So a reply has to be recognised before it may claim a request: these are the
# keys that identify each answer. Only getters whose replies carry silent keys
# need an entry — everything else is logged anyway.
_REPLY_KEYS = {
    # polled constantly (see _poll) — these are the ones that must never steal
    "status": {"machineStatus"},
    "positions": {"machinePositionX", "machinePositionY", "machinePositionZ",
                  "jobPositionX", "jobPositionY", "jobPositionZ"},
    "currentFeedRate": {"currentFeedRate"},
    "currentSpindleRPM": {"currentSpindleRPM"},
    "getFeedRateOverride": {"feedRateOverride"},
    "getSpindleOverride": {"spindleOverride"},
    "getToolLength": {"toolLength", "toolLengthReferenceZ"},
    "jobProgress": {"jobProgress", "currentFileLine", "totalFileLines",
                    "estimatedRemainingSeconds"},
    "fileList": {"fileList", "gcodeFileList"},
    "gcodeFileList": {"fileList", "gcodeFileList"},
    "openedGcodeFileName": {"openedFileName", "openedGcodeFileName"},
    "getHousingSettings": {"housingInstalled", "allowMotionWhenDoorOpen"},
    "machineName": {"machineName"},
    "deviceId": {"deviceId"},
    "firmwareVersion": {"firmwareVersion"},
    "getWifiSettings": {"clientWifiSSID", "apWifiSSID", "hotspotEnabled"},
    "getProbeHeight": {"probeHeight"},
    "getJobHistory": {"jobHistory"},
    "getOpenedFileBounds": {"fileMinXmm", "fileMaxXmm", "fileMinYmm",
                            "fileMaxYmm"},
    "getDebuggingSettings": {"debuggingBase"},
}

#: The getters the app polls in the background. Their replies are the only ones
#: that turn up unasked-for, so they are also the only ones that must be
#: RECOGNISED when the pending request is a getter this table does not list —
#: an unknown request is shown (better a mislabelled line than a missing one),
#: unless what arrived is visibly one of these.
_POLLED_GETS = ("status", "positions", "currentFeedRate", "currentSpindleRPM",
                "getFeedRateOverride", "getSpindleOverride", "getToolLength",
                "jobProgress", "fileList", "openedGcodeFileName")
_POLLED_REPLY_KEYS = tuple(_REPLY_KEYS[g] for g in _POLLED_GETS)


def _reply_answers(req_text: str, payload: dict) -> bool:
    """Could *payload* be the answer to the request *req_text*?

    Fails OPEN: a request this table does not know about gets its reply shown,
    because a console that hides answers is the bug being fixed here. The one
    exception is a reply that is recognisably a background poll's — that one is
    never attributed to a request it cannot belong to.
    """
    try:
        req = json.loads(req_text)
    except (TypeError, ValueError):
        return True                       # raw G-code etc. — not ours to judge
    if not isinstance(req, dict):
        return True
    rtype = (req.get("getType") or req.get("setType")
             or req.get("commandType"))
    keys = _REPLY_KEYS.get(rtype)
    if keys is not None:
        return bool(keys & payload.keys())
    return not any(k & payload.keys() for k in _POLLED_REPLY_KEYS)


# Boolean machine-state flags surfaced via machine_flags_changed
_FLAG_KEYS = (
    "rotaryAxisConnected", "dryRunEnabled", "dryRunActive", "procedureBusy",
    "jobRunning", "jobPaused", "jobResuming", "housingInstalled",
    # Door interlock: with the housing installed and the spindle off, whether an
    # open door lets motion carry on. Arrives with housingInstalled from
    # get/setHousingSettings.
    "allowMotionWhenDoorOpen",
    # Live door/lid sensor, carried on every `status` poll. The raw pin state:
    # true whenever the door is open, whether or not a housing is installed —
    # the firmware only *acts* on it when one is, so callers pair it with
    # housingInstalled themselves.
    "doorOpen",
    "homedSincePowerOn",
)

# Firmware debug-print switches (getDebuggingSettings; each one is set with
# its own setDebugging<Name> and saved in the machine's EEPROM).
DEBUGGING_KEYS = ("debuggingBase", "debuggingSpindle", "debuggingStatus",
                  "debuggingMacroBuffer", "debuggingSDCard", "debuggingJson",
                  "debuggingESP32", "debuggingEEPROM", "debuggingJob")

# WiFi / hotspot settings keys (parsed from getWifiSettings responses)
_WIFI_KEYS = ("clientWifiSSID", "clientWifiPassword",
              "apWifiSSID", "apWifiPassword", "hotspotEnabled")

# ── Machine geometry ─────────────────────────────────────────────────────────
#: Usable bed, mm (firmware Config.h MAX_WORK_DIMENSION_XY_MM, grbl $130/$131).
WORK_AREA_MM = (150.0, 150.0)

# Procedure-completion message types (msg["type"]) forwarded as signals
_COMPLETION_TYPES = frozenset({
    "homingCompleted", "resumeCompleted",
    "probeZ0Completed", "probeToolLengthCompleted",
})

# Commands that end a job early. A job that stops after one of these was
# stopped, not finished, so it gets no "job done" notice.
_JOB_STOP_COMMANDS = frozenset({"stopJobReset", "closeGcodeFile"})

#: How long jobRunning has to stay false (and jobPaused with it) before a job
#: counts as over. A pause can surface as jobRunning=false a poll before
#: jobPaused=true arrives; four 500 ms polls is plenty to see it.
JOB_END_SETTLE_MS = 2000

#: The highest progress a job has to have reported for its end to count as
#: finished. A job that stopped by itself at 40% (an SD read error, say) is not
#: done. Ignored when the firmware reported no progress at all.
JOB_DONE_MIN_PERCENT = 90

# Homing commands that put the machine into a (blocking) homing move. During
# it the firmware's JSON status replies are queued, so the real "Home" state
# doesn't arrive until it finishes — the UI shows HOMING locally instead
# (mobile parity: App.tsx homingInProgress / effectiveMachineStatus).
_HOMING_COMMANDS = frozenset({
    "grblHoming", "grblHomingX", "grblHomingY", "grblHomingZ",
})
# Safety cap so a missing homingCompleted can't leave the badge stuck on HOMING.
HOMING_MAX_MS = 180_000

_WS_POLL_CYCLE = (
    {"type": "get", "getType": "status"},
    {"type": "get", "getType": "positions"},
    {"type": "get", "getType": "currentFeedRate"},
    {"type": "get", "getType": "currentSpindleRPM"},
)

# Canonical machine-status labels (Android parity): UI logic compares lowercase
# tokens like 'idle'/'run'/'hold' after trim+lower. We normalize incoming
# firmware values once here so every desktop panel receives consistent labels.
_CANONICAL_MACHINE_STATUS = {
    "idle": "Idle",
    "run": "Run",
    "jog": "Jog",
    "hold": "Hold",
    "home": "Home",
    "alarm": "Alarm",
    "door": "Door",
    "check": "Check",
    "sleep": "Sleep",
}


def _normalize_machine_status(raw: object) -> str:
    s = str(raw).strip()
    if not s:
        return ""
    # Firmware/GRBL variants sometimes include details after a colon
    # (for example 'Hold:0'); Android logic uses the primary state token.
    base = s.split(":", 1)[0].strip().lower()
    return _CANONICAL_MACHINE_STATUS.get(base, s)


class Connection(QObject):
    """
    Signals
    -------
    machine_status_changed(str)       'Idle' | 'Run' | 'Hold' | 'Alarm' | …
    position_changed(dict)            keys: mx, my, mz, ma, jx, jy, jz, ja
    file_list_changed(list)           list of {'fileName', 'fileSize', 'fileDate'}
    opened_file_changed(str | None)
    feed_rate_changed(int)            mm/min
    spindle_rpm_changed(int)          RPM
    feed_override_changed(int)        %
    spindle_override_changed(int)     %
    serial_state_changed(bool, str)   (connected, message)
    ws_client_count_changed(int)
    log_received(str, str)            (text, css-color)
    transfer_started()
    transfer_progress(int)            lines sent so far
    transfer_finalizing()             last chunk sent, waiting for the machine
    transfer_done()
    """

    machine_status_changed   = Signal(str)
    position_changed         = Signal(dict)
    file_list_changed        = Signal(list)
    opened_file_changed      = Signal(object)   # str | None
    feed_rate_changed        = Signal(int)
    spindle_rpm_changed      = Signal(int)
    feed_override_changed    = Signal(int)
    spindle_override_changed = Signal(int)
    serial_state_changed     = Signal(bool, str)
    ws_client_count_changed  = Signal(int)
    log_received             = Signal(str, str)
    transfer_started         = Signal()            # upload thread launched
    transfer_finalizing      = Signal()            # all sent, machine processing
    transfer_progress        = Signal(int)
    transfer_done            = Signal()
    # Extended telemetry (mobile-app parity)
    job_progress_changed     = Signal(dict)        # {percent, currentLine, totalLines, etaSeconds}
    machine_flags_changed    = Signal(dict)        # changed boolean flags (see _FLAG_KEYS)
    tool_length_changed      = Signal(float)       # mm
    machine_info_changed     = Signal(dict)        # machineName / firmwareVersion / …
    grbl_setting_received    = Signal(int, float)  # ($N, value) from a `$$` listing
    procedure_completed      = Signal(str, str, dict)  # (type, result, full payload)
    download_progress        = Signal(str, int)    # (fileName, percent)
    download_done            = Signal(str, str)    # (fileName, local path | "" on failure)
    debugging_settings_received = Signal(dict)   # debugging* → bool, every getDebuggingSettings reply
    wifi_settings_changed    = Signal(dict)        # clientWifiSSID / apWifiSSID / hotspotEnabled / …
    job_history_received     = Signal(dict)        # raw getJobHistory payload
    #: raw getOpenedFileBounds payload — where the toolpath sits in job
    #: coordinates, which `fileList` cannot say (it carries sizes only).
    opened_file_bounds_received = Signal(dict)
    file_hash_received       = Signal(str, str)    # (fileName, sha256 lowercase hex)
    motion_error             = Signal(str, str)    # (title, message) — user-facing notice
    transfer_error           = Signal(str, str)    # (title, message) — upload was rejected
    # Multi-machine WiFi discovery / selection (mobile-app parity)
    machine_chooser_requested = Signal(list)   # 2+ machines: show chooser (list of dicts)
    machine_list_changed      = Signal(list)   # chooser contents changed live
    machine_selected          = Signal(dict)   # a machine became the active one
    machine_chooser_dismissed = Signal()       # chooser should close (no selection)
    ws_connected_changed      = Signal(bool)   # a WiFi machine is selected / released
    # A job ran to its end: (fileName, run seconds). Not emitted for a job the
    # app stopped, an alarm, or a dropped connection — see _watch_job.
    job_finished              = Signal(str, float)

    # ── Colors (mirrored from the test-app palette) ───────────────────────
    C_TX   = "#4ec9b0"
    C_RX   = "#ce9178"
    C_INFO = "#888888"
    C_WARN = "#dcdcaa"
    C_ERR  = "#f44747"
    C_WS_TX = "#c586c0"
    C_WS_RX = "#9cdcfe"

    def __init__(self, parent=None):
        super().__init__(parent)

        self._serial = SerialWorker(SerialConfig())
        self._serial.line_received.connect(self._on_serial_line)
        self._serial.connection_changed.connect(self._on_serial_state)
        self._serial.probe_status.connect(self._on_probe_status)

        self._ws = WsServer()
        self._ws.log_signal.connect(self._on_ws_log)
        self._ws.state_changed.connect(self._on_ws_state)
        self._ws.client_count_changed.connect(self._on_ws_clients)
        self._ws.client_connected.connect(self._on_ws_client_connected)
        self._ws.client_disconnected.connect(self._on_ws_client_disconnected)
        self._ws.message_received.connect(self._on_ws_line)

        # BLE advertiser — the WiFi-discovery side-channel (machine scans for it).
        self._ble = BleAdvertiser(self)
        self._ble.status.connect(lambda m: self.log_received.emit(m, self.C_INFO))

        self._serial_connected: bool = False
        self._ws_client_count: int = 0

        # ── Multi-machine WiFi discovery / selection (mobile-app parity) ──────
        # clientId → {clientId, name, deviceId, firmware}
        self._machines: dict[str, dict] = {}
        self._selected_client: str | None = None
        self._selected_device_id: str | None = None
        # "idle" | "searching" | "choosing" | "connected"
        self._selection_phase: str = "idle"
        self._discovery_timer: QTimer | None = None
        # Stable deviceIds already seen this discovery round. The window is only
        # *extended* when a genuinely-new deviceId appears — reconnections of an
        # already-seen machine must NOT keep pushing the deadline out, or the 5 s
        # window never elapses under reconnect churn (mobile-app parity). Cleared
        # at the start of every round (server stop, serial takeover, rescan, or
        # when every machine has dropped).
        self._seen_device_ids: set[str] = set()
        # When True, the next discovery resolve shows the chooser even for a
        # single machine (set by a manual "Switch machine" rescan, so the user
        # always gets to pick; cleared once the chooser opens / a machine is
        # selected). Automatic startup discovery leaves this False and a lone
        # machine auto-connects (mobile-app parity).
        self._force_chooser: bool = False
        # Last discovery field logged, for the offline-hold log suppression in
        # _resolve_discovery.
        self._last_discovery_ids: str = ""

        # Developer offline mode (no hardware): when True the real transports
        # are torn down and a canned idle-machine status drives the UI. send()
        # and the poll are gated on a real transport, so nothing transmits.
        self._offline: bool = False

        # WiFi fallback is gated behind a serial head-start (see start() /
        # SERIAL_HEADSTART_MS). False until either the head-start elapses with no
        # cable or serial drops; while False the BLE beacon stays off and incoming
        # WS machines are refused, so a cabled machine connects directly without
        # ever entering WiFi discovery / the multi-machine chooser.
        self._wifi_enabled: bool = False

        # Local "homing in progress" flag: while set, the status is reported as
        # "home" regardless of what the (queued) firmware replies say.
        self._homing: bool = False

        # Snapshot for duplicate suppression
        self._last_status: str | None = None
        self._last_pos: dict = {
            "mx": 0.0, "my": 0.0, "mz": 0.0, "ma": 0.0,
            "jx": 0.0, "jy": 0.0, "jz": 0.0, "ja": 0.0,
        }
        # A is set to 0 once per app launch, at the first Idle report
        # (_zero_a_on_launch)
        self._launch_a_zeroed: bool = False
        self._file_list_snapshot: tuple = ()
        # Last fileList reply was cut off by the firmware (see _salvage_file_list)
        self._file_list_truncated: bool = False
        self._opened_file: str | None = None
        # Last reported spindle speed, and when it last dropped to 0 (for the
        # spin-down wait before a job start — see spindle_coast_left).
        self._spindle_rpm = 0
        self._spindle_stopped_at: float | None = None
        self._file_meta: dict[str, dict] = {}     # fileName → full metadata dict
        self._flags: dict = {}                    # latest boolean machine flags
        self._machine_info: dict = {}             # machineName / firmwareVersion / …
        self._grbl_settings: dict[int, float] = {} # $N → value, from `$$`
        self._wifi: dict = {}                     # WiFi / hotspot settings
        self._tool_length: float | None = None
        self._job_progress: dict = {"percent": 0, "currentLine": 0,
                                    "totalLines": 0, "etaSeconds": 0}
        # Last file the overrides were reset for (Android parity: overrides
        # return to 100% on connect and whenever a new file is opened).
        self._last_override_reset_file: str | None = None
        # fileName → local sha256 awaiting the firmware's gcodeFileHash reply
        # (post-download integrity check; one download at a time).
        self._pending_hash_check: dict[str, str] = {}
        # Filenames whose gcodeFileHash request is in flight (FIFO). Lets a
        # bare fileHashNotFound miss be attributed to its request so callers
        # get file_hash_received(name, "") instead of a silent timeout.
        self._hash_req_names: deque = deque()
        # Last time each motion-error popup was shown (monotonic), for throttle.
        self._motion_error_at: dict[str, float] = {}
        # (code, monotonic time) of the last raw ALARM:N line, or None
        self._last_alarm: tuple[int, float] | None = None

        # User-initiated requests (send()/send_raw()) awaiting a reply, as
        # (monotonic timestamp, payload) pairs. See USER_REQ_REPLY_WINDOW_S.
        self._pending_user_reqs: deque = deque()

        # Transfer state
        self._transfer_active = False
        self._transfer_event = threading.Event()
        self._abort_transfer = False
        self._last_transfer_ok = False
        self._transfer_error: str | None = None

        # Download state (machine → desktop, fcl chunks). The file is
        # reconstructed byte-exact ("|" → "\n" per chunk, mobile parity) so its
        # sha256 can be checked against the firmware's gcodeFileHash and so
        # cached previews line up 1:1 with the firmware's line numbering.
        self._download_name: str | None = None
        self._download_text: list[str] = []      # decoded chunk pieces
        self._download_line_count = 0
        self._download_total = 0
        self._download_size = 0                  # bytes, from the start reply
        self._download_last_line = 0             # currentFileLine of the last chunk kept
        self._download_started = False           # start reply received
        self._download_outstanding = 0           # chunk requests awaiting a reply
        self._download_retries = 0               # re-requests of the current chunk
        self._download_timer: QTimer | None = None

        os.makedirs(CACHE_DIR, exist_ok=True)

        # Polling timer (500 ms — mobile parity for serial; reduces from 20→10 cmd/sec)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_tick = 0
        self._ws_poll_tick = 0

        # Safety timeout that clears the homing flag if no homingCompleted ever
        # arrives, so the badge can't get stuck on HOMING.
        self._homing_timeout = QTimer(self)
        self._homing_timeout.setSingleShot(True)
        self._homing_timeout.setInterval(HOMING_MAX_MS)
        self._homing_timeout.timeout.connect(self._end_homing)

        # Discovery self-heal (1.5 s, mobile parity): re-arms the discovery
        # window if WiFi machines are attached but no window is pending, so a
        # missed event / race can never leave the splash stuck forever. Started
        # in start() so headless tests that drive the handlers directly stay
        # deterministic.
        self._heal_timer = QTimer(self)
        self._heal_timer.setInterval(1500)
        self._heal_timer.timeout.connect(self._discovery_self_heal)

        # Serial head-start (see SERIAL_HEADSTART_MS): one-shot; on expiry it
        # turns on the WiFi fallback unless serial has already connected.
        self._serial_headstart_timer = QTimer(self)
        self._serial_headstart_timer.setSingleShot(True)
        self._serial_headstart_timer.setInterval(SERIAL_HEADSTART_MS)
        self._serial_headstart_timer.timeout.connect(self._on_serial_headstart_elapsed)

        # "Job done" detection. The firmware sends no completion message for a
        # job, so the end is read off the jobRunning flag: true -> false, with
        # no pause, stop, alarm or disconnect in between. See _watch_job.
        self._job_watch: dict | None = None
        self._job_end_timer = QTimer(self)
        self._job_end_timer.setSingleShot(True)
        self._job_end_timer.setInterval(JOB_END_SETTLE_MS)
        self._job_end_timer.timeout.connect(self._confirm_job_end)

        # WiFi keepalive (see WS_KEEPALIVE_INTERVAL_MS): pings every attached-but-
        # unselected machine so the firmware watchdog can't force-close it mid-
        # discovery / while the chooser is open. Started in start(), stopped in
        # stop(); the tick itself no-ops once a machine is selected.
        self._ws_keepalive_timer = QTimer(self)
        self._ws_keepalive_timer.setInterval(WS_KEEPALIVE_INTERVAL_MS)
        self._ws_keepalive_timer.timeout.connect(self._ws_keepalive_tick)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_serial_connected(self) -> bool:
        return self._serial.is_connected

    @property
    def is_ws_active(self) -> bool:
        """True when a WiFi machine has been selected and is the active link,
        or while the developer offline mode is simulating a connection."""
        return self._offline or self._selected_client is not None

    @property
    def is_offline(self) -> bool:
        """True while the developer offline (no-hardware) mode is active."""
        return self._offline

    @property
    def file_list_truncated(self) -> bool:
        """True when the machine's last file list was cut off at the firmware's
        8 KB reply limit, so files after the last one listed are missing."""
        return self._file_list_truncated

    @property
    def discovered_machine_count(self) -> int:
        """How many distinct machines (deduped by deviceId) have announced
        themselves and are waiting to be picked. Lets the app menu say what is
        available while offline mode is holding a multi-machine field open."""
        return len(self._build_choices())

    @property
    def last_transfer_ok(self) -> bool:
        """Whether the most recent transfer finished, rather than being
        aborted or failing. ``transfer_done`` fires for all three."""
        return self._last_transfer_ok

    @property
    def last_transfer_error(self) -> str | None:
        """The firmware error that killed the most recent upload, or None if it
        completed or the user aborted it. Cleared when the next one starts."""
        return self._transfer_error

    @property
    def is_transfer_active(self) -> bool:
        """True while a file upload (desktop → machine) is in progress."""
        return self._transfer_active

    @property
    def is_downloading(self) -> bool:
        """True while a file download (machine → desktop) is in progress —
        including the ones the live preview starts to sync its cache."""
        return self._download_name is not None

    def select_machine(self, client_id: str) -> None:
        """Choose *client_id* as the active WiFi machine (called by the chooser)."""
        self._select_machine(client_id)

    def rescan_machines(self, force_chooser: bool = True) -> None:
        """Manually re-run WiFi machine discovery (mobile 'Switch machine').

        Releases the active selection (without dropping any socket, so attached
        machines stay available), asks every connected machine to re-announce
        its identity, and reopens the discovery window. With *force_chooser* the
        picker is shown even for a single machine, so the user can always choose
        which machine to connect to. No-op while serial is connected — serial is
        the preferred transport and always wins."""
        if self._serial.is_connected:
            return
        self._force_chooser = force_chooser
        # Forget the active selection so the picker (not a silent reconnect)
        # wins, but keep the sockets open so machines stay available.
        had_selection = self._selected_client is not None
        self._selected_client = None
        self._selected_device_id = None
        self._selection_phase = "searching"
        self._seen_device_ids.clear()   # fresh round
        self._sync_poll_timer()
        if had_selection:
            self.ws_connected_changed.emit(False)
        # Re-scanning → resume advertising so far-off machines can rediscover us
        # (it was stopped when a machine was selected).
        self._enable_wifi_fallback()
        # Nudge every attached machine to re-identify so the chooser repopulates.
        for cid in list(self._machines.keys()):
            for gt in ("machineName", "deviceId", "firmwareVersion"):
                self._ws.send_to_client(cid, json.dumps(
                    {"type": "get", "getType": gt}, separators=(",", ":")))
        self.log_received.emit("[WS] Re-scanning for machines…", self.C_INFO)
        self._arm_discovery()

    def current_job_position(self) -> dict:
        """Latest job (work-coordinate) position: keys jx, jy, jz."""
        return {k: self._last_pos.get(k, 0.0) for k in ("jx", "jy", "jz")}

    def current_position(self) -> dict:
        """Full latest position snapshot: job work coordinates (jx/jy/jz/ja) and
        machine absolute coordinates (mx/my/mz/ma)."""
        keys = ("jx", "jy", "jz", "ja", "mx", "my", "mz", "ma")
        return {k: self._last_pos.get(k, 0.0) for k in keys}

    def flags(self) -> dict:
        """Latest boolean machine flags (dryRunEnabled, procedureBusy, …)."""
        return dict(self._flags)

    def machine_status(self) -> str:
        """Latest normalized machine status ('Idle', 'Run', …), "" if unknown."""
        return self._last_status or ""

    def grbl_settings(self) -> dict:
        """GRBL `$N` settings last read with `$$` ({100: 800.0, …})."""
        return dict(self._grbl_settings)

    def request_grbl_settings(self) -> None:
        """Ask the controller for its `$$` listing. Each `$N=value` line of the
        reply is stored and emitted as grbl_setting_received."""
        self.send_raw("$$")

    def door_blocked(self) -> bool:
        """True when the door/lid is open, so anything that spins the tool or
        moves the machine must not be offered.

        Either signal is enough: `doorOpen` — the live sensor carried on every
        `status` poll — or the machine state itself being `Door`.

        This does NOT also require `housingInstalled`. The firmware only acts
        on the door when a housing is registered, but that is a stored setting,
        and a machine reporting its lid open should not be offered a start or a
        spin-up because a settings flag says otherwise. The consequence to know
        about: the sensor pin is INPUT_PULLUP with HIGH = open, so a machine
        with no door switch wired reads permanently open. The right panel's
        Door row shows exactly what is being read.

        Deliberately not relaxed by allowMotionWhenDoorOpen either: that only
        covers motion with the spindle off, and a spinning spindle with the
        door open always pauses whatever it says."""
        return (bool(self._flags.get("doorOpen", False))
                or _normalize_machine_status(self._last_status or "") == "Door")

    def machine_info(self) -> dict:
        """Latest machine identity/info fields."""
        return dict(self._machine_info)

    def wifi_settings(self) -> dict:
        """Latest WiFi/hotspot settings (clientWifiSSID, apWifiSSID, …)."""
        return dict(self._wifi)

    def tool_length(self) -> float | None:
        return self._tool_length

    def serial_port(self) -> str | None:
        """Device path of the USB serial link, or None."""
        return self._serial.port

    def release_serial(self) -> str | None:
        """Close the serial link and STOP hunting for it; returns the port.

        For the one operation that has to hold the port itself — the 1200 baud
        reboot touch. Closing alone is not enough: the discovery loop would
        reopen the port from its own thread a moment later and probe it, which
        at best races the touch and at worst is the thing holding the port when
        the touch tries to open it. `resume_serial()` puts it back.
        """
        port = self._serial.port
        self._serial.disconnect()
        return port

    def resume_serial(self) -> None:
        """Start looking for the machine again after `release_serial()`."""
        self._serial.start_auto_connect()

    def work_area_mm(self) -> tuple[float, float]:
        """Usable bed (width, depth) in mm."""
        return WORK_AREA_MM

    def file_meta(self, name: str) -> dict:
        """Full metadata dict for *name* from the last file list (or {})."""
        return dict(self._file_meta.get(name, {}))

    def opened_file_name(self) -> str | None:
        return self._opened_file

    def job_progress(self) -> dict:
        return dict(self._job_progress)

    @staticmethod
    def cached_file_path(name: str) -> str | None:
        """Local cache path for *name* if we have a copy, else None."""
        p = os.path.join(CACHE_DIR, name)
        return p if os.path.isfile(p) else None

    @staticmethod
    def forget_cached_file(name: str) -> None:
        """Drop the local copy of *name* (and its .fwhash sidecar). Called when
        the file is deleted from the machine: a new file saved later under the
        same name — a fresh scan as wave.ply — must not be served from the old
        copy, and nothing else checks a cached .ply for freshness."""
        for p in (os.path.join(CACHE_DIR, name),
                  os.path.join(CACHE_DIR, name + ".fwhash")):
            try:
                os.remove(p)
            except OSError:
                pass

    @staticmethod
    def cache_contents() -> list:
        """All cached files as (name, size_bytes), sorted by name."""
        try:
            return sorted(
                (n, os.path.getsize(os.path.join(CACHE_DIR, n)))
                for n in os.listdir(CACHE_DIR)
                if os.path.isfile(os.path.join(CACHE_DIR, n))
            )
        except OSError:
            return []

    def download_file(self, name: str) -> None:
        """Fetch *name* from the machine into the local cache.

        Pull-based protocol (mirrors the test app):
        startFileDownload → ack {fileName, fileSize, totalFileLines} → loop of
        get nextFileDownloadChunk → {fcl, currentFileLine, downloadedBytes, eof}
        until eof. Emits download_progress / download_done.

        A chunk reply can go missing — a request dropped while the serial link
        pauses, or a WebSocket send the firmware could not complete (it then
        sends nothing and rewinds). The firmware expects the client to ask
        again, so a late chunk is re-requested (_download_timeout) rather than
        failing the whole download."""
        if self._download_name is not None:
            self.log_received.emit("[Download] Another download is active", self.C_WARN)
            return
        self._download_cleanup()
        self._download_name = name
        self.cmd("startFileDownload", filename=name)
        self._download_timer = QTimer(self)
        self._download_timer.setSingleShot(True)
        self._download_timer.timeout.connect(self._download_timeout)
        self._download_timer.start(self._download_start_wait_ms(name))

    #: Waiting for a chunk reply before asking for it again, and how often.
    #: The firmware gives up on a chunk after 5 failed sends and closes the
    #: session 25 s after the last request, so this stays inside both.
    DOWNLOAD_CHUNK_WAIT_MS = 5000
    DOWNLOAD_CHUNK_RETRIES = 4

    def _download_start_wait_ms(self, name: str) -> int:
        """How long to wait for the startFileDownload reply. Current firmware
        answers at once; older firmware counts the file's lines first, which
        blocks it for ~2 s per MB (a 9.4 MB scan took ~20 s), so scale with
        the size the file list reported."""
        try:
            size = int(self._file_meta.get(name, {}).get("fileSize", 0))
        except (TypeError, ValueError):
            size = 0
        return 8000 + 3000 * size // 1_000_000

    def abort_download(self) -> None:
        if self._download_name is not None:
            self.cmd("abortFileDownload")
            self._download_failed(aborted=True)

    def start(self) -> None:
        """Start serial auto-connect and the WS server.

        Serial is the preferred transport, so the WiFi fallback (BLE beacon + WS
        discovery) is held back for a short head-start: a cabled machine answers
        the 2 Mbaud serialPing and connects directly without any multi-machine
        check. WiFi only switches on if serial hasn't won by SERIAL_HEADSTART_MS
        (see _on_serial_headstart_elapsed) or once a serial link later drops."""
        self._wifi_enabled = False
        self._serial.start_auto_connect()
        self._ws.start(port=WS_PORT, mdns_name="")
        # Do NOT advertise over BLE yet — that beacon is what invites a machine to
        # dial in over WiFi. Give serial first crack; enable WiFi only if needed.
        self._serial_headstart_timer.start()
        self._heal_timer.start()
        self._ws_keepalive_timer.start()

    def stop(self) -> None:
        """Graceful shutdown."""
        self._ws_keepalive_timer.stop()
        self._serial_headstart_timer.stop()
        self._heal_timer.stop()
        self._ble.stop()
        self._ws.stop()
        self._serial.disconnect()

    # One canned "connected, idle" machine used by the developer offline mode.
    # Includes every boolean flag (so the gates open), a homed state (so the
    # homing overlay is already satisfied) and an attached distance sensor (so
    # the detect buttons are exercisable). Positions/RPM are zeroed.
    _OFFLINE_STATUS = {
        "result": "success",
        "machineStatus": "idle",
        "machineName": "Offline",
        "firmwareVersion": "offline",
        "machinePositionX": 0.0, "machinePositionY": 0.0,
        "machinePositionZ": 0.0, "machinePositionA": 0.0,
        "jobPositionX": 0.0, "jobPositionY": 0.0,
        "jobPositionZ": 0.0, "jobPositionA": 0.0,
        "currentFeedRate": 0, "currentSpindleRPM": 0,
        "feedRateOverride": 100, "spindleOverride": 100,
        # No toolLength: the card shows its placeholder until something is
        # actually measured, offline included — a canned number there reads as
        # a real measurement.
        "rotaryAxisConnected": False, "dryRunEnabled": False,
        "dryRunActive": False, "procedureBusy": False,
        "jobRunning": False, "jobPaused": False, "jobResuming": False,
        "housingInstalled": True, "doorOpen": False,
        "homedSincePowerOn": True,
    }

    def enter_offline_mode(self) -> None:
        """Developer offline mode (no hardware): simulate a connected, idle
        machine so the whole UI can be exercised.

        The real transports are deliberately left RUNNING. Offline mode used to
        call stop(), which killed serial auto-connect, the WS server and the BLE
        beacon — so a machine plugged in afterwards could never be found and the
        mode was a one-way trip. Keeping them up costs nothing (no machine is
        selected, so the poll stays off and send()/_poll/_send_silent are gated
        on a real transport — nothing is transmitted and the console stays
        quiet) and it means hardware that appears later still discovers us and
        takes over, see _leave_offline_for_real_link().

        Seeding one idle status populates every panel and satisfies the initial
        homing flow (homedSincePowerOn)."""
        if self._offline:
            return
        self._offline = True
        self._poll_timer.stop()           # nothing real to poll yet
        # Populate the UI from the canned status first (flags, position, info),
        # then announce the WiFi link as up so the splash fades and the gates
        # open with the machine state already in place.
        self._route(json.dumps(self._OFFLINE_STATUS))
        self.ws_connected_changed.emit(True)
        self.log_received.emit(
            "[Offline] Simulated machine — still searching for real hardware…",
            self.C_INFO)

    # ── Offline rotary ───────────────────────────────────────────────────────
    #
    # Rotary Mode is the machine reporting rotaryAxisConnected, which the canned
    # offline machine never did, and the Rotary setup (Controls → Rotary) waits
    # for two moves to end and for the cable to be detected -- so offline it hung
    # on its first step. The offline machine acts those out instead: each of the
    # two setup moves reports Run, then Idle a moment later, and the cable is
    # "plugged in" once the bed has moved to the installation position, where a
    # real operator connects it. set_offline_rotary() plugs or unplugs it
    # directly (app menu, offline only); unplugging is how Rotary Mode is left.
    # Nothing else is simulated: jogs and other moves still change nothing.

    _OFFLINE_MOVE_MS = 1500      # a setup move, Run -> Idle
    _OFFLINE_CABLE_MS = 1000     # after the install move, the cable "goes in"

    def _offline_command(self, obj: dict) -> None:
        """Act out the few commands the offline machine knows; drop the rest."""
        if obj.get("type") != "command":
            return
        ct = obj.get("commandType")
        if ct == "installRotary":
            self._offline_move(ct, then=lambda: QTimer.singleShot(
                self._OFFLINE_CABLE_MS, lambda: self.set_offline_rotary(True)))
        elif ct == "moveToRotaryPosition":
            self._offline_move(ct)

    def _offline_move(self, what: str, then=None) -> None:
        self.log_received.emit(f"[Offline] Simulating {what}", self.C_INFO)

        def status(s: str) -> None:
            if self._offline:
                self._route(json.dumps({"result": "success", "machineStatus": s}))

        def done() -> None:
            if not self._offline:
                return
            status("idle")
            if then is not None:
                then()

        # Run on the next turn, not inside cmd(): the caller connects to the
        # status signal right after sending.
        QTimer.singleShot(0, lambda: status("run"))
        QTimer.singleShot(self._OFFLINE_MOVE_MS, done)

    def set_offline_rotary(self, connected: bool) -> None:
        """Plug or unplug the offline machine's rotary axis cable (offline only)."""
        if not self._offline:
            return
        if bool(self._flags.get("rotaryAxisConnected")) == bool(connected):
            return
        self._route(json.dumps({"result": "success",
                                "rotaryAxisConnected": bool(connected)}))
        self.log_received.emit(
            "[Offline] Rotary axis cable "
            + ("connected" if connected else "disconnected"), self.C_INFO)

    def exit_offline_mode(self) -> None:
        """Leave the developer offline mode by hand and go back to searching.

        Drops the simulated snapshot, reports the link as down (which brings the
        search splash back) and re-opens a discovery round. The transports were
        never stopped by enter_offline_mode(), so there is nothing to restart —
        only the WiFi beacon has to be re-armed if it had been switched off."""
        if not self._offline:
            return
        self._clear_offline_snapshot()
        self.ws_connected_changed.emit(False)
        self.log_received.emit(
            "[Offline] Left offline mode — searching for a machine…",
            self.C_INFO)
        # Re-announce ourselves and re-open the discovery window. No-op while
        # serial is connected, which cannot happen here (a live serial link
        # would already have pulled us out via _leave_offline_for_real_link).
        self.rescan_machines(force_chooser=False)

    def _leave_offline_for_real_link(self) -> None:
        """Real hardware connected while offline mode was running — drop the
        simulation and let the machine drive the UI.

        No ws_connected_changed is emitted here: the caller announces the real
        link itself (_select_machine emits it, serial reports through
        serial_state_changed), so the splash never flashes back in between."""
        if not self._offline:
            return
        self._clear_offline_snapshot()
        self.log_received.emit(
            "[Offline] Real machine connected — leaving offline mode.",
            self.C_INFO)

    def _clear_offline_snapshot(self) -> None:
        """Forget the canned offline machine: clear the flag, the identity shown
        in the top bar, and the status/flags snapshot so whatever connects next
        re-emits them as changes (they only fire on a delta)."""
        self._offline = False
        self._machine_info = {}
        self.machine_info_changed.emit({})
        self._reset_machine_state()

    def _on_serial_headstart_elapsed(self) -> None:
        """Serial head-start expired with no cable connection — switch on the
        WiFi fallback so a machine can discover us over BLE/WS."""
        if self._serial.is_connected:
            return
        self._enable_wifi_fallback()

    def _enable_wifi_fallback(self) -> None:
        """Turn on the WiFi transport: advertise over BLE so the machine can find
        us and dial the WS server, and let WS clients in. No-op while serial is
        connected (serial always wins)."""
        if self._serial.is_connected:
            return
        self._wifi_enabled = True
        self._ble.start(_local_ip())

    def send(self, obj: dict) -> None:
        """Send a JSON command on the best available transport (serial preferred)."""
        msg = json.dumps(obj, separators=(",", ":"))
        if self._serial.is_connected:
            self._serial.send_priority(msg)
            self.log_received.emit(f"[TX] {msg}", self.C_TX)
        elif self._selected_client is not None:
            self._ws_send(msg)
            self.log_received.emit(f"[WS→TX] {msg}", self.C_WS_TX)
        else:
            if self._offline:
                self._offline_command(obj)
            return
        self._pending_user_reqs.append((time.monotonic(), msg))

    def cmd(self, command_type: str, **extra) -> None:
        if command_type in _JOB_STOP_COMMANDS and self._job_watch is not None:
            self._job_watch["stopped"] = True
        self.send({"type": "command", "commandType": command_type, **extra})

    def home(self, command_type: str = "grblHoming", **extra) -> None:
        """Send a homing command and flag homing in progress locally, so the UI
        immediately shows HOMING. The firmware's status replies are queued
        behind the blocking homing move, so the real 'Home' state doesn't arrive
        until it finishes — the mobile app drives this the same way (App.tsx
        onHomingStarted → homingInProgress → effectiveMachineStatus). Cleared on
        the homingCompleted message, on an alarm, on disconnect, or by timeout."""
        if command_type in _HOMING_COMMANDS:
            self._begin_homing()
        self.cmd(command_type, **extra)

    def _begin_homing(self) -> None:
        self._homing = True
        self._homing_timeout.start()
        if self._last_status != "Home":
            self._last_status = "Home"
            self.machine_status_changed.emit("Home")

    def _end_homing(self) -> None:
        if not self._homing:
            return
        self._homing = False
        self._homing_timeout.stop()

    def set(self, set_type: str, **extra) -> None:
        self.send({"type": "set", "setType": set_type, **extra})

    def _zero_a_on_launch(self, ja) -> None:
        """Start every app session with A reading 0, as the A zero button does.

        The read-out comes up far from 0 after a relaunch (whole turns from the
        last rotary job), and the operator zeroed it by hand every time. Done
        once per launch, at the first report with the machine Idle -- a
        reconnect later in the session keeps the A0 the operator is working
        with. Never while a job is running or paused: if the app comes up
        mid-job, A is left alone for the whole session. Alarm / homing / no
        status yet: wait for Idle.
        """
        status = self._last_status or ""
        if (status in ("Run", "Hold")
                or self._flags.get("jobRunning") or self._flags.get("jobPaused")):
            self._launch_a_zeroed = True
            return
        if status != "Idle":
            return
        self._launch_a_zeroed = True
        try:
            ja = float(ja)
        except (TypeError, ValueError):
            return
        if abs(ja) < 1e-6:
            return
        self.set("setJobA", jobA=0.0)
        self.log_received.emit(f"[A] A read {ja:g} at launch: set to 0", self.C_INFO)

    def spindle_rpm(self) -> int:
        """Last spindle speed the machine reported (0 = off)."""
        return self._spindle_rpm

    def spindle_coast_left(self, spindown_s: float) -> float:
        """Seconds the spindle may still be coasting: GRBL reports the speed
        it was told to run, so it reads 0 the moment M5 lands while the motor
        is still turning. 0 when it stopped at least *spindown_s* ago."""
        if self._spindle_stopped_at is None:
            return 0.0
        return max(0.0, spindown_s - (time.monotonic() - self._spindle_stopped_at))

    def get(self, get_type: str, **extra) -> None:
        """A `get` request. *extra* carries the request's own parameters —
        no getter currently uses it."""
        self.send({"type": "get", "getType": get_type, **extra})

    def jog(self, feed_rate: int, **axes) -> None:
        """Continuous-jog start / keepalive. Only the SIGN of each axis value
        matters (direction); the firmware jogs toward the soft-limit and stops
        on jog_cancel() or its own 350 ms keep-alive watchdog. Repeat this
        every ~120 ms while the button is held. Sent silently — it would flood
        the raw log otherwise (the poll suppresses the bare success acks)."""
        msg = {"type": "command", "commandType": "jog",
               "feedRate": feed_rate, **axes}
        if self._serial.is_connected:
            self._serial.send_priority(json.dumps(msg, separators=(",", ":")))
        else:
            self._send_ws_silent(msg)

    def jog_cancel(self) -> None:
        """Stop a continuous jog immediately (GRBL 0x85). Safe when idle."""
        msg = {"type": "command", "commandType": "jogCancel"}
        if self._serial.is_connected:
            self._serial.send_priority(json.dumps(msg, separators=(",", ":")))
        else:
            self._send_ws_silent(msg)

    def request_file_hash(self, filename: str) -> None:
        """Ask the firmware for a .nc file's stored SHA-256; the reply arrives
        via file_hash_received(fileName, sha256) — sha256 is "" when the
        machine has no stored hash for it (older file). Sent silently —
        callers use this as a background integrity check, not a console
        action."""
        self._hash_req_names.append(filename)
        self._send_silent_any({"type": "get", "getType": "gcodeFileHash",
                               "filename": filename})

    @staticmethod
    def cached_file_fw_hash(name: str) -> str | None:
        """The firmware-reported SHA-256 recorded when *name* was last
        downloaded into the cache (sidecar file), or None. Downloads are
        normalized by the firmware (blank/comment lines are not transferred),
        so cache freshness is tracked by comparing firmware hashes to each
        other — never to a locally computed digest."""
        try:
            with open(os.path.join(CACHE_DIR, name + ".fwhash")) as fh:
                return fh.read().strip().lower() or None
        except OSError:
            return None

    @staticmethod
    def _store_fw_hash(name: str, sha: str) -> None:
        try:
            with open(os.path.join(CACHE_DIR, name + ".fwhash"), "w") as fh:
                fh.write(sha)
        except OSError:
            pass

    def send_raw(self, text: str) -> None:
        """Send a raw (non-JSON) line — e.g. G-code passthrough like 'M3 S1000'."""
        if self._serial.is_connected:
            self._serial.send_priority(text)
            self.log_received.emit(f"[TX] {text}", self.C_TX)
        elif self._selected_client is not None:
            self._ws_send(text)
            self.log_received.emit(f"[WS→TX] {text}", self.C_WS_TX)
        else:
            return
        self._pending_user_reqs.append((time.monotonic(), text))

    def start_transfer(self, local_path: str, remote_name: str) -> None:
        """Launch a JSON file-chunk transfer in a background thread."""
        try:
            with open(local_path) as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
        except OSError as exc:
            self.log_received.emit(f"[Transfer] Cannot read file: {exc}", self.C_ERR)
            return
        # Keep a local copy so Live Preview can render without re-downloading.
        try:
            shutil.copyfile(local_path, os.path.join(CACHE_DIR, remote_name))
        except OSError:
            pass
        file_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Flag it now, not when the thread gets going, so listeners of
        # transfer_started already see is_transfer_active as True.
        self._transfer_active = True
        self.transfer_started.emit()
        threading.Thread(
            target=self._transfer_worker,
            args=(lines, remote_name, file_date),
            daemon=True,
        ).start()

    def abort_transfer(self) -> None:
        if self._transfer_active:
            self._abort_transfer = True
            self._transfer_event.set()     # don't sit out a long ack wait
            self.cmd("abortFileTransfer")
            self.log_received.emit("[Transfer] Aborted by user", self.C_WARN)

    def _fail_transfer(self, err: str, payload: dict) -> None:
        """End the running upload because the firmware rejected it, and say why.

        Three things have to happen, and only the last one is the visible part:

        * wake the worker — it is inside ``_transfer_event.wait(5.0)`` for an
          ack that will never arrive, and every remaining chunk would burn the
          same five seconds;
        * mark the transfer aborted, so it cannot end on the "Complete" branch
          and leave ``last_transfer_ok()`` true. A caller acting on that (the
          CAM page deletes its staged export) would throw away the only copy of
          a file the machine never stored;
        * tell the user which rejection it was.

        No ``abortFileTransfer`` is sent: the firmware has already dropped the
        temporary file on every error in the table, and a start that failed
        never opened one.

        First failure wins — a rejected chunk stream can answer several chunks
        with the same error before the worker stops."""
        if self._transfer_error is not None:
            return
        self._transfer_error = err
        self._abort_transfer = True
        self._transfer_event.set()
        self.log_received.emit(f"[Transfer] Rejected by machine: {err}", self.C_ERR)
        self.transfer_error.emit(*_transfer_error_notice(err, payload))

    # ── Download internals ────────────────────────────────────────────────

    def _download_chunk(self, p: dict) -> None:
        """Handle one {fcl, eof} chunk, then pull the next (test-app protocol).
        Each "|" terminates a line, so decoding is a plain "|"→"\\n" replace —
        this keeps the reconstruction byte-exact with the file on the SD card
        (mobile parity), which the sha256 integrity check below relies on."""
        self._download_outstanding = max(0, self._download_outstanding - 1)
        self._download_retries = 0
        chunk = p.get("fcl", "")
        # A chunk the firmware re-sent after a send it thought had failed
        # carries the same currentFileLine as the copy already kept: skip it.
        line = p.get("currentFileLine")
        dup = (isinstance(line, int) and self._download_last_line > 0
               and line <= self._download_last_line)
        if chunk and not dup and isinstance(line, int):
            # currentFileLine counts every line delivered so far, so this
            # chunk must start right where the last kept one ended. A gap
            # means a chunk was lost past recovery: fail rather than save a
            # file with lines missing.
            if line - chunk.count("|") != self._download_last_line:
                self.log_received.emit(
                    f"[Download] {self._download_name}: a chunk went missing "
                    f"(lines {self._download_last_line}→"
                    f"{line - chunk.count('|')}) — try again", self.C_ERR)
                self._raw_send({"type": "command",
                                "commandType": "abortFileDownload"})
                self._download_failed()
                return
        if chunk and not dup:
            self._download_text.append(chunk.replace("|", "\n"))
            self._download_line_count += chunk.count("|")
            if isinstance(line, int):
                self._download_last_line = line
        if bool(p.get("eof")):
            name = self._download_name
            path = os.path.join(CACHE_DIR, name)
            data = "".join(self._download_text)
            try:
                with open(path, "w", newline="") as fh:
                    fh.write(data)
            except OSError as exc:
                self.log_received.emit(f"[Download] Save failed: {exc}", self.C_ERR)
                path = ""
            self._download_cleanup()
            self.download_progress.emit(name, 100)
            self.download_done.emit(name, path)
            self.log_received.emit(f"[Download] Complete → {name}", self.C_TX)
            # Integrity check against the firmware's stored hash (.nc only —
            # the gcodeFileHash API rejects other extensions).
            if path and name.lower().endswith(".nc"):
                digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
                self._pending_hash_check = {name: digest}
                self.request_file_hash(name)
            return
        self.download_progress.emit(self._download_name, self._download_percent(p))
        # Only one request in flight: a late reply to a chunk that was already
        # re-requested must not start a second request chain.
        if self._download_outstanding == 0:
            self._request_download_chunk()

    def _download_percent(self, p: dict) -> int:
        """Progress from bytes when the firmware reports them (downloadedBytes
        / fileSize); older firmware only has lines. totalFileLines is 0
        ("unknown") for files over 32 KB on current firmware."""
        done = p.get("downloadedBytes")
        if isinstance(done, (int, float)) and self._download_size > 0:
            return max(0, min(99, round(done * 100 / self._download_size)))
        if self._download_total > 0:
            return min(99, round(self._download_line_count * 100
                                 / self._download_total))
        return 0

    def _request_download_chunk(self) -> None:
        self._download_outstanding += 1
        self._raw_send({"type": "get", "getType": "nextFileDownloadChunk"})
        if self._download_timer:
            self._download_timer.start(self.DOWNLOAD_CHUNK_WAIT_MS)

    def _download_timeout(self) -> None:
        """No reply in time. Before the start reply there is nothing to retry
        (a second startFileDownload would only be refused as already in
        progress); after it, ask for the chunk again a few times."""
        if self._download_name is None:
            return
        if not self._download_started:
            self._download_failed()
            return
        if self._download_retries >= self.DOWNLOAD_CHUNK_RETRIES:
            self._download_failed()
            return
        self._download_retries += 1
        self.log_received.emit(
            f"[Download] {self._download_name}: no reply, asking again "
            f"({self._download_retries}/{self.DOWNLOAD_CHUNK_RETRIES})",
            self.C_WARN)
        self._request_download_chunk()

    def _download_failed(self, aborted: bool = False) -> None:
        name = self._download_name or ""
        self._download_cleanup()
        if name:
            msg = "aborted" if aborted else "timed out"
            self.log_received.emit(f"[Download] {name} {msg}", self.C_WARN)
            self.download_done.emit(name, "")

    def _download_cleanup(self) -> None:
        self._download_name = None
        self._download_text = []
        self._download_line_count = 0
        self._download_total = 0
        self._download_size = 0
        self._download_last_line = 0
        self._download_started = False
        self._download_outstanding = 0
        self._download_retries = 0
        if self._download_timer:
            self._download_timer.stop()
            self._download_timer.deleteLater()
            self._download_timer = None

    # ── Internal helpers ──────────────────────────────────────────────────

    def _pop_pending_user_req(self) -> str | None:
        """Oldest user-initiated request still awaiting a reply, or None.
        Stale entries (reply consumed by a typed handler, or lost) are dropped
        first so they can't keep re-labelling poll misses as user traffic."""
        now = time.monotonic()
        q = self._pending_user_reqs
        while q and now - q[0][0] > USER_REQ_REPLY_WINDOW_S:
            q.popleft()
        return q.popleft()[1] if q else None

    def _echo_if_asked(self, p: dict, rx_label: str, rx_color: str) -> None:
        """Log a normally-silent reply when it answers something the user sent.

        Called instead of dropping such a reply on the floor. A pending request
        is only claimed when the reply is recognisably its answer, so a poll
        reply landing in the same window cannot consume it and leave the real
        answer invisible (see `_reply_answers`).
        """
        q = self._pending_user_reqs
        now = time.monotonic()
        while q and now - q[0][0] > USER_REQ_REPLY_WINDOW_S:
            q.popleft()
        if not q or not _reply_answers(q[0][1], p):
            return
        req = q.popleft()[1]
        self.log_received.emit(
            f"{rx_label} {json.dumps(p, separators=(',', ':'))} <= {req}",
            rx_color)

    def _send_silent(self, obj: dict) -> None:
        msg = json.dumps(obj, separators=(",", ":"))
        if self._serial.is_connected:
            self._serial.send(msg)

    def _send_silent_any(self, obj: dict) -> None:
        """Silent send on whichever transport is active (serial preferred)."""
        if self._serial.is_connected:
            self._send_silent(obj)
        else:
            self._send_ws_silent(obj)

    def _reset_overrides(self) -> None:
        """Return feed/spindle overrides to 100% and re-read them. Mirrors the
        Android app, which enforces default overrides on every (re)connect and
        whenever a new file is opened (App.tsx ~9951)."""
        for c in ("setFeedRateOverride", "setSpindleOverride"):
            self._send_silent_any({"type": "command", "commandType": c,
                                   "action": "reset"})
        self._send_silent_any({"type": "get", "getType": "getFeedRateOverride"})
        self._send_silent_any({"type": "get", "getType": "getSpindleOverride"})

    def _request_housing_settings(self) -> None:
        """Read the housing / door-interlock settings on every (re)connect.

        `doorOpen` rides on every status poll, but `housingInstalled` is only
        ever sent in a getHousingSettings reply — and _reset_machine_state()
        drops the flag cache on each new link. Without this the interlock stays
        unknown (so nothing gates on the door) until the user happens to open
        the Hardware-settings sheet, which is what asks for it otherwise."""
        self._send_silent_any({"type": "get", "getType": "getHousingSettings"})

    def _set_opened_file(self, name: str | None) -> None:
        """Track the opened file and run the Android-parity side effects on a
        change: the height map is bound to the opened file, so it is cleared
        whenever that file changes or closes (App.tsx ~10345); a newly opened
        file gets its line count fetched (for job progress / preview coloring)
        and starts from default overrides (App.tsx ~9958)."""
        if name == self._opened_file:
            return
        previous = self._opened_file
        self._opened_file = name
        if name is not None:
            self._send_silent_any(
                {"type": "get", "getType": "openedGcodeFileTotalLines"})
            if name != self._last_override_reset_file:
                self._reset_overrides()
                self._last_override_reset_file = name
        self.opened_file_changed.emit(name)

    def _send_ws_silent(self, obj: dict) -> None:
        if self._selected_client is not None:
            self._ws.send_to_client(
                self._selected_client, json.dumps(obj, separators=(",", ":")))

    def _ws_send(self, msg: str) -> None:
        """Send a raw text payload to the selected WiFi machine only."""
        if self._selected_client is not None:
            self._ws.send_to_client(self._selected_client, msg)

    # ── Polling ───────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if self._transfer_active or self._download_name is not None:
            return
        t = self._poll_tick
        self._poll_tick += 1

        if self._serial.is_connected:
            # Serial polling reduced: 2 core queries per 500ms (5/sec) instead of
            # 4 per 200ms (20/sec), matching mobile app's stability. Defer feed rate,
            # spindle RPM, and file list queries to infrequent ticks (every 2.5 sec).
            self._send_silent({"type": "get", "getType": "status"})
            self._send_silent({"type": "get", "getType": "positions"})
            if t % 5 == 0:
                self._send_silent({"type": "get", "getType": "currentFeedRate"})
                self._send_silent({"type": "get", "getType": "currentSpindleRPM"})
            # jobProgress is poll-only in the firmware (it never pushes), and
            # it drives the job bar / ETA / preview progress coloring. Poll only
            # when a job is actually running (mobile parity); file list / opened
            # file every 2.5s (reduced from every cycle).
            if self._opened_file is not None and (
                    self._flags.get("jobRunning") or self._flags.get("jobPaused")):
                self._send_silent({"type": "get", "getType": "jobProgress"})
            if t % 5 == 1:
                self._send_silent({"type": "get", "getType": "fileList"})
                self._send_silent({"type": "get", "getType": "openedGcodeFileName"})
            if t % 10 == 2:
                self._send_silent({"type": "get", "getType": "getFeedRateOverride"})
                self._send_silent({"type": "get", "getType": "getSpindleOverride"})
            if t % 25 == 7:
                self._send_silent({"type": "get", "getType": "getToolLength"})

        elif self._selected_client is not None:
            wt = self._ws_poll_tick
            self._ws_poll_tick += 1
            self._send_ws_silent(_WS_POLL_CYCLE[wt % len(_WS_POLL_CYCLE)])
            # Defer non-critical polls to infrequent ticks.
            if wt % 5 == 4 and self._opened_file is not None:
                self._send_ws_silent({"type": "get", "getType": "jobProgress"})
            if wt % 10 == 2:
                self._send_ws_silent({"type": "get", "getType": "fileList"})
            elif wt % 10 == 3:
                self._send_ws_silent({"type": "get", "getType": "openedGcodeFileName"})
            elif wt % 25 == 7:
                self._send_ws_silent({"type": "get", "getType": "getToolLength"})

    def _ws_keepalive_tick(self) -> None:
        """Heartbeat every attached-but-unselected WiFi machine so the firmware's
        app-level ping watchdog (WEBSOCKET_API_PING_TIMEOUT_MS, 5 s) doesn't force-
        close it during the discovery window or while the chooser is open — which
        would churn the machine list and bounce the splash. Once a machine is
        selected the 200 ms _poll keeps that link alive, so this stands down (it
        also no-ops while serial is connected, since WS clients are refused then).
        1:1 with the Android app's !wsConnected heartbeat (App.tsx ~9764)."""
        if self._selected_client is not None or self._ws_client_count == 0:
            return
        self._ws.send_to_all(json.dumps(
            {"type": "websocketPing"}, separators=(",", ":")))

    # ── Incoming routing ──────────────────────────────────────────────────

    def _note_grbl_limit(self, raw: str) -> None:
        """Turn GRBL's own soft-limit reports into the soft-limit notice.

        A refused move usually arrives as JSON ({"error":"softLimitExceeded"},
        handled in _route). But a motion that is already running when it meets
        travel -- a Frame tracing the job outline, a program -- is stopped by
        the controller itself, which says so only as a raw ``ALARM:2`` line;
        and a jog past travel comes back as ``error:15``. Both used to reach
        nothing but the console, so the machine fell silent into Alarm with no
        word of why. Throttled like the JSON notices.

        The firmware's macros (Frame, Go to XY0, …) check soft limits
        themselves before each move and stop short, leaving the machine Idle:
        ``CPUB: Soft-limit blocked command: G1 F1200 X-69.569 Y-61.715`` then
        ``CPUB: Macro aborted by soft limits at line: …``. That pair is one
        refusal, so both map to one key and the throttle shows one notice,
        naming the position the blocked move was heading for.
        """
        up = raw.upper()
        key = None
        notice = None
        m = re.match(r"ALARM:(\d+)\b", up)
        if m:
            code = int(m.group(1))
            self._last_alarm = (code, time.monotonic())
            if code == 2:
                key = "grblSoftLimitAlarm"
        elif up == "ERROR:15":
            key = "grblJogTravelExceeded"
        else:
            m = re.search(r"SOFT-LIMIT BLOCKED COMMAND:\s*(.*)$"
                          r"|ABORTED BY SOFT LIMITS? AT LINE:\s*(.*)$", up)
            if m:
                key = "macroSoftLimitBlocked"
                notice = _macro_soft_limit_notice(
                    m.group(1) or m.group(2) or "", self._last_pos,
                    bool(self._flags.get("jobRunning")))
        if key is None:
            return
        now = time.monotonic()
        if now - self._motion_error_at.get(key, 0.0) > 3.0:
            self._motion_error_at[key] = now
            self.motion_error.emit(*(notice or _MOTION_ERROR_NOTICES[key]))

    @property
    def last_alarm(self) -> tuple[int, float] | None:
        """(code, monotonic time) of the last ``ALARM:N`` seen, or None."""
        return self._last_alarm

    def _route(self, line: str, from_ws: bool = False) -> None:
        rx_label = "[WS←RX]" if from_ws else "[RX]"
        rx_color = self.C_WS_RX if from_ws else self.C_RX

        # Unblock transfer
        if self._transfer_active and '"result":"success"' in line:
            self._transfer_event.set()

        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            # A file list cut off by the firmware's 8 KB reply limit.
            p = _salvage_file_list(line)
        if p is None:
            # A `$N=value` line from a `$$` listing: remember it, then log it
            # like any other raw reply below.
            m = _GRBL_SETTING_RE.match(line.strip())
            if m:
                n, v = int(m.group(1)), float(m.group(2))
                self._grbl_settings[n] = v
                self.grbl_setting_received.emit(n, v)
            # Raw non-JSON line. GRBL "ok"/"error:N" are genuine replies to a
            # G-code line we sent, so they claim the pending user request. But
            # unsolicited controller pushes (ALARM:*, status <...>, [MSG:...],
            # the reset banner) are async — pairing them with the last click
            # would falsely imply causation, so log them alone and leave the
            # pending queue untouched.
            self._note_grbl_limit(line.strip())
            if _is_unsolicited_grbl_push(line.strip()):
                self.log_received.emit(f"{rx_label} {line}", rx_color)
                return
            req = self._pop_pending_user_req()
            text = f"{rx_label} {line}"
            self.log_received.emit(f"{text} <= {req}" if req else text, rx_color)
            return
        list_truncated = (isinstance(p, dict)
                          and bool(p.pop("_truncated", False)))

        ok = p.get("result") == "success"

        # Machine status
        if ok and "machineStatus" in p:
            s = _normalize_machine_status(p["machineStatus"])
            # While homing, keep reporting "home" (the real state is queued and
            # would otherwise show a stale Idle/Alarm). An alarm ends homing so
            # a genuine fault is surfaced (mobile parity).
            if self._homing:
                if s == "Alarm":
                    self._end_homing()
                else:
                    s = "Home"
            if s == "Alarm" and self._job_watch is not None:
                self._job_watch["stopped"] = True
            if s != self._last_status:
                self._last_status = s
                self.machine_status_changed.emit(s)

        # Positions
        pos_keys = {"machinePositionX", "machinePositionY", "machinePositionZ",
                    "machinePositionA",
                    "jobPositionX",     "jobPositionY",     "jobPositionZ",
                    "jobPositionA"}
        if ok and pos_keys.intersection(p):
            pos = {
                "mx": p.get("machinePositionX", self._last_pos.get("mx", 0.0)),
                "my": p.get("machinePositionY", self._last_pos.get("my", 0.0)),
                "mz": p.get("machinePositionZ", self._last_pos.get("mz", 0.0)),
                "ma": p.get("machinePositionA", self._last_pos.get("ma", 0.0)),
                "jx": p.get("jobPositionX",     self._last_pos.get("jx", 0.0)),
                "jy": p.get("jobPositionY",     self._last_pos.get("jy", 0.0)),
                "jz": p.get("jobPositionZ",     self._last_pos.get("jz", 0.0)),
                "ja": p.get("jobPositionA",     self._last_pos.get("ja", 0.0)),
            }
            if pos != self._last_pos:
                self._last_pos = pos
                self.position_changed.emit(pos)
        if ok and not self._launch_a_zeroed and "jobPositionA" in p:
            self._zero_a_on_launch(p["jobPositionA"])

        # Feed rate / spindle RPM
        if ok and "currentFeedRate" in p:
            try:
                self.feed_rate_changed.emit(int(p["currentFeedRate"]))
            except (TypeError, ValueError):
                pass

        if ok and "currentSpindleRPM" in p:
            try:
                rpm = int(p["currentSpindleRPM"])
            except (TypeError, ValueError):
                rpm = None
            if rpm is not None:
                if rpm <= 0 < self._spindle_rpm:
                    self._spindle_stopped_at = time.monotonic()
                self._spindle_rpm = rpm
                self.spindle_rpm_changed.emit(rpm)

        # Overrides
        if ok and "feedRateOverride" in p:
            try:
                self.feed_override_changed.emit(int(p["feedRateOverride"]))
            except (TypeError, ValueError):
                pass

        if ok and "spindleOverride" in p:
            try:
                self.spindle_override_changed.emit(int(p["spindleOverride"]))
            except (TypeError, ValueError):
                pass

        # Procedure completion messages (probe macros, homing)
        mtype = p.get("type")
        if isinstance(mtype, str) and mtype in _COMPLETION_TYPES:
            result = str(p.get("result", ""))
            if mtype == "homingCompleted":
                # Homing finished — drop the local HOMING override; the next
                # status poll surfaces the real (Idle/Alarm) state.
                self._end_homing()
            if mtype == "probeToolLengthCompleted" and result == "success":
                try:
                    self._tool_length = float(p["toolLength"])
                    self.tool_length_changed.emit(self._tool_length)
                except (KeyError, TypeError, ValueError):
                    pass
            self.procedure_completed.emit(mtype, result, p)
            self.log_received.emit(f"{rx_label} {line}", rx_color)
            return

        # Boolean machine flags
        changed_flags = {}
        for k in _FLAG_KEYS:
            if k in p and isinstance(p[k], bool) and self._flags.get(k) != p[k]:
                self._flags[k] = p[k]
                changed_flags[k] = p[k]
        if changed_flags:
            self.machine_flags_changed.emit(changed_flags)
            self._watch_job(changed_flags)

        # Tool length (polled)
        if ok and "toolLength" in p and mtype is None:
            try:
                tl = float(p["toolLength"])
                if tl != self._tool_length:
                    self._tool_length = tl
                    self.tool_length_changed.emit(tl)
            except (TypeError, ValueError):
                pass

        # Job progress (suppressed during downloads — their chunks reuse the
        # currentFileLine/totalFileLines keys and would move the job bar)
        if (self._download_name is None
                and ("currentFileLine" in p or "jobProgress" in p
                     or "totalFileLines" in p)):
            jp = dict(self._job_progress)
            try:
                if "currentFileLine" in p:
                    jp["currentLine"] = int(p["currentFileLine"])
                if "totalFileLines" in p:
                    jp["totalLines"] = int(p["totalFileLines"])
                if "estimatedRemainingSeconds" in p:
                    jp["etaSeconds"] = max(0, int(p["estimatedRemainingSeconds"]))
                if "jobProgress" in p:
                    jp["percent"] = max(0, min(100, int(p["jobProgress"])))
                elif jp["totalLines"] > 0:
                    jp["percent"] = max(0, min(100, round(jp["currentLine"] * 100 / jp["totalLines"])))
            except (TypeError, ValueError):
                pass
            if self._job_watch is not None:
                self._job_watch["peak"] = max(self._job_watch["peak"],
                                              int(jp.get("percent", 0) or 0))
            if jp != self._job_progress:
                self._job_progress = jp
                self.job_progress_changed.emit(dict(jp))

        # Machine identity / stats / persistent hardware settings
        info_changed = {}
        for k in ("machineName", "firmwareVersion", "deviceId",
                  "powerOnHours", "runHours", "jobCount",
                  "probeHeight", "toolLengthReferenceZ", "spindleType"):
            if k in p and self._machine_info.get(k) != p[k]:
                self._machine_info[k] = p[k]
                info_changed[k] = p[k]
        if info_changed:
            self.machine_info_changed.emit(dict(self._machine_info))

        # WiFi / hotspot settings
        wifi_changed = False
        for k in _WIFI_KEYS:
            if k in p and self._wifi.get(k) != p[k]:
                self._wifi[k] = p[k]
                wifi_changed = True
        if wifi_changed:
            self.wifi_settings_changed.emit(dict(self._wifi))

        # Firmware debugging switches. Emitted on every reply, not on change:
        # a set is only answered with a bare success, so the re-read after it
        # is what confirms the switch, even when nothing changed.
        if ok and "debuggingBase" in p:
            self.debugging_settings_received.emit(
                {k: bool(p[k]) for k in DEBUGGING_KEYS if k in p})

        if ok and "machineStatus" not in p:
            # The opened file's MIN/MAX. Keyed on fileMinXmm, which no other
            # reply carries; `openedFileName` alone would also match a plain
            # file-name poll.
            if "fileMinXmm" in p:
                self.opened_file_bounds_received.emit(dict(p))
                return self._echo_if_asked(p, rx_label, rx_color)

        # Stored file hash (gcodeFileHash) — background integrity checks.
        if ok and "fileSha256" in p:
            fname = str(p.get("fileName", ""))
            sha = str(p.get("fileSha256", "")).strip().lower()
            try:
                self._hash_req_names.remove(fname)
            except ValueError:
                pass
            expected = self._pending_hash_check.pop(fname, None)
            if expected is not None:
                # Download just completed: record the firmware hash so cache
                # freshness can later be checked hash-to-hash (downloads are
                # normalized — blank/comment lines are not transferred — so a
                # local digest only matches for comment-free files).
                self._store_fw_hash(fname, sha)
                if expected == sha:
                    self.log_received.emit(
                        f"[Download] {fname} hash verified", self.C_INFO)
            self.file_hash_received.emit(fname, sha)
            return  # silent

        # Job history pages
        if ok and isinstance(p.get("jobHistory"), list):
            self.job_history_received.emit(p)
            return

        # Download start-ack: {fileName, totalFileLines} without fcl → pull 1st chunk
        if (self._download_name is not None and ok and not self._download_started
                and "fileName" in p and "totalFileLines" in p and "fcl" not in p):
            # totalFileLines is 0 ("unknown") for big files on current
            # firmware; progress then comes from downloadedBytes / fileSize.
            for attr, key in (("_download_total", "totalFileLines"),
                              ("_download_size", "fileSize")):
                try:
                    setattr(self, attr, max(0, int(p.get(key) or 0)))
                except (TypeError, ValueError):
                    setattr(self, attr, 0)
            self._download_started = True
            self._request_download_chunk()
            return

        # Download chunk: {fcl, eof} → store, then pull the next one
        if self._download_name is not None and isinstance(p.get("fcl"), str):
            self._download_chunk(p)
            return

        # Download failure (e.g. fileNotFound) while a download is pending
        if (self._download_name is not None and not ok
                and (p.get("error") in ("fileNotFound", "noFileDownloadInProgress")
                     or (not self._download_started and p.get("error") in (
                         "fileTransferInProgress", "fileDownloadInProgress")))):
            self._download_failed()
            return

        # File list (firmware uses "fileList"; older payloads used "gcodeFileList")
        if ok and ("fileList" in p or "gcodeFileList" in p):
            files = p.get("fileList", p.get("gcodeFileList"))
            if isinstance(files, list):
                self._file_meta = {
                    f.get("fileName", ""): f for f in files if isinstance(f, dict)
                }
                snap = tuple(
                    (f.get("fileName", ""), f.get("fileSize", 0), f.get("fileDate", ""))
                    for f in files if isinstance(f, dict)
                ) + (("__truncated__", list_truncated, ""),)
                if list_truncated != self._file_list_truncated:
                    self._file_list_truncated = list_truncated
                    if list_truncated:
                        self.log_received.emit(
                            f"[Files] The machine's file list is cut off at its "
                            f"8 KB reply limit: {len(files)} files listed, any "
                            f"after {files[-1].get('fileName', '?') if files else '?'} "
                            f"are missing. Delete old files to see them.",
                            self.C_WARN)
                if snap != self._file_list_snapshot:
                    self._file_list_snapshot = snap
                    self.file_list_changed.emit(files)
            return self._echo_if_asked(p, rx_label, rx_color)

        # Opened file (key varies: openedFileName / openedGcodeFileName)
        if ok and ("openedFileName" in p or "openedGcodeFileName" in p):
            name = str(p.get("openedFileName", p.get("openedGcodeFileName"))).strip() or None
            self._set_opened_file(name)
            return self._echo_if_asked(p, rx_label, rx_color)

        # User-facing motion rejections (mobile parity, App.tsx ~9491): the
        # firmware refused a move/jog. Surfaced as a popup, throttled per error
        # so a held jog's stream of rejections notifies only once.
        if not ok:
            err = p.get("error")
            # A rejected upload. Handled before the motion notices because the
            # worker thread is blocked on an ack that is never coming, and the
            # cost of not noticing is not just a missing message: it streams
            # the rest of the file at one five-second timeout per chunk into a
            # machine that has already deleted the temp file, then reports the
            # upload complete. See _fail_transfer.
            if self._transfer_active and err in _TRANSFER_ERROR_NOTICES:
                self._fail_transfer(err, p)
            notice = _MOTION_ERROR_NOTICES.get(err)
            if notice is not None:
                now = time.monotonic()
                if now - self._motion_error_at.get(err, 0.0) > 3.0:
                    self._motion_error_at[err] = now
                    self.motion_error.emit(*notice)

        # Expected polling miss: bare fileNotFound / noFileOpened /
        # fileHashNotFound (see USER_REQ_REPLY_WINDOW_S). fileNotFound doubles
        # as "no file open" for the openedGcodeFileName poll, so it also
        # clears the opened file. Shown only when a user request claims it.
        poll_miss = (not ok and len(p) == 2
                     and p.get("error") in ("fileNotFound", "noFileOpened",
                                            "fileHashNotFound"))
        if poll_miss:
            err = p.get("error")
            # Claim the answering user request BEFORE the side effects below:
            # closing the file has its own pending entry, which must
            # not be attributed to this (earlier) reply.
            req = self._pop_pending_user_req()
            # Only an UNCLAIMED fileNotFound is the openedGcodeFileName poll
            # miss — the same bare payload also answers user requests like
            # openGcodeFile/deleteGcodeFile on a missing file, and treating
            # those as "file closed" would wrongly clear the height map.
            if err == "fileNotFound" and req is None:
                self._set_opened_file(None)
            if err == "fileHashNotFound":
                from_download = bool(self._pending_hash_check)
                self._pending_hash_check.clear()
                if self._hash_req_names:
                    fname = self._hash_req_names.popleft()
                    if from_download:
                        self.log_received.emit(
                            f"[Download] {fname}: no stored hash on the machine "
                            f"(older file) — skipping verification", self.C_INFO)
                    # "" tells listeners the machine cannot verify this file.
                    self.file_hash_received.emit(fname, "")
                return
            if req is not None:
                self.log_received.emit(
                    f"{rx_label} {json.dumps(p, separators=(',', ':'))} <= {req}",
                    rx_color)
            return

        # Silent-key responses: the poll's own traffic, kept out of the log —
        # unless this one is the answer to something the user sent.
        if ok and _SILENT_KEYS.intersection(p.keys()):
            return self._echo_if_asked(p, rx_label, rx_color)

        # Everything else → log, tagged with the user request it answers when
        # one is pending; bare success acks from silent sends (jog keep-alives,
        # connect-time setup) stay silent.
        req = self._pop_pending_user_req()
        if ok and len(p) == 1 and req is None:
            return
        text = f"{rx_label} {json.dumps(p, separators=(',', ':'))}"
        self.log_received.emit(f"{text} <= {req}" if req else text, rx_color)

    # ── Transport callbacks ───────────────────────────────────────────────

    def _on_serial_line(self, line: str) -> None:
        self._route(line, from_ws=False)

    def _on_ws_line(self, client_id: str, line: str) -> None:
        # Always learn identity (machineName / deviceId / firmware) even before a
        # machine is selected — that is how discovery populates the chooser.
        self._learn_identity(client_id, line)
        # Only the selected machine's traffic drives the UI.
        if self._accept_ws_message(client_id):
            self._route(line, from_ws=True)

    def _watch_job(self, changed: dict) -> None:
        """Follow a job from jobRunning=true to its end (job_finished).

        Pause and resume keep the same job: jobRunning drops while paused, so
        the end is only judged once neither flag is set, and only after
        JOB_END_SETTLE_MS, in case jobPaused trails jobRunning by a poll."""
        if changed.get("jobRunning") is True:
            self._job_end_timer.stop()
            if self._job_watch is not None:
                self._job_watch.pop("ended", None)     # resumed: not over
            if self._job_watch is None:
                self._job_watch = {"file": self._opened_file or "",
                                   "started": time.monotonic(),
                                   "stopped": False, "peak": 0}
        elif (self._job_watch is not None
              and not self._flags.get("jobRunning")
              and not self._flags.get("jobPaused")):
            # the job ended NOW; the timer only confirms it is not a pause,
            # so its settle time must not count as run time
            self._job_watch.setdefault("ended", time.monotonic())
            self._job_end_timer.start()

    def _confirm_job_end(self) -> None:
        watch, self._job_watch = self._job_watch, None
        if watch is None:
            return
        if self._flags.get("jobRunning") or self._flags.get("jobPaused"):
            self._job_watch = watch          # back on (resume): keep watching
            return
        peak = watch["peak"]
        if watch["stopped"] or (0 < peak < JOB_DONE_MIN_PERCENT):
            return
        self.job_finished.emit(watch["file"],
                               watch.get("ended", time.monotonic()) - watch["started"])

    def job_elapsed(self) -> float | None:
        """Seconds the job being watched has run so far, or None."""
        w = self._job_watch
        return None if w is None else time.monotonic() - w["started"]

    def _reset_machine_state(self) -> None:
        """Forget the accumulated machine snapshot (flags + status) so the next
        (re)connection re-emits them as changes. machine_flags_changed and
        machine_status_changed only fire on a *delta*, so without this a
        reconnect to a machine in the same state would silently skip them —
        which is how the initial homing overlay could fail to reappear on
        reconnect (its rotary/homed state never re-arrived)."""
        if self._flags:
            self._flags = {}
        self._last_status = None
        # GRBL settings belong to the machine just left; re-read on demand.
        self._grbl_settings = {}
        # Also the tool length: it is emitted on a delta too, so a machine
        # reporting the same length as the last one would never re-emit and
        # the card would sit on its placeholder for good.
        self._tool_length = None
        self._end_homing()
        # A job seen only up to a disconnect has no known ending.
        self._job_watch = None
        self._job_end_timer.stop()

    def _on_serial_state(self, connected: bool, msg: str) -> None:
        self._serial_connected = connected
        self._pending_user_reqs.clear()
        self._last_override_reset_file = None
        self._reset_machine_state()
        if connected:
            # A cable turned up while the developer offline mode was simulating
            # a machine — drop the simulation first, so the identity/flags below
            # land on a clean slate and serial_state_changed (emitted at the end
            # of this handler) is what announces the link.
            self._leave_offline_for_real_link()
            # Ask the firmware to reply in JSON, then identify the device.
            self._send_silent({"type": "set", "setType": "setJsonSettings",
                               "jsonResponse": True})
            self._send_silent({"type": "get", "getType": "deviceId"})
            self._send_silent({"type": "get", "getType": "machineName"})
            # Enforce default overrides on every (re)connect (Android parity).
            self._reset_overrides()
            self._request_housing_settings()
            # Serial is the preferred transport — cancel the head-start, turn the
            # WiFi fallback back off, stop the BLE discovery beacon, and tear down
            # any in-flight WiFi discovery / selection.
            self._serial_headstart_timer.stop()
            self._wifi_enabled = False
            self._ble.stop()
            self._cancel_ws_discovery_for_serial()
        else:
            # Serial gone: switch on the WiFi fallback (BLE beacon + WS clients)
            # immediately so the machine can find us over WiFi.
            self._enable_wifi_fallback()
        self._sync_poll_timer()
        self.serial_state_changed.emit(connected, msg)
        color = self.C_INFO
        self.log_received.emit(f"[Serial] {msg}", color)

    def _on_probe_status(self, msg: str) -> None:
        if self._selected_client is None:
            self.log_received.emit(f"[Discovery] {msg}", self.C_INFO)

    def _on_ws_log(self, text: str, color: str) -> None:
        self.log_received.emit(text, color)

    def _on_ws_state(self, running: bool) -> None:
        if not running:
            # Server stopped → drop every machine and any active selection.
            self._pending_user_reqs.clear()
            self._machines.clear()
            self._seen_device_ids.clear()
            self._cancel_discovery_timer()
            if self._selected_client is not None:
                self._selected_client = None
                self.ws_connected_changed.emit(False)
            if self._selection_phase == "choosing":
                self.machine_chooser_dismissed.emit()
            self._selection_phase = "idle"
            self._force_chooser = False
            self._sync_poll_timer()
            self._on_ws_clients(0)

    def _on_ws_clients(self, count: int) -> None:
        # Raw connection count, for the status line only. The handshake and
        # identity queries now happen per-client in _on_ws_client_connected, and
        # the poll timer follows selection (see _sync_poll_timer).
        self._ws_client_count = count
        self.ws_client_count_changed.emit(count)

    # ── Multi-machine WiFi discovery / selection ──────────────────────────

    def _on_ws_client_connected(self, client_id: str) -> None:
        """A WiFi machine opened a connection. Serial wins if present; otherwise
        register the machine, identify it, and (re)arm the discovery window."""
        # Refuse WiFi while serial is connected OR while the WiFi fallback is still
        # gated behind the serial head-start (see start() / SERIAL_HEADSTART_MS).
        # The latter keeps a cabled machine from ever entering WiFi discovery / the
        # multi-machine chooser — without the beacon we shouldn't get here anyway,
        # but a machine that dials from a prior session's advertised IP would.
        if self._serial.is_connected or not self._wifi_enabled:
            self._ws.close_client(client_id)
            return
        self._machines[client_id] = {
            "clientId": client_id, "name": None,
            "deviceId": None, "firmware": None,
        }
        # Ask it to reply in JSON, then identify itself.
        self._ws.send_to_client(client_id, json.dumps(
            {"type": "set", "setType": "setJsonSettings", "jsonResponse": True},
            separators=(",", ":")))
        for gt in ("machineName", "deviceId", "firmwareVersion"):
            self._ws.send_to_client(client_id, json.dumps(
                {"type": "get", "getType": gt}, separators=(",", ":")))

        if self._selection_phase in ("idle", "searching"):
            self._selection_phase = "searching"
            # Arm the window only if it isn't already counting down. Do NOT reset
            # it here — the deadline is pushed out solely when a genuinely-new
            # deviceId is identified (see _learn_identity), so reconnect churn
            # can't keep the window (and the splash) open forever (mobile parity).
            self._arm_discovery_if_idle()
        elif self._selection_phase == "choosing":
            # A new machine appeared while the chooser is open — refresh it.
            self.machine_list_changed.emit(self._build_choices())
        # phase == "connected": registered for deviceId reconnect-matching only.

    def _on_ws_client_disconnected(self, client_id: str) -> None:
        was_selected = client_id == self._selected_client
        self._machines.pop(client_id, None)
        if not self._machines:
            # Every machine has dropped → the round is over. Forget the seen
            # deviceIds so a fresh round re-extends the window for each one as it
            # reappears (mobile parity); a transient single-machine drop keeps
            # them because the field isn't empty.
            self._seen_device_ids.clear()
        if self._selection_phase == "choosing":
            choices = self._build_choices()
            if not choices:
                # Everyone vanished while the chooser is open → close it. Do NOT
                # auto-select a lone survivor here: the chooser is up precisely so
                # the user picks, and auto-selecting made it flicker shut (e.g.
                # when one machine dialled twice and a transient socket dropped).
                self._selection_phase = "idle"
                self._force_chooser = False
                self.machine_chooser_dismissed.emit()
            else:
                self.machine_list_changed.emit(choices)
        if was_selected:
            self._reset_ws_selection()

    def _arm_discovery(self) -> None:
        """(Re)start the discovery window; _resolve_discovery fires at the end.

        Always *resets* the countdown — call this only when a genuinely-new
        machine widens the field (a new deviceId), never for mere reconnect
        churn (use _arm_discovery_if_idle for socket (re)connects)."""
        if self._discovery_timer is None:
            self._discovery_timer = QTimer(self)
            self._discovery_timer.setSingleShot(True)
            self._discovery_timer.timeout.connect(self._resolve_discovery)
        self._discovery_timer.start(MACHINE_DISCOVERY_WINDOW_MS)

    def _arm_discovery_if_idle(self) -> None:
        """Start the discovery window only if it isn't already counting down.

        A reconnecting machine must not keep pushing the deadline out, so a
        socket (re)connect arms the window but never resets a running one
        (mobile-app parity)."""
        if self._discovery_timer is None or not self._discovery_timer.isActive():
            self._arm_discovery()

    def _discovery_self_heal(self) -> None:
        """Safety net (mobile parity): if WiFi machines are attached but the
        discovery window isn't pending — e.g. a missed event / race left it
        unarmed — re-arm it so the splash can never get stuck forever. Never
        disturbs an open chooser ('choosing') or an active link (serial or an
        already-selected machine)."""
        if self._serial.is_connected or self._selected_client is not None:
            return
        if (self._selection_phase in ("idle", "searching") and self._machines
                and (self._discovery_timer is None
                     or not self._discovery_timer.isActive())):
            self._selection_phase = "searching"
            self._arm_discovery()

    def _cancel_discovery_timer(self) -> None:
        if self._discovery_timer is not None:
            self._discovery_timer.stop()

    def _resolve_discovery(self) -> None:
        """End of the discovery window: 0 → wait, 1 → auto-connect, 2+ → chooser.

        Only ever runs from the "searching" phase (the discovery timer). Once the
        chooser is open ("choosing") nothing re-resolves — that would risk
        auto-selecting out from under an open chooser."""
        if self._selection_phase != "searching":
            return
        choices = self._build_choices()
        ids = ", ".join((c.get("deviceId") or c["clientId"]) for c in choices)
        # While offline holds a multi-machine field open the window re-resolves
        # every few seconds; log it only when the field actually changes, so the
        # console isn't flooded with the same line.
        if not (self._offline and ids == self._last_discovery_ids):
            self.log_received.emit(
                f"[Discovery] Window elapsed: {len(choices)} machine(s) [{ids}]",
                self.C_INFO)
        self._last_discovery_ids = ids
        if not choices:
            # Nothing yet — keep waiting (and keep _force_chooser armed so the
            # picker still appears once a machine finally announces).
            self._selection_phase = "idle" if not self._machines else "searching"
            return
        if len(choices) == 1 and not self._force_chooser:
            self._select_machine(choices[0]["clientId"])
        elif self._offline and not self._force_chooser:
            # Several machines answered while the developer offline mode is
            # running. A lone machine is unambiguous and takes over silently
            # (above), but throwing a modal picker over a session the user
            # deliberately put into offline mode is not something they asked
            # for — and picking one here would yank them out of it mid-task.
            # Hold the round open instead: the machines stay registered and
            # keep being refreshed, and the user connects when ready via the
            # app menu (Machine connection → Connect), whose exit_offline_mode()
            # re-runs discovery and shows the picker for real.
            self._selection_phase = "searching"
        else:
            self._selection_phase = "choosing"
            self._force_chooser = False
            self.machine_chooser_requested.emit(choices)

    def _dedupe_machines(self) -> list:
        """Machines collapsed by deviceId (latest connection wins); machines that
        have not reported a deviceId yet are kept individually."""
        by_device: dict[str, dict] = {}
        no_id: list = []
        for m in self._machines.values():
            did = m.get("deviceId")
            if did:
                prev = by_device.get(did)
                if prev is None or int(m["clientId"]) > int(prev["clientId"]):
                    by_device[did] = m
            else:
                no_id.append(m)
        out = list(by_device.values()) + no_id
        out.sort(key=lambda m: int(m["clientId"]))
        return out

    def _build_choices(self) -> list:
        """Deduped machine list with a human label on each entry."""
        out = []
        for i, m in enumerate(self._dedupe_machines()):
            label = m.get("name") or m.get("deviceId") or f"Machine {i + 1}"
            out.append({**m, "label": label})
        return out

    def _select_machine(self, client_id: str) -> None:
        m = self._machines.get(client_id)
        if m is None:
            return
        # A machine dialled in while the developer offline mode was simulating
        # one — drop the simulation; the ws_connected_changed(True) at the end
        # of this method is what announces the real link.
        self._leave_offline_for_real_link()
        # Fresh link → forget any prior machine snapshot so status/flags
        # re-emit as changes (see _reset_machine_state).
        self._reset_machine_state()
        self._selected_client = client_id
        self._selected_device_id = m.get("deviceId") or self._selected_device_id
        self._selection_phase = "connected"
        self._force_chooser = False
        self._cancel_discovery_timer()
        # Drive only the chosen machine — close every other connection.
        for cid in list(self._machines.keys()):
            if cid != client_id:
                self._ws.close_client(cid)
                self._machines.pop(cid, None)
        self._sync_poll_timer()
        # The connect-time identity replies arrived during discovery and were
        # not routed, so _machine_info is still empty. Seed it from what we
        # learned (instant top-bar title) and re-query now that routing is live
        # (authoritative — mirrors what serial does on connect).
        self._seed_machine_info(m)
        for gt in ("machineName", "firmwareVersion", "deviceId"):
            self._send_ws_silent({"type": "get", "getType": gt})
        # WiFi machine connected → stop advertising our IP over BLE; the beacon
        # is only a discovery side-channel and isn't needed once connected. It
        # restarts on selection loss / rescan so a machine can rediscover us.
        self._ble.stop()
        # Enforce default overrides on every (re)connect (Android parity).
        self._last_override_reset_file = None
        self._reset_overrides()
        self._request_housing_settings()
        self.machine_selected.emit(dict(m))
        self.ws_connected_changed.emit(True)
        label = m.get("name") or m.get("deviceId") or f"machine {client_id}"
        self.log_received.emit(f"[WS] Connected to {label}", self.C_INFO)

    def _seed_machine_info(self, m: dict) -> None:
        """Push a selected machine's discovered identity into _machine_info."""
        seed = {}
        if m.get("name"):
            seed["machineName"] = m["name"]
        if m.get("firmware"):
            seed["firmwareVersion"] = m["firmware"]
        if m.get("deviceId"):
            seed["deviceId"] = m["deviceId"]
        changed = {k: v for k, v in seed.items() if self._machine_info.get(k) != v}
        if changed:
            self._machine_info.update(changed)
            self.machine_info_changed.emit(dict(self._machine_info))

    def _reset_ws_selection(self) -> None:
        """The selected machine went away. Release it but remember its deviceId
        so the same machine auto-reconnects without prompting."""
        self._reset_machine_state()
        self._selected_client = None
        self._selection_phase = "searching" if self._machines else "idle"
        self._seen_device_ids.clear()   # back to a fresh search round
        self._sync_poll_timer()
        self.ws_connected_changed.emit(False)
        # No longer connected → resume advertising so the machine can find us
        # again (no-op if serial has since taken over).
        self._enable_wifi_fallback()
        if self._machines:
            self._arm_discovery()

    def _cancel_ws_discovery_for_serial(self) -> None:
        """Serial just connected (preferred) — tear down WiFi discovery, close
        every WS machine connection, and forget the remembered deviceId."""
        self._cancel_discovery_timer()
        had_selection = self._selected_client is not None
        was_choosing = self._selection_phase == "choosing"
        for cid in list(self._machines.keys()):
            self._ws.close_client(cid)
        self._machines.clear()
        self._seen_device_ids.clear()
        self._selected_client = None
        self._selected_device_id = None
        self._selection_phase = "idle"
        self._force_chooser = False
        if had_selection:
            self.ws_connected_changed.emit(False)
        if was_choosing:
            self.machine_chooser_dismissed.emit()

    def _sync_poll_timer(self) -> None:
        """Run the poll timer iff a transport is active (serial or selected WS)."""
        active = self._serial.is_connected or self._selected_client is not None
        if active and not self._poll_timer.isActive():
            self._poll_timer.start()
        elif not active and self._poll_timer.isActive():
            self._poll_timer.stop()

    def _learn_identity(self, client_id: str, line: str) -> None:
        """Parse identity fields from a machine's reply during discovery."""
        m = self._machines.get(client_id)
        if m is None:
            return
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(p, dict):
            return
        changed = new_device = False
        if isinstance(p.get("machineName"), str) and p["machineName"] != m.get("name"):
            m["name"] = p["machineName"]; changed = True
        if (isinstance(p.get("firmwareVersion"), str)
                and p["firmwareVersion"] != m.get("firmware")):
            m["firmware"] = p["firmwareVersion"]; changed = True
        did = p.get("deviceId")
        if isinstance(did, str) and did and did != m.get("deviceId"):
            m["deviceId"] = did; changed = new_device = True
        if not changed:
            return
        if new_device:
            # Diagnostic: surfaces every machine that announces a stable deviceId,
            # with the phase it arrived in — so the Console shows whether 2 WiFi
            # machines really both reach discovery (distinct deviceIds) or only one.
            self.log_received.emit(
                f"[Discovery] Identified '{m.get('name') or '?'}' "
                f"deviceId={did} (client {client_id}, phase {self._selection_phase})",
                self.C_INFO)
        # Fast reconnect: a machine matching the last-selected deviceId reappeared
        # after a drop → re-select it immediately, no waiting / prompting. Only
        # while searching — never while a chooser is open, or it would close the
        # chooser out from under the user.
        if (new_device and self._selected_client is None
                and self._selection_phase == "searching"
                and self._selected_device_id is not None
                and m.get("deviceId") == self._selected_device_id):
            self._select_machine(client_id)
            return
        # The first time we see a stable deviceId this round, a genuinely-new
        # machine has widened the field → extend the window so slow announcers
        # aren't missed. Reconnections of an already-seen machine (a new clientId
        # re-reporting a known deviceId) must NOT extend it, or the window never
        # elapses under reconnect churn — the reported "no chooser, never
        # connects" bug (mobile-app parity).
        if (new_device and self._selection_phase == "searching"
                and did not in self._seen_device_ids):
            self._seen_device_ids.add(did)
            self._arm_discovery()
        if self._selection_phase in ("searching", "choosing"):
            self.machine_list_changed.emit(self._build_choices())

    def _accept_ws_message(self, client_id: str) -> bool:
        """Whether a message from *client_id* should drive the UI."""
        if self._serial.is_connected:
            return False
        if self._selection_phase != "connected" or self._selected_client is None:
            return False
        if client_id == self._selected_client:
            return True
        # A different clientId is talking. If it is the same physical machine
        # (deviceId match) it has reconnected → adopt the new clientId.
        m = self._machines.get(client_id)
        did = m.get("deviceId") if m else None
        if did and self._selected_device_id and did == self._selected_device_id:
            self._selected_client = client_id
            return True
        return False

    # ── File transfer ─────────────────────────────────────────────────────

    def _raw_send(self, obj: dict) -> None:
        msg = json.dumps(obj, separators=(",", ":"))
        if self._serial.is_connected:
            self._serial.send(msg)
        elif self._selected_client is not None:
            self._ws.send_to_client(self._selected_client, msg)

    def _transfer_worker(self, lines: list, filename: str, file_date: str) -> None:
        import time
        CHUNK = 50
        self._transfer_active = True
        self._transfer_event.clear()
        self._abort_transfer = False
        self._transfer_error = None
        # transfer_done fires from the finally block for success, abort AND
        # error alike, so callers that need to act only on a completed upload
        # (deleting a staged CAM export, say) have to check this afterwards.
        ok = False

        try:
            self._raw_send({"type": "command", "commandType": "abortFileTransfer"})
            time.sleep(0.5)
            self._raw_send({"type": "command", "commandType": "startFileTransfer",
                            "filename": filename, "filelines": len(lines),
                            "fileDate": file_date})
            time.sleep(0.5)
            self._transfer_event.clear()

            sent = 0
            while sent < len(lines) and not self._abort_transfer:
                end = min(sent + CHUNK, len(lines))
                payload = "|".join(lines[sent:end])
                self._raw_send({"type": "fc", "fcl": payload})
                sent = end
                self.transfer_progress.emit(sent)
                last = sent >= len(lines)
                if last:
                    # The ack to the last chunk only comes once the machine
                    # has copied and hashed the whole file (see
                    # TRANSFER_FINALIZE_TIMEOUT_S). Keep the link quiet until
                    # then: the poll stays off while _transfer_active is set.
                    self.transfer_finalizing.emit()
                wait_s = TRANSFER_FINALIZE_TIMEOUT_S if last else 5.0
                if not self._transfer_event.wait(timeout=wait_s):
                    self.log_received.emit(
                        "[Transfer] Warning: no ack – continuing…", self.C_WARN
                    )
                self._transfer_event.clear()

            if not self._abort_transfer:
                ok = True
                self.log_received.emit(
                    f"[Transfer] Complete → {filename}", self.C_TX
                )
        except Exception as exc:
            self.log_received.emit(f"[Transfer] Error: {exc}", self.C_ERR)
        finally:
            self._last_transfer_ok = ok
            # Re-emit the next file list even if unchanged: the file manager
            # holds a "processing" row until a post-upload list names the file.
            self._file_list_snapshot = ()
            self._transfer_active = False
            self._abort_transfer = False
            self.transfer_done.emit()
