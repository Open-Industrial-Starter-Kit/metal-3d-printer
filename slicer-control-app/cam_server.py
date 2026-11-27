"""
cam_server.py
Loopback-only static HTTP hosting for the vendored browser CAM tools.

Kiri:Moto and EasyTrace5000 are pure client-side JS/WASM, but neither runs from
``file://``: ES-module imports, web workers and WASM fetches are all blocked by
the opaque origin a file URL gets.  EasyTrace's geometry core is Clipper2
compiled to WASM, so a real HTTP origin is not optional.

One server per bundle, not one server for all
---------------------------------------------
Each tool gets its **own** ``http://127.0.0.1:<port>``, rooted at its own
directory.  That is forced by how the tools are built, not by taste:

* Kiri:Moto's pages reference server-absolute paths (``/lib/main/kiri.js``,
  ``/moto/theme.css``, ``/font/...``) and its offline loader registers a service
  worker at scope ``/``.  Mounted under a shared ``/kirimoto/`` prefix, every one
  of those 404s.
* EasyTrace's suite landing page likewise links ``/easytrace5000/``.

Giving each bundle its own origin makes "same origin" and "inside this tool"
the same statement, which also makes the navigation guard in ui/pages/cam.py
trivial: same port means in-bundle.

The ``/lib/`` overlay
---------------------
Kiri:Moto is not served straight from disk even upstream.  ``grid-apps/app.js``
does::

    mod.static("/lib/", "alt")
    mod.static("/lib/", "src")

— i.e. ``/lib/`` is two overlaid directories — plus a small rewrite table
mapping the entry points onto their packed bundles.  ``LIB_REWRITES`` and the
``overlays`` mechanism below reproduce exactly that, which is what lets a plain
static server host it with no Node process at runtime.

Nothing here talks to the machine.  G-code produced inside the tools comes back
through the download interception in ``ui/pages/cam.py``.
"""

from __future__ import annotations

import functools
import http.server
import os
import posixpath
import re
import socket
import sys
import threading
import urllib.parse


