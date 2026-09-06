import struct

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QAction, QColor, QGuiApplication
import numpy as np


class CopyPixelAction(QAction):
    def __init__(self, parent=None):
        super().__init__("Copy This Pixel Color", parent=parent)


def copy_pixel_value(raw: np.ndarray):
    """
    raw: np.ndarray of shape (channels) containing one pixel.
         dtype may be uint8, uint16, float32, float64, etc.
    """

    mime = QMimeData()

    # -----------------------------------------
    # 1. Universal RGB8 / RGBA8 (text formats)
    # -----------------------------------------
    channels = raw.shape[0]

    # Normalize to 0–255
    # If dtype is integer: scale by dtype max
    # If dtype is float: assume 0–1 range
    if np.issubdtype(raw.dtype, np.integer):
        max_val = np.iinfo(raw.dtype).max
        rgb8 = np.clip((raw.astype(np.float64) / max_val) * 255, 0, 255).astype(np.uint8)
    else:
        # float: assume 0–1 range
        rgb8 = np.clip(raw * 255.0, 0, 255).astype(np.uint8)

    rgb8 = rgb8.tolist()

    # Grayscale fix:
    # 1 channel → replicate into R,G,B
    # 2 channels → treat as Gray + Alpha
    if len(rgb8) == 1:
        rgb8 = [rgb8[0], rgb8[0], rgb8[0]]
    elif len(rgb8) == 2:
        rgb8 = [rgb8[0], rgb8[0], rgb8[0], rgb8[1]]

    # 3 channels → leave as RGB
    # 4+ channels → use first 4 (RGB + A)
    if len(rgb8) > 4:
        rgb8 = rgb8[:4]

    # Hex formats
    if channels >= 4:
        hex8 = f"#{rgb8[0]:02x}{rgb8[1]:02x}{rgb8[2]:02x}{rgb8[3]:02x}"
    else:
        hex8 = f"#{rgb8[0]:02x}{rgb8[1]:02x}{rgb8[2]:02x}"

    mime.setText(hex8)
    mime.setData("text/plain", hex8.encode("utf-8"))

    # CSS rgba
    if channels >= 4:
        css = f"rgba({rgb8[0]}, {rgb8[1]}, {rgb8[2]}, {rgb8[3]/255:.3f})"
    else:
        css = f"rgb({rgb8[0]}, {rgb8[1]}, {rgb8[2]})"

    mime.setData("text/css", css.encode("utf-8"))

    # Qt QColor (8‑bit only)
    if channels >= 4:
        mime.setColorData(QColor(rgb8[0], rgb8[1], rgb8[2], rgb8[3]))
    else:
        mime.setColorData(QColor(rgb8[0], rgb8[1], rgb8[2]))

    # -----------------------------------------
    # 2. Full‑fidelity raw pixel data (custom)
    # -----------------------------------------
    dtype_name = str(raw.dtype)  # noqa
    channel_count = channels
    raw_bytes = raw.tobytes()

    # Header: [channel_count (uint32)] [dtype_name (null‑terminated)]
    header = struct.pack(">I", channel_count) + dtype_name.encode("utf-8") + b"\0"
    payload = header + raw_bytes

    mime.setData("application/x-vimage-rawpixel", payload)

    # -----------------------------------------
    # Send to clipboard
    # -----------------------------------------
    QGuiApplication.clipboard().setMimeData(mime)
