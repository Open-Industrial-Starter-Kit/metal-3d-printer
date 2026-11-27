"""
ui/cam_links.py
Points EasyTrace's footer links at real web pages, so every one of them opens in
the user's browser.

Why they need help
------------------
The status bar's right-hand strip — Version, Home, AGPLv3, Doc, Help, GitHub,
Support Development — is the only outbound signposting the tool has, and inside
this webview almost none of it works:

* Four of the seven carry ``target="_blank"``. A new-window request goes to
  ``QWebEnginePage.createWindow()``, and the default implementation returns
  nothing, so QtWebEngine drops the navigation: AGPLv3, Doc, Help and GitHub are
  simply dead clicks.
* **Home** points at ``/``, the suite landing page. The PCB tab deep-links past
  that page on purpose, and ``ToolPage.acceptNavigationRequest`` refuses
  same-origin paths outside the bundle's nav base, so it is dead too.
* **Doc** points at ``doc/`` *inside* the bundle. Were the ``_blank`` not
  swallowing it, that would be worse than dead — it would replace the CAM tool
  with a docs site inside a webview that has no back button.
* **Support Development** is not a link at all in practice: ``#support`` opens
  an in-app modal, which is a dialog inside a machine control app rather than
  the page the user expected.

The fix
-------
Every link is rewritten to an absolute ``https`` URL and its ``target`` removed,
which turns each click into an ordinary main-frame navigation to a foreign
origin — exactly the case ``ToolPage`` already hands to
``QDesktopServices.openUrl``. So the click opens the system browser and the CAM
tab does not move.

Attribution is the point of most of these, and EasyTrace is AGPL-3.0: its NOTICE
asks that the software be credited, and the licence link is part of that. Making
them work is the opposite of hiding them — the CAM header credits the project on
every visit, and these now reach the real source, licence and docs.

The destinations come from the project's own metadata (``sitemap.xml``,
``CITATION.cff``) rather than being guessed, and the Support link goes to the
Ko-fi page its modal offers.
"""

from __future__ import annotations

#: Project home, from CITATION.cff `url` / sitemap.xml.
SITE = "https://cam.eltryus.design"
REPO = "https://github.com/RicardoJCMarques/EasyTrace5000"

#: Literal `href` attribute in the footer -> where it should really go.
#: Keyed on the attribute rather than the link text, which carries a version
#: number and is translated.
#:
#: Absolute links (Version, Help, GitHub) are not listed: their URLs are already
#: right and only need `target` stripped.
REWRITE = {
    # The suite landing page the PCB tab deep-links past.
    "/": f"{SITE}/",
    # Shipped beside the bundle; on the web it is the repository's copy.
    "../LICENSE": f"{REPO}/blob/main/LICENSE",
    # In-bundle docs -> the same pages online (sitemap.xml lists this URL).
    "doc/": f"{SITE}/easytrace5000/doc/",
    # The in-app support modal's own call to action.
    "#support": "https://ko-fi.com/eltryus",
}

#: Where the links live.
_CONTAINER = "#status-right"


def footer_links_js() -> str:
    """JS rewriting the footer links to open in the system browser."""
    pairs = ",".join(f"{_q(k)}:{_q(v)}" for k, v in REWRITE.items())
    return f"""
(function () {{
  var REWRITE = {{{pairs}}};
  var CONTAINER = {_q(_CONTAINER)};

  function fix() {{
    var host = document.querySelector(CONTAINER);
    if (!host) return false;
    var links = host.querySelectorAll('a');
    if (!links.length) return false;

    Array.prototype.forEach.call(links, function (a) {{
      var href = a.getAttribute('href');
      if (href && REWRITE.hasOwnProperty(href)) a.setAttribute('href', REWRITE[href]);
      // Without this the click asks for a new window, which QtWebEngine drops.
      // As a same-tab navigation to another origin it reaches ToolPage, which
      // hands it to the system browser and leaves the tab where it is.
      a.removeAttribute('target');
    }});
    return true;
  }}

  // Static markup, but it arrives after this script runs.
  var tries = 400;
  (function wait() {{
    if (fix()) return;
    if (tries-- > 0) setTimeout(wait, 50);
  }})();
}})();
"""


def _q(s: str) -> str:
    """Minimal JS string literal — these are all internal constants."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
