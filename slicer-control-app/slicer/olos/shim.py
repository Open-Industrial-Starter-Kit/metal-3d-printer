"""
slicer/olos/shim.py
The handful of OLOS server pieces the vendored slicer engine imports.

The engine (``prusa_slicer.py``, ``slicers_shared_methods.py``) came from the
OLOS_Server tree, where it sat inside a FastAPI application and reached for four
server modules. Measured against the vendored files, that reach is tiny: five
constants, one helper method, a temp-file factory, and a progress message. This
module supplies those four things and nothing else, so the engine runs inside a
PySide6 desktop app with no server, no websocket and no event loop.

The alternative was to vendor ``core/constants.py`` (9.5 KB of machine, GRBL,
camera and websocket constants), ``utils/image_to_gcode_helper.py`` (475 lines,
which imports ``svg2gcode``), ``utils/tmp_files_manager.py`` and
``utils/websocket_json_data.py``. All four would arrive almost entirely unused:
the engine touches exactly ``NEW_LINE``, ``SLICE_DATA_TYPE``,
``MIDDLE_PRIORITY_COMMAND``, ``GCODE_CONTOUR_LAYER_COMMENT``,
``GCODE_INFILL_LAYER_COMMENT`` and ``add_end_of_job_commands()``. Keeping the
surface this narrow is what makes the engine updatable: a new upstream revision
of the two engine files can be dropped in with its imports rewritten, and this
file is the only thing that has to still fit.

Values are copied from OLOS_Server/src/core/constants.py so the generated
G-code is byte-identical to what OLOS produces for the same settings.
"""

import atexit
import os
import tempfile


class CoreConstants:
    """The five constants the engine reads, at their upstream values."""

    #: OLOS emits G-code with Unix line endings regardless of host platform.
    NEW_LINE = '\n'

    #: Progress-message tag and priority. Retained because the engine passes
    #: them to the write queue; our queue shim ignores both (see ProgressQueue).
    SLICE_DATA_TYPE = 'SLICE'
    MIDDLE_PRIORITY_COMMAND = 2

    #: Section markers the engine writes above each block of a layer. The layer
    #: preview and the M610/M611 retargeting both key off these, so they are
    #: load-bearing comments, not decoration.
    GCODE_CONTOUR_LAYER_COMMENT = "; contour"
    GCODE_INFILL_LAYER_COMMENT = "; infill"

    #: End-of-job pair, used only by ImageToGcodeHelper below.
    GRBL_COOLANT_OFF = 'M9'
    GRBL_END_PROGRAM = 'M2'


class ImageToGcodeHelper:
    """Stands in for OLOS's 475-line helper.

    The vendored engine calls exactly one of its methods --
    ``add_end_of_job_commands`` -- which is why the real helper, and the
    ``svg2gcode`` package it imports at module scope, are both left behind.
    """

    def add_end_of_job_commands(self):
        c = CoreConstants
        return c.NEW_LINE + c.GRBL_COOLANT_OFF + c.NEW_LINE + c.GRBL_END_PROGRAM


class TempFilesManager:
    """Temp files that outlive their handle and are removed at exit.

    The engine writes PrusaSlicer's SL1 archive to one of these and reopens it
    by path, so ``delete=False`` is required, not incidental.
    """

    def __init__(self):
        self._tmp_files = []
        atexit.register(self.cleanup)

    def create_temp_file(self, suffix=''):
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        self._tmp_files.append(tmp_file.name)
        tmp_file.close()
        return tmp_file.name

    def cleanup(self):
        while self._tmp_files:
            path = self._tmp_files.pop()
            try:
                os.unlink(path)
            except OSError:
                pass


class WebsocketJsonData:
    """Progress payload, reduced to the one field a desktop caller wants.

    Upstream this was a JSON string bound for a browser over a websocket. Here
    it is a plain tuple, because the consumer is a Qt signal in the same
    process and serialising through JSON to immediately parse it back would be
    pure overhead.
    """

    @staticmethod
    def parse_slicing_progress_to_json(layer_index, total_layers):
        return (layer_index, total_layers)


class ProgressQueue:
    """A ``websocket_write_queue`` the engine can ``put()`` into.

    The engine calls ``queue.put(data_type, priority, payload)`` -- three
    positional arguments, not the one ``queue.Queue.put`` takes -- so a stdlib
    queue cannot be passed directly. This adapter accepts that signature,
    drops the routing arguments that only mean something to a websocket, and
    hands the payload to a callback.

    Pass ``None`` for ``on_progress`` to discard progress entirely, which is
    what a headless or test caller wants.
    """

    def __init__(self, on_progress=None):
        self._on_progress = on_progress

    def put(self, data_type, priority, payload):
        if self._on_progress is None:
            return
        layer_index, total_layers = payload
        self._on_progress(layer_index, total_layers)
