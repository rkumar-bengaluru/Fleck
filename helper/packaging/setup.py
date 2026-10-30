import sys

from setuptools import setup

sys.setrecursionlimit(10000)  # modulegraph recurses deeply on big packages

import zlib

# uv python builds zlib in; py2app 0.28 copies zlib.__file__ unconditionally. Any existing file satisfies it.
zlib.__file__ = getattr(zlib, "__file__", sys.executable)

setup(
    app=["../fleck_helper/probe.py"],
    name="Fleck Helper",
    options={"py2app": {
        "plist": "Info.plist",
        "packages": ["rumps"],
        "includes": ["fleck_helper.probe", "AVFoundation", "ScreenCaptureKit", "CoreMedia", "UserNotifications"],
        "argv_emulation": False,
        "dist_dir": "../../dist",
        "bdist_base": "../../build/py2app",
    }},
)