def _vendor_dir() -> str:
    """Locate vendor/, in-tree and inside a PyInstaller bundle.

    A frozen build unpacks data files under ``sys._MEIPASS`` — a temp dir in
    one-file mode, the app folder in one-folder mode; either way it is the root
    the spec's ``("vendor", "vendor")`` entry lands in.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "vendor")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


VENDOR_DIR = _vendor_dir()

# Kiri:Moto entry points are rewritten onto their packed bundles, mirroring the
# table in grid-apps/app.js. Without these, /kiri/index.html's script tags 404.
LIB_REWRITES = {
    "/lib/main/kiri.js":        "/lib/pack/kiri-main.js",
    "/lib/kiri/run/engine.js":  "/lib/pack/kiri-eng.js",
    "/lib/kiri/run/minion.js":  "/lib/pack/kiri-pool.js",
    "/lib/kiri/run/worker.js":  "/lib/pack/kiri-work.js",
}

#: bundle key -> how to serve it.
#:   root      : directory under vendor/<key>/ that becomes the server root
#:   entry     : path appended to the origin to open the tool
#:   nav_base  : URL prefix the webview may navigate within
#:   overlays  : url-prefix -> directories searched in order, layered over root
#:   rewrites  : exact-path substitutions applied before lookup
#:   port      : preferred loopback port -- see PREFERRED_PORT_NOTE
BUNDLES = {
    # EasyTrace's repo root is a landing page for the whole "EasyCAM5000 Suite"
    # (EasyTrace5000 + EasyShape5000). We deep-link past it into the PCB
    # workspace and pin nav_base there: EasyShape is out of scope, because
    # Kiri:Moto owns all general 2D/3D CAM.
    "easytrace": {
        "root": "",
        "entry": "easytrace5000/",
        "nav_base": "/easytrace5000/",
        "port": 8772,
        "overlays": {},
        "rewrites": {},
    },
    # Kiri:Moto is served from its built web/ tree, with /lib/ overlaid on
    # alt/ then src/ exactly as upstream's own server does.
    #
    # /wasm/ is a second top-level mount: the CAM/slicing kernels are requested
    # as /wasm/kiri-geo.wasm etc. but ship in src/wasm/. Upstream's own service
    # worker lists exactly these prefixes — ["font","mesh","kiri","void","lib",
    # "wasm"] — and all but lib and wasm already exist inside web/.
    "kirimoto": {
        "root": "web",
        "entry": "kiri/",
        "nav_base": "/",
        "port": 8771,
        "overlays": {
            "/lib/": ["alt", "src"],
            "/wasm/": ["src/wasm"],
        },
        "rewrites": LIB_REWRITES,
        # Kiri's toolpath *animation* needs window.SharedArrayBuffer, which the
        # browser only exposes in a cross-origin isolated context. Without these
        # two headers it refuses with "The security context of this window
        # prevents animations". Safe here because every request is same-origin
        # (measured: zero external requests), which is exactly the condition
        # COEP require-corp enforces.
        "headers": {
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Embedder-Policy": "require-corp",
        },
        # The slicing worker is *served* from /lib/kiri/run/worker.js but its
        # content is the packed kiri-work.js, which asks for "../wasm/x.wasm".
        # Relative to the serving URL that becomes /lib/kiri/wasm/x.wasm — a
        # path that exists in no tree, because the rewrite moved the script
        # without moving its base URL. Resolve any unmatched *.wasm by basename
        # out of src/wasm/ rather than hand the worker a 404 HTML page (which
        # surfaces as "expected magic word 00 61 73 6d, found 3c 21 44 4f").
        "wasm_fallback": "src/wasm",
    },
}


#: Same-origin endpoint the injected export buttons call to declare where the
#: G-code should go, *before* triggering the tool's own export.
#:
#: This exists because Qt cancels a QWebEngineDownloadRequest unless accept() is
#: called before the signal handler returns — so the intent cannot be fetched
#: from the page asynchronously once the download has already started. Recording
#: it up front through the server we already run is simpler than standing up a
#: QWebChannel, and being same-origin it needs no extra permissions.
CONTROL_PREFIX = "/__piccola/"

#: Longest job name accepted from the page. Filenames are far shorter than this;
#: the cap only stops an oversized query value from being kept.
_MAX_EXPORT_NAME = 200

#: Rendered-animation cache (``/__piccola/anim-cache/<key>``): the rotary
#: animator's keyframes for a job, so reopening a job already rendered shows
#: every point of it at once. Keyed by a hash of the toolpath the page
#: computes; one file per key under ``cam/anim-cache/``. Bounded: a key is 8-64
#: hex characters, a body at most _ANIM_CACHE_MAX_BYTES, and the folder is
#: trimmed to _ANIM_CACHE_MAX_FILES oldest-first.
_ANIM_CACHE_KEY = re.compile(r"^[0-9a-f]{8,64}$")
_ANIM_CACHE_MAX_BYTES = 1024 * 1024 * 1024
_ANIM_CACHE_MAX_FILES = 24
#: and the folder as a whole: oldest files go first once it passes this
_ANIM_CACHE_MAX_TOTAL = 4 * 1024 * 1024 * 1024


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Static handler with overlay roots, rewrites and traversal containment."""

    # Every type the bundles actually serve, pinned. Chromium refuses to
    # stream-compile a WASM module served as octet-stream, and rejects scripts,
    # module workers and stylesheets whose type is wrong.
    #
    # Pinned rather than left to ``mimetypes`` because on Windows that module
    # reads HKEY_CLASSES_ROOT, where any installed program can have rewritten
    # ".js" to text/plain or ".css" to something else entirely. That is a
    # per-machine setting, so it presents as "Kiri loads here but hangs on my
    # colleague's PC" — see guess_type below.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".wasm": "application/wasm",
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".map": "application/json",
        ".svg": "image/svg+xml",
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
        ".bin": "application/octet-stream",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".txt": "text/plain",
        ".xml": "application/xml",
        ".stl": "model/stl",
    }

    def guess_type(self, path):
        # The stdlib falls back to mimetypes (i.e. the Windows registry) for
        # anything not in extensions_map; stop at our own table instead.
        ext = posixpath.splitext(path)[1].lower()
        return self.extensions_map.get(ext, "application/octet-stream")

    def __init__(self, *args, roots=None, overlays=None, rewrites=None,
                 wasm_fallback=None, control=None, headers=None, **kw):
        self._roots = roots or []
        self._overlays = overlays or {}
        self._rewrites = rewrites or {}
        self._wasm_fallback = wasm_fallback
        self._control = control          # shared dict, read from the Qt thread
        self._extra_headers = headers or {}
        super().__init__(*args, **kw)

    def do_GET(self):  # noqa: N802 (Qt/stdlib naming)
        if self._control is not None and self.path.startswith(CONTROL_PREFIX):
            self._handle_control()
            return
        super().do_GET()

    def do_PUT(self):  # noqa: N802
        if self._control is not None and self.path.startswith(CONTROL_PREFIX):
            self._handle_control()
            return
        self.send_response(405)
        self.end_headers()

    # -- rendered-animation cache -------------------------------------------

    def _anim_cache_dir(self) -> str | None:
        d = self._control.get("anim_cache_dir") if self._control else None
        return d or None

    def _anim_cache_get(self, key: str) -> None:
        d = self._anim_cache_dir()
        path = os.path.join(d, key + ".bin") if d else None
        if not path or not os.path.isfile(path):
            self.send_response(404)
            self.end_headers()
            return
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            # touched: it is the one most recently used
            try:
                os.utime(path, None)
            except OSError:
                pass
        except OSError:
            self.send_response(500)
            self.end_headers()

    def _anim_cache_put(self, key: str) -> None:
        d = self._anim_cache_dir()
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not d or length <= 0 or length > _ANIM_CACHE_MAX_BYTES:
            # drain nothing: a refused body is simply not read, the client
            # sees the status and the connection is closed by Content-Length
            self.send_response(413 if length > _ANIM_CACHE_MAX_BYTES else 400)
            self.end_headers()
            return
        try:
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, key + ".part")
            with open(tmp, "wb") as fh:
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise OSError("short body")
                    fh.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, os.path.join(d, key + ".bin"))
            self._anim_cache_trim(d)
            self.send_response(204)
        except OSError:
            self.send_response(500)
        self.end_headers()

    @staticmethod
    def _anim_cache_trim(d: str) -> None:
        try:
            files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".bin")]
            files.sort(key=lambda p: os.path.getmtime(p))
            drop = files[:-_ANIM_CACHE_MAX_FILES] if len(files) > _ANIM_CACHE_MAX_FILES else []
            keep = [p for p in files if p not in drop]
            total = sum(os.path.getsize(p) for p in keep)
            while keep and total > _ANIM_CACHE_MAX_TOTAL:
                p = keep.pop(0)
                total -= os.path.getsize(p)
                drop.append(p)
            for p in drop:
                os.remove(p)
        except OSError:
            pass

    def _handle_control(self) -> None:
        parts = urllib.parse.urlsplit(self.path)
        action = parts.path[len(CONTROL_PREFIX):].strip("/")
        query = urllib.parse.parse_qs(parts.query)

        if action.startswith("anim-cache/"):
            key = action[len("anim-cache/"):]
            if not _ANIM_CACHE_KEY.match(key):
                self.send_response(400)
                self.end_headers()
                return
            if self.command == "PUT":
                self._anim_cache_put(key)
            else:
                self._anim_cache_get(key)
            return
        if action == "export-intent":
            mode = (query.get("mode") or ["file"])[0]
            # Only ever accept a known value; anything else falls back to the
            # safe option (write to disk) rather than driving the machine.
            self._control["export_intent"] = (
                "machine" if mode == "machine" else "file")
            # The name the user typed for the job, if the tool has such a field.
            # Kept raw here and sanitised where it becomes a path
            # (CamPage._safe_filename) — this is untrusted page input. Bounded
            # only in length, so a runaway value cannot sit in the control dict.
            name = (query.get("name") or [""])[0]
            self._control["export_name"] = name[:_MAX_EXPORT_NAME]
            self.send_response(204)
        else:
            self.send_response(404)
        self.end_headers()

    # -- path resolution --------------------------------------------------

    @staticmethod
    def _contain(base: str, rel: str) -> str | None:
        """Join *rel* under *base*, or None if it would escape.

        We override translate_path, so SimpleHTTPRequestHandler's own traversal
        defence no longer applies and containment is our responsibility.
        """
        target = os.path.normpath(os.path.join(base, rel.lstrip("/")))
        base_abs = os.path.abspath(base)
        try:
            if os.path.commonpath([os.path.abspath(target), base_abs]) != base_abs:
                return None
        except ValueError:      # another drive (Windows): outside by definition
            return None
        return target

    def translate_path(self, path: str) -> str:
        p = urllib.parse.urlsplit(path).path
        p = posixpath.normpath(urllib.parse.unquote(p))
        p = self._rewrites.get(p, p)

        # Overlay prefixes first (e.g. /lib/ -> alt/, src/), first hit wins.
        for prefix, dirs in self._overlays.items():
            if p.startswith(prefix):
                rel = p[len(prefix):]
                for d in dirs:
                    cand = self._contain(d, rel)
                    if cand and os.path.exists(cand):
                        return cand
                # Fall through to the roots rather than 404 here — a path under
                # an overlay prefix may still exist in the main tree.
                break

        for r in self._roots:
            cand = self._contain(r, p)
            if cand and os.path.exists(cand):
                return cand

        # Last resort for WASM (see "wasm_fallback" in BUNDLES): resolve by
        # basename so a rewritten script's relative wasm URL still lands.
        if self._wasm_fallback and p.endswith(".wasm"):
            cand = self._contain(self._wasm_fallback, posixpath.basename(p))
            if cand and os.path.exists(cand):
                return cand

        # Nothing matched: hand back a contained non-existent path so the base
        # class produces a clean 404 instead of leaking a filesystem location.
        return self._contain(self._roots[0], p) or self._roots[0]

    def end_headers(self):
        # No-store so swapping a vendor folder is picked up immediately rather
        # than presenting as a stale-cache mystery.
        self.send_header("Cache-Control", "no-store")
        for name, value in self._extra_headers.items():
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, *_args):
        pass   # the app has its own console page; asset requests are noise


