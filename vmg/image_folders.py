import os

from PySide6.QtCore import QSettings, QStandardPaths, QDir


# The exhaustive list of extensions your app scanning logic supports
SUPPORTED_EXTENSIONS = {
    ".bmp", ".dng", ".heic", ".heif", ".gif",
    ".pbm", ".pgm", ".ppm", ".png", ".jpg",
    ".jpeg", ".tif", ".tiff", ".webp"
}

# Unified Open Filter string mapping every single format out-of-the-box
OPEN_IMAGE_FILTERS = (
    "Supported Images (*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp *.gif *.pbm *.pgm *.ppm *.dng *.heic *.heif);;"
    "PNG Images (*.png);;"
    "JPEG Images (*.jpg *.jpeg);;"
    "TIFF Images (*.tif *.tiff);;"
    "HEIC Images (*.heic);;"
    "WEBP Images (*.webp);;"
    "BMP Images (*.bmp);;"
    "PPM Images (*.ppm *.pgm *.pbm);;"
    "GIF Images (*.gif);;"
    "DNG Images (*.dng);;"
    "All files (*)"
)

# Standardized Save Filters matching your exact specification safely
SAVE_IMAGE_FILTERS = (
    "PNG Images (*.png);;"
    "JPEG Images (*.jpg *.jpeg);;"
    "TIFF Images (*.tif *.tiff);;"
    "WEBP Images (*.webp);;"
    "BMP Images (*.bmp);;"
    "PPM Images (*.ppm *.pgm *.pbm);;"
    "GIF Images (*.gif);;"
    "All files (*)"
)


def log_successful_save(file_path: str):
    """Call this ONLY after the image is verified written to disk."""
    if not file_path:
        return
    folder = os.path.dirname(os.path.abspath(file_path))
    QSettings().setValue("latest_save_folder", folder)
    QSettings().setValue("latest_load_folder", folder)


def log_successful_load(file_path: str):
    """Call this when a user opens an image, picks a recent folder, or browses."""
    if not file_path:
        return
    folder = os.path.dirname(os.path.abspath(file_path))
    QSettings().setValue("latest_load_folder", folder)


def get_save_folder(provenance_path: str = "") -> str:
    """
    Default folder path for vimage image QFileDialog.getSaveFileName

    provenance_path: file path to parent image of this image
    """
    if provenance_path:
        ap = os.path.abspath(provenance_path)
        p: str = os.path.dirname(ap)
        if os.path.exists(ap) and os.path.exists(p) and os.access(p, os.W_OK):
            return p

    for key in ["latest_save_folder", "latest_load_folder"]:
        p: str = QSettings().value(key)
        if os.path.exists(p) and os.access(p, os.W_OK):
            return p

    for std_path in [
        QStandardPaths.StandardLocation.PicturesLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.HomeLocation,
    ]:
        p = QStandardPaths.writableLocation(std_path)
        if os.path.exists(p) and os.access(p, os.W_OK):
            return p

    return QDir.tempPath()


def get_load_folder() -> str:
    for key in ["latest_load_folder", "latest_save_folder"]:
        p: str = QSettings().value(key)
        if os.path.exists(p) and os.access(p, os.R_OK):
            return p

    for std_paths in [
        QStandardPaths.StandardLocation.PicturesLocation,
        QStandardPaths.StandardLocation.DocumentsLocation,
        QStandardPaths.StandardLocation.HomeLocation,
    ]:
        for p in QStandardPaths.standardLocations(std_paths):
            if os.path.exists(p) and os.access(p, os.R_OK):
                return p

    return QDir.tempPath()
