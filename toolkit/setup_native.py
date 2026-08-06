"""Build the arm64 macOS app from this directory.

Usage (inside an arm64 virtual environment):
    python -m pip install -r requirements-native.txt
    python setup_native.py py2app

The resulting app is placed in ``dist/``. Keep server.py and index.html in the
same Resources directory: server.py resolves all bundled web/FFmpeg assets from
its own location, while preferences live in Application Support.
"""

from setuptools import setup


APP = ["server.py"]
DATA_FILES = [
    (".", ["index.html", "JZB.png"]),
    ("ui", ["ui/engine-status.js", "ui/api.js", "ui/state.js", "ui/profiles.js", "ui/modals.js",
            "ui/queue.js", "ui/conversion.js", "ui/preview.js", "ui/watch.js", "ui/settings.js", "ui/controls.js"]),
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
    "includes": [
        "objc._objc", "AppKit", "Foundation", "Quartz", "Security", "WebKit",
        "PyObjCTools.AppHelper",
    ],
    "plist": {
        "CFBundleName": "10-bit Converter",
        "CFBundleDisplayName": "8-bit → 10-bit Converter",
        "CFBundleIdentifier": "com.jazibali360.tenbitconverter",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
    },
}


setup(
    app=APP,
    name="10-bit Converter",
    py_modules=["export_safety", "media_probe", "conversion_runner", "preflight", "history_store",
                "preview_service", "conversion_service", "watch_service", "native_lifecycle", "single_instance"],
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
