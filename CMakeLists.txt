cmake_minimum_required(VERSION 3.12)  # Requires 3.12+ for C++20 support
project(PixelSpy LANGUAGES CXX)  # CXX because of binary helpers

option(BUILD_BINARY_UTILS "Build binary utilities" OFF)
option(BUILD_INSTALLER "BuildInstaller" ON)

if(UNIX)
  set(LINUX_PACKAGE_FORMAT "DEB" CACHE STRING "Linux package format: DEB or RPM")
  set_property(CACHE LINUX_PACKAGE_FORMAT PROPERTY STRINGS DEB RPM)
endif()

# TODO: binary utils should be a whole separate cmake, distinct from pyinstaller
if (BUILD_BINARY_UTILS)
add_subdirectory(csrc)  # compiled helpers
endif()

if (BUILD_INSTALLER)

# Remember to manually update version here every build
# Path to the version file inside your source tree
set(PIXELSPY_VERSION_FILE "${CMAKE_SOURCE_DIR}/vmg/version.py")

# Read the file
file(READ "${PIXELSPY_VERSION_FILE}" PIXELSPY_VERSION_PY)

# Extract the version string from __version__ = "x.y.z"
string(REGEX MATCH "__version__ *= *\"([^\"]+)\"" _ "${PIXELSPY_VERSION_PY}")
set(PIXELSPY_VERSION "${CMAKE_MATCH_1}")

message(STATUS "PixelSpy version: ${PIXELSPY_VERSION}")

# Split into major/minor/patch
string(REGEX MATCH "([0-9]+)\\.([0-9]+)\\.([0-9]+)" _ "${PIXELSPY_VERSION}")
set(PIXELSPY_VERSION_MAJOR "${CMAKE_MATCH_1}")
set(PIXELSPY_VERSION_MINOR "${CMAKE_MATCH_2}")
set(PIXELSPY_VERSION_PATCH "${CMAKE_MATCH_3}")

message(STATUS "PixelSpy version components: "
    "${PIXELSPY_VERSION_MAJOR}.${PIXELSPY_VERSION_MINOR}.${PIXELSPY_VERSION_PATCH}")

# Feed into CPACK
set(CPACK_PACKAGE_VERSION_MAJOR "${PIXELSPY_VERSION_MAJOR}")
set(CPACK_PACKAGE_VERSION_MINOR "${PIXELSPY_VERSION_MINOR}")
set(CPACK_PACKAGE_VERSION_PATCH "${PIXELSPY_VERSION_PATCH}")

mark_as_advanced(
    CMAKE_BACKWARDS_COMPATIBILITY
    CMAKE_CONFIGURATION_TYPES
    EXECUTABLE_OUTPUT_PATH
    LIBRARY_OUTPUT_PATH
)

# Compute git hash to identify exact code version
find_package(Git REQUIRED)
if(GIT_FOUND)
    # First refresh the cache
    execute_process(COMMAND ${GIT_EXECUTABLE} -C ${CMAKE_SOURCE_DIR} diff --quiet HEAD)
    # Then get the hash
    execute_process(COMMAND ${GIT_EXECUTABLE}
        -C ${CMAKE_SOURCE_DIR} describe --always --dirty --broken
        OUTPUT_VARIABLE GIT_HASH
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    message(STATUS "PixelSpy git hash = ${GIT_HASH}")
    # Put one copy in the scripts folder for dev use
    # This file will be git ignored, to avoid endless loop
    configure_file(
        "${CMAKE_SOURCE_DIR}/CMake/git_hash.txt.in"
        "${CMAKE_SOURCE_DIR}/vmg/git_hash.txt"
        @ONLY
        NEWLINE_STYLE UNIX
    )
endif()

find_program(UIC NAMES PySide6-uic pyside6-uic
  HINTS
    ${CMAKE_SOURCE_DIR}/.venv/bin/
    ${CMAKE_SOURCE_DIR}/venv/bin/
    ${CMAKE_SOURCE_DIR}/.venv/Scripts/
    ${CMAKE_SOURCE_DIR}/venv/Scripts/
    /Library/Frameworks/Python.framework/Versions/3.14/bin/
    /usr/local/bin/
)

find_program(PYINSTALLER pyinstaller
    HINTS
      ${CMAKE_SOURCE_DIR}/.venv/bin/
      ${CMAKE_SOURCE_DIR}/venv/bin/
      ${CMAKE_SOURCE_DIR}/.venv/Scripts/
      ${CMAKE_SOURCE_DIR}/venv/Scripts/
      /Library/Frameworks/Python.framework/Versions/3.14/bin/
      /usr/local/bin/
)
if (WIN32)
  set(SPEC_FILE vimage_win32.spec)
elseif(APPLE)
  set(SPEC_FILE vimage_macos.spec)
elseif(UNIX)
  set(SPEC_FILE vimage_linux.spec)
endif()
add_custom_target(RunPyInstaller ALL
    COMMAND ${CMAKE_COMMAND} -E remove_directory "${CMAKE_BINARY_DIR}/dist"
    COMMAND ${CMAKE_COMMAND} -E remove_directory "${CMAKE_BINARY_DIR}/build"
    COMMAND ${PYINSTALLER} ${CMAKE_SOURCE_DIR}/CMake/${SPEC_FILE}
    DEPENDS "${CMAKE_SOURCE_DIR}/CMake/${SPEC_FILE}"
    WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
    COMMENT "Running PyInstaller"
)

if (WIN32)
  install(DIRECTORY "${CMAKE_BINARY_DIR}/dist/PixelSpy/"
      DESTINATION "."
  )
  set_property(INSTALL "PixelSpy"
      PROPERTY CPACK_START_MENU_SHORTCUTS "PixelSpy"
  )
elseif(APPLE)
  install(DIRECTORY "${CMAKE_BINARY_DIR}/dist/PixelSpy.app"
      DESTINATION "."
      USE_SOURCE_PERMISSIONS
  )
elseif(UNIX)
  install(DIRECTORY "${CMAKE_BINARY_DIR}/dist/pixelspy"
      DESTINATION "libexec"
  )
  # 1. Install your actual program target normally
  install(
     PROGRAMS "${CMAKE_BINARY_DIR}/dist/pixelspy/pixelspy"
     DESTINATION "libexec/pixelspy"
  )
  # 2. Define the symlink target path (relative to where the symlink will live)
  # From 'bin/', you must go up one folder to reach 'libexec/'
  set(SYMLINK_TARGET "../libexec/pixelspy/pixelspy")
  set(SYMLINK_NAME "pixelspy")
  # 3. Create the symlink physically inside the build directory at configure time
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E create_symlink
    "${SYMLINK_TARGET}"
    "${CMAKE_CURRENT_BINARY_DIR}/${SYMLINK_NAME}"
  )
  # 4. Use install(FILES) to package the symlink.
  # CPack RPM will see that this file is a symlink and package it properly!
  install(
    FILES "${CMAKE_CURRENT_BINARY_DIR}/${SYMLINK_NAME}"
    DESTINATION bin
  )
  configure_file(${CMAKE_SOURCE_DIR}/CMake/resources/debian/postinst.in.sh
   ${CMAKE_BINARY_DIR}/postinst
   @ONLY
  )
  configure_file(${CMAKE_SOURCE_DIR}/CMake/resources/debian/postrm.in.sh
   ${CMAKE_BINARY_DIR}/postrm
   @ONLY
  )
  install(FILES ${CMAKE_SOURCE_DIR/CMake/resources/debian/mimetypes-pixelspy.xml}
    DESTINATION "share/mime/packages"
  )
  install(FILES ${CMAKE_SOURCE_DIR}/CMake/resources/debian/pixelspy.desktop
        DESTINATION "share/applications"
  )
  install(FILES ${CMAKE_SOURCE_DIR}/CMake/resources/debian/64x64/pixelspy.png
        DESTINATION "share/icons/hicolor/64x64/apps"
  )
  install(FILES ${CMAKE_SOURCE_DIR}/CMake/resources/debian/256x256/pixelspy.png
        DESTINATION "share/icons/hicolor/256x256/apps"
  )
  endif()

