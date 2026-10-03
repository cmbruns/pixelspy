"""
file pixelspy.py

Launches the PixelSpy application.
This Python script is the primary entry point for pyinstaller-based packages.
"""

# TODO: use time to help measure startup latency
import time
earliest_timestamp = time.process_time_ns()


# Hack to make it work with pyinstaller
try:
    from OpenGL.platform import win32  # required
except AttributeError:
    pass

try:
    from OpenGL.arrays import (
        numpymodule,  # required
        ctypesarrays,  # required
        strings,  # required
    )
except AttributeError:
    pass

from vmg import VimageApp

VimageApp()
