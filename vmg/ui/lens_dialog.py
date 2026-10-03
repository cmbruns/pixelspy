from math import radians, degrees

from PySide6 import QtWidgets, QtCore

from vmg.interfaces import TiledImageLike, InputPanoramaFormat
from vmg.ui.ui_lens_parameters import Ui_Dialog


class LensDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_Dialog()
        self.ui.setupUi(self)
        self.image = None
        self.block_signals = False

    def closeEvent(self, event):
        event.ignore()   # prevent destruction
        self.hide()      # just hide the window

    camera_settings_changed = QtCore.Signal()

    def set_image(self, image: TiledImageLike):
        self.block_signals = True
        self.image = image
        self.ui.fov_doubleSpinBox.setValue(degrees(image.md.inscribed_fov_radians))
        self.ui.lensrot_doubleSpinBox.setValue(degrees(image.md.df_lens_rot_radians))
        roll = (image.md.pose_roll_degrees + 180) % 360 - 180
        self.ui.poseRoll_doubleSpinBox.setValue(roll)
        pitch = max(-90, min(90, image.md.pose_pitch_degrees))
        self.ui.posePitch_doubleSpinBox.setValue(pitch)
        heading = image.md.pose_heading_degrees % 360
        self.ui.poseHeading_doubleSpinBox.setValue(heading)
        self.block_signals = False

    @QtCore.Slot(float)
    def on_fov_doubleSpinBox_valueChanged(self, value: float):
        if self.block_signals:
            return
        if self.image is None:
            return
        if self.image.md.input_panorama_format != InputPanoramaFormat.DUAL_FISHEYE:
            return
        if self.image.md.inscribed_fov_radians == radians(value):
            return
        self.image.md.inscribed_fov_radians = radians(value)
        self.camera_settings_changed.emit()

    @QtCore.Slot(float)
    def on_lensrot_doubleSpinBox_valueChanged(self, value: float):
        if self.block_signals:
            return
        if self.image is None:
            return
        if self.image.md.input_panorama_format != InputPanoramaFormat.DUAL_FISHEYE:
            return
        if self.image.md.df_lens_rot_radians == radians(value):
            return
        self.image.md.df_lens_rot_radians = radians(value)
        self.camera_settings_changed.emit()

    @QtCore.Slot(float)
    def on_poseHeading_doubleSpinBox_valueChanged(self, value: float):
        if self.block_signals:
            return
        if self.image is None:
            return
        if self.image.md.input_panorama_format == InputPanoramaFormat.FLAT:
            return
        if self.image.md.pose_heading_degrees == value:
            return
        self.image.md.pose_heading_degrees = value
        self.image.md.update_pcm_rot_geo()
        self.camera_settings_changed.emit()

    @QtCore.Slot(float)
    def on_posePitch_doubleSpinBox_valueChanged(self, value: float):
        if self.block_signals:
            return
        if self.image is None:
            return
        if self.image.md.input_panorama_format == InputPanoramaFormat.FLAT:
            return
        if self.image.md.pose_pitch_degrees == value:
            return
        self.image.md.pose_pitch_degrees = value
        self.image.md.update_pcm_rot_geo()
        self.camera_settings_changed.emit()

    @QtCore.Slot(float)
    def on_poseRoll_doubleSpinBox_valueChanged(self, value: float):
        if self.block_signals:
            return
        if self.image is None:
            return
        if self.image.md.input_panorama_format == InputPanoramaFormat.FLAT:
            return
        if self.image.md.pose_roll_degrees == value:
            return
        self.image.md.pose_roll_degrees = value
        self.image.md.update_pcm_rot_geo()
        self.camera_settings_changed.emit()