set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_SOURCE_DIR}/LICENSE.txt")
set(CPACK_PACKAGE_EXECUTABLES "PixelSpy;PixelSpy")
set(CPACK_PACKAGE_CONTACT "zeo1@rotatingpenguin.com")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "PixelSpy desktop image file viewer")
set(CPACK_PACKAGE_DESCRIPTION "PixelSpy desktop image file viewer")

if (WIN32)
  set(CPACK_GENERATOR WIX)
  set(CPACK_WIX_UPGRADE_GUID F1CA83FC-4F1B-42A3-B033-4D597CAD1A37)
  set(CPACK_WIX_UI_REF WixUI_FeatureTree)  # To show selectable feature tree
  # set(CPACK_WIX_UI_REF WixUI_InstallDir)  # No Feature selection for now
  set(CPACK_WIX_PRODUCT_ICON "${CMAKE_SOURCE_DIR}/vmg/images/vimage2.ico")
  SET(CPACK_WIX_EXTENSIONS WixUtilExtension)
  set(CPACK_WIX_PATCH_FILE "${CMAKE_SOURCE_DIR}/CMake/wix_patch.xml")
elseif(APPLE)
  set(CPACK_GENERATOR DragNDrop)
  set(CPACK_DMG_DS_STORE "${CMAKE_SOURCE_DIR}/CMake/DS_Store")
elseif(UNIX)
    if(LINUX_PACKAGE_FORMAT STREQUAL "DEB")
        set(CPACK_GENERATOR DEB)
        set(CPACK_DEBIAN_PACKAGE_MAINTAINER "Christopher Bruns")
        set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
        set(CPACK_DEBIAN_PACKAGE_PRIORITY "optional")
        set(CPACK_DEBIAN_PACKAGE_DEPENDS "")
        set(CPACK_DEBIAN_PACKAGE_RELEASE 1)
        set(CPACK_SET_DESTDIR "ON")
        # Set file associations
        set(CPACK_DEBIAN_PACKAGE_CONTROL_EXTRA
            "${CMAKE_BINARY_DIR}/postinst"
            "${CMAKE_BINARY_DIR}/postrm"
        )
    elseif(LINUX_PACKAGE_FORMAT STREQUAL "RPM")
        set(CPACK_GENERATOR "RPM")
        set(CPACK_RPM_PACKAGE_LICENSE "MIT")
        set(CPACK_RPM_PACKAGE_GROUP "Applications/Graphics")
        set(CPACK_RPM_PACKAGE_RELEASE 1)
        set(CPACK_RPM_PACKAGE_SUMMARY "PixelSpy viewer")
        set(CPACK_RPM_PACKAGE_DESCRIPTION "PixelSpy desktop image viewer")
        set(CPACK_RPM_PACKAGE_REQUIRES "")
        # Avoid troublesome automatic dependency detection
        set(CPACK_RPM_PACKAGE_AUTOREQPROV "no")
    endif()
endif()

include(CPack)

endif(BUILD_INSTALLER)
