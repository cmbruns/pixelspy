#!/bin/sh
set -e

update-mime-database /usr/share/mime

# Create symlink if missing
if [ ! -e @CMAKE_INSTALL_PREFIX@/bin/PixelSpy ]; then
    ln -s @CMAKE_INSTALL_PREFIX@/PixelSpy/PixelSpy @CMAKE_INSTALL_PREFIX@/bin/PixelSpy
fi
