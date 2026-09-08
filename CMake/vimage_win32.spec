# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ

block_cipher = None

a = Analysis(
    scripts=['../scripts/pixelspy.py'],
    pathex=["..", ],
    binaries=[
     ('C:/libjpeg-turbo64/bin/jpeg62.dll', '.'),
     ('C:/libjpeg-turbo64/bin/turbojpeg.dll', '.'),
    ],
    datas=[
     ("../vmg/glsl/*.vert", "vmg/glsl"),
     ("../vmg/glsl/*.frag", "vmg/glsl"),
     ("../vmg/glsl/*.geom", "vmg/glsl"),
     ("../vmg/images/*", "vmg/images"),
     ("../vmg/git_hash.txt", "vmg"),
     ("../vmg/lib/*.dll", "vmg/lib"),
    ],
    hiddenimports=[
        "vmg.glsl",
        "vmg.lib",
        "imagecodecs._shared",
        "imagecodecs._shared_cython",
        "imagecodecs._imcd",
        "imagecodecs._jpeg8",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PixelSpy',
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
    name='PixelSpy'
)
