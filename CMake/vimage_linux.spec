# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ

block_cipher = None


def find_lib(name, search_paths):
    for path in search_paths:
        candidate = os.path.join(path, name)
        if os.path.exists(candidate):
            return candidate
    return None


lib_paths = [
    "/opt/libjpeg-turbo/lib64",
    "/usr/local/lib64",
    "/usr/lib64",
    "/usr/lib",
]

jpeg_lib = find_lib("libjpeg.so", lib_paths)
turbojpeg_lib = find_lib("libturbojpeg.so.0", lib_paths)

binaries = []

if jpeg_lib:
    binaries.append((jpeg_lib, '.'))
else:
    print("WARNING: libjpeg.so not found")

if turbojpeg_lib:
    binaries.append((turbojpeg_lib, '.'))
else:
    print("WARNING: libturbojpeg.so.0 not found")

a = Analysis(
    scripts=['../scripts/pixelspy.py'],
    pathex=["..", ],
    binaries=binaries,
    datas=[
     ("../vmg/glsl/*.vert", "vmg/glsl"),
     ("../vmg/glsl/*.frag", "vmg/glsl"),
     ("../vmg/glsl/*.geom", "vmg/glsl"),
     ("../vmg/images/*", "vmg/images"),
     ("../vmg/git_hash.txt", "vmg"),
     # ("../vmg/lib/*.so", "vmg/lib"),
    ],
    hiddenimports=[
        "vmg.glsl",
        "vmg.lib",
        "imagecodecs._shared",
        "imagecodecs._shared_cython",
        "imagecodecs._imcd",
        "imagecodecs._jpeg8",
        "OpenGL.platform.egl",
        "OpenGL.platform.glx",
        "OpenGL.platform.baseplatform",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False)

pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pixelspy',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="../vmg/images/cat_eye2.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pixelspy'
)
