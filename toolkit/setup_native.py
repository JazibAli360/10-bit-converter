"""Build the arm64 macOS app from this directory.

Usage (inside an arm64 virtual environment):
    python -m pip install -r requirements-native.txt
    python setup_native.py py2app

The resulting app is placed in ``dist/``. Keep server.py and index.html in the
same Resources directory: server.py resolves all bundled web/FFmpeg assets from
its own location, while preferences live in Application Support.
"""

import os

from setuptools import setup
from py2app.recipes import tkinter as py2app_tkinter


# The application has no macOS Tk UI. py2app's generic Tk recipe attempts to
# create a Tk interpreter while packaging and aborts under the Command Line
# Tools Python runtime before filtering the Windows-only fallback import.
# Disable only that packaging recipe; the native PyWebView shell is unchanged.
py2app_tkinter.check = lambda _cmd, _module_graph: None


APP = ["server.py"]
VERSION = os.environ.get("TENBIT_RELEASE_VERSION", "0.1.0")
DATA_FILES = [
    (".", ["index.html", "JZB.png"]),
    ("ui", ["ui/engine-status.js", "ui/api.js", "ui/state.js", "ui/profiles.js", "ui/modals.js",
            "ui/queue.js", "ui/conversion.js", "ui/preview.js", "ui/watch.js", "ui/settings.js", "ui/support.js", "ui/controls.js"]),
    ("bin/arm64", ["bin/arm64/ffmpeg", "bin/arm64/ffprobe"]),
    ("bin/arm64", ["bin/arm64/libplacebo.bundle.zip"]),
]
OPTIONS = {
    "arch": "arm64",
    "argv_emulation": False,
    "iconfile": "JZB.icns",
    # PyWebView imports the Cocoa bridge dynamically. Listing the bridge
    # packages explicitly prevents py2app from replacing objc._objc with a
    # namespace stub at runtime.
    "packages": ["webview", "objc", "engines"],
    # Tk is imported only inside the Windows-only browser fallback. Letting
    # py2app inspect it initializes Apple's deprecated Tk framework during a
    # macOS build, which can abort the build before the app is assembled.
    "excludes": ["tkinter", "_tkinter"],
    "includes": [
        "objc._objc", "AppKit", "Foundation", "Quartz", "Security", "WebKit",
        "PyObjCTools.AppHelper",
    ],
    "plist": {
        "CFBundleName": "10-bit Converter",
        "CFBundleDisplayName": "8-bit → 10-bit Converter",
        "CFBundleIdentifier": "com.jazibali360.tenbitconverter",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
    },
}


setup(
    app=APP,
    name="10-bit Converter",
    py_modules=["export_safety", "media_probe", "conversion_runner", "preflight", "history_store",
                "preview_service", "conversion_service", "watch_service", "native_lifecycle", "single_instance", "update_service"],
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
