# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.building.build_main import Analysis, BUNDLE, COLLECT, EXE, PYZ

a = Analysis(
    scripts=['../scripts/pixelspy.py'],
    pathex=['../'],
    binaries=[],
    datas=[
        ("../vmg/glsl/*.vert", "vmg/glsl"),
        ("../vmg/glsl/*.frag", "vmg/glsl"),
        ("../vmg/glsl/*.geom", "vmg/glsl"),
        ("../vmg/images/*", "vmg/images"),
        ("../vmg/git_hash.txt", "vmg"),
        # ("../vmg/lib/*.dylib", "vmg/lib"),
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
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

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
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="../vmg/images/vimage2.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PixelSpy',
)
app = BUNDLE(
    coll,
    name="PixelSpy.app",
    icon="../CMake/vimage2.icns",
    bundle_identifier="com.rotatingpenguin.pixelspy.app",
    info_plist={
    	"CFBundleDisplayName": "PixelSpy",
	    "CFBundleExecutable": "PixelSpy",
	    "CFBundleIdentifier": "com.rotatingpenguin.pixelspy.app",
	    "CFBundleInfoDictionaryVersion": "6.0",
	    "CFBundleName": "PixelSpy",
	    "CFBundlePackageType": "APPL",
	    "CFBundleShortVersionString": "0.0.0",
	    "NSHighResolutionCapable": True,
        'CFBundleDocumentTypes': [
            # GIF
            {
                'CFBundleTypeName': 'GIF Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['com.compuserve.gif'],
                'LSHandlerRank': 'Alternate'
            },

            # JPEG (.jpg, .jpeg)
            {
                'CFBundleTypeName': 'JPEG Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.jpeg'],
                'LSHandlerRank': 'Alternate'
            },

            # PNG
            {
                'CFBundleTypeName': 'PNG Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.png'],
                'LSHandlerRank': 'Alternate'
            },

            # BMP
            {
                'CFBundleTypeName': 'Bitmap Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['com.microsoft.bmp'],
                'LSHandlerRank': 'Alternate'
            },

            # DNG
            {
                'CFBundleTypeName': 'Digital Negative Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['com.adobe.raw-image'],
                'LSHandlerRank': 'Alternate'
            },

            # HEIC / HEIF
            {
                'CFBundleTypeName': 'High Efficiency Image File Format',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.heic', 'public.heif'],
                'LSHandlerRank': 'Alternate'
            },

            # Minecraft map .DAT files
            {
                'CFBundleTypeName': 'Minecraft Map Info',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['com.rotatingpenguin.pixelspy.minecraft-map'],
                'LSHandlerRank': 'Alternate'
            },

            # PBM
            {
                'CFBundleTypeName': 'Portable Bitmap',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.pbm'],
                'LSHandlerRank': 'Alternate'
            },

            # PGM
            {
                'CFBundleTypeName': 'Portable Graymap',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.pgm'],
                'LSHandlerRank': 'Alternate'
            },

            # PPM
            {
                'CFBundleTypeName': 'Portable Pixmap',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.ppm'],
                'LSHandlerRank': 'Alternate'
            },

            # TIFF (.tif, .tiff)
            {
                'CFBundleTypeName': 'TIFF Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['public.tiff'],
                'LSHandlerRank': 'Alternate'
            },

            # WebP
            {
                'CFBundleTypeName': 'WebP Image',
                'CFBundleTypeRole': 'Viewer',
                'LSItemContentTypes': ['org.webmproject.webp'],
                'LSHandlerRank': 'Alternate'
            },
        ]
    }
)