# Why the port is fixed rather than kernel-assigned.
#
# A tool's settings live in localStorage, and localStorage is partitioned per
# *origin* — scheme, host AND port. Binding port 0 gets a different port every
# launch, so every launch is a different origin, so every launch starts with
# empty storage. Everything each tool keeps there — Kiri's tool library, stock
# sizes, operation lists, UI preferences — silently resets. That presents as
# "the app forgets things", which is nobody's first guess at a port number.
#
# Measured before this was fixed: two launches sharing one on-disk profile came
# up as http://127.0.0.1:51464 and http://127.0.0.1:51493, and a counter written
# to localStorage read 1 both times.
#
# The ports sit just above the machine WebSocket server's 8765 and below the
# ephemeral range (49152+ on macOS/BSD), so the kernel will not hand one of them
# out at random to something else.


class _LoopbackServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer that cannot share its port on Windows.

    The stdlib sets SO_REUSEADDR. On Linux/macOS that only lets a bind reuse a
    port in TIME_WAIT. On Windows it means something else entirely: a second
    socket may bind a port another process is *actively listening on*, and the
    bind succeeds. Connections are then split between the two listeners.

    So when anything still holds 8771 — a previous Piccola that has not fully
    exited, a hung instance, another app — the new server binds "successfully",
    the fallback in _bind never runs, and the webview talks to the old process.
    That old process is usually half-dead, and the tab sits on "Kiri:Moto is
    Loading" forever, some launches and not others.

    SO_EXCLUSIVEADDRUSE makes the bind fail loudly instead, which is the case
    _bind already handles.
    """

    if sys.platform == "win32":
        allow_reuse_address = False

        def server_bind(self):
            excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if excl is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
            super().server_bind()


class _BundleServer:
    """One loopback origin serving one CAM bundle."""

    def __init__(self, key: str, spec: dict, vendor: str, control: dict | None = None):
        self.key = key
        base = os.path.join(vendor, key)
        self._entry = spec["entry"]
        self._nav_base = spec["nav_base"]
        root = os.path.join(base, spec["root"]) if spec["root"] else base

        handler = functools.partial(
            _Handler,
            directory=root,
            roots=[root],
            overlays={pre: [os.path.join(base, d) for d in dirs]
                      for pre, dirs in spec["overlays"].items()},
            rewrites=spec["rewrites"],
            wasm_fallback=(os.path.join(base, spec["wasm_fallback"])
                           if spec.get("wasm_fallback") else None),
            control=control,
            headers=spec.get("headers"),
        )
        self._httpd = self._bind(spec.get("port"), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.socket.getsockname()[1]
        threading.Thread(target=self._httpd.serve_forever,
                         name=f"cam-{key}", daemon=True).start()

    @staticmethod
    def _bind(preferred, handler):
        """Bind the preferred port, falling back to a kernel-assigned one.

        The fallback keeps the CAM tab working when something else already holds
        the port -- a second Piccola instance, or an unrelated dev server. That
        instance loses settings persistence (different origin next launch), which
        is worth saying out loud rather than failing to start.
        """
        if preferred:
            try:
                return _LoopbackServer(
                    ("127.0.0.1", preferred), handler)
            except OSError as e:
                print(f"[cam] port {preferred} unavailable ({e.strerror}); "
                      f"falling back to a random port. Settings saved in the "
                      f"CAM tools will not survive a restart.", file=sys.stderr)
        return _LoopbackServer(("127.0.0.1", 0), handler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/{self._entry}"

    @property
    def nav_base(self) -> str:
        return self._nav_base

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()


class CamServer:
    """Owns one :class:`_BundleServer` per vendored CAM bundle.

    Bundles that are not present are simply not served — a missing bundle is a
    degraded CAM tab, not a startup failure.
    """

    def __init__(self, root: str = VENDOR_DIR, anim_cache_dir: str | None = None):
        self._root = root
        os.makedirs(self._root, exist_ok=True)
        self._servers: dict[str, _BundleServer] = {}
        # Written by a server thread, read from the Qt thread. Each assignment
        # is a lone string store, atomic under the GIL, so no lock is needed.
        # The two keys are written back-to-back and read as a pair, but nothing
        # can observe a half-written pair: the page only starts the export once
        # the control request has *completed*, so both stores are done before
        # the first download exists.
        self._control: dict = {"export_intent": "file", "export_name": "",
                               "anim_cache_dir": anim_cache_dir or ""}
        for key, spec in BUNDLES.items():
            if self._present(key, spec):
                self._servers[key] = _BundleServer(key, spec, self._root,
                                                   control=self._control)

    def _present(self, key: str, spec: dict) -> bool:
        base = os.path.join(self._root, key, spec["root"], spec["entry"])
        return os.path.isfile(os.path.join(base, "index.html"))

    def has_bundle(self, bundle: str) -> bool:
        return bundle in self._servers

    def bundle_dir(self, bundle: str) -> str:
        """On-disk directory for a bundle, whether or not it is being served.

        Lets callers read a tool's own metadata — ui/pages/cam.py reads
        Kiri:Moto's version out of it — instead of hard-coding values that would
        silently go stale on the next vendor refresh.
        """
        return os.path.join(self._root, bundle)

    def url_for(self, bundle: str) -> str:
        return self._servers[bundle].url

    def base_path(self, bundle: str) -> str:
        return self._servers[bundle].nav_base

    def port_for(self, bundle: str) -> int:
        return self._servers[bundle].port

    def take_export_request(self) -> tuple[str, str]:
        """Destination and job name for the next export.

        Destination is "machine" — the user asked for it to be sent — or "file".
        Name is what was typed in the tool's filename field, or "" for a tool
        that has none.

        Consumed on read and reset to the safe defaults, so a stale intent from
        an earlier export can never silently drive a later one to the machine,
        and a stale name cannot end up on unrelated files. Both are taken
        together because they describe one export: consuming them separately
        would let a second reader see the reset value.
        """
        intent = self._control.get("export_intent", "file")
        name = self._control.get("export_name", "")
        self._control["export_intent"] = "file"
        self._control["export_name"] = ""
        return intent, name

    def peek_export_intent(self) -> str:
        return self._control.get("export_intent", "file")

    def missing_bundles(self) -> list[str]:
        return [b for b in BUNDLES if b not in self._servers]

    def stop(self) -> None:
        """Idempotent — close may run on window close and again at teardown."""
        for srv in self._servers.values():
            srv.stop()
        self._servers.clear()


def wait_until_ready(port: int, timeout: float = 5.0) -> bool:
    """Block until *port* accepts a connection.

    ThreadingHTTPServer binds and listens in __init__, before its thread starts,
    so this is a guard for the pathological case rather than the normal path.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False
