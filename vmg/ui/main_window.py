from typing import Optional

import ctypes
import locale
from OpenGL import GL
from datetime import datetime
from inspect import cleandoc
import inspect
import io
import logging
import os
import pathlib
from pathlib import Path
import time

import PIL
from PIL import Image, ImageGrab
from pillow_heif import register_heif_opener  # Support apple .HEIF images
import pillow_minecraft_map
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtGui import QUndoStack, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox

from vmg.ui.circular_combo_box import CircularComboBox
from vmg.command import CropToSelection
from vmg.image_folders import SAVE_IMAGE_FILTERS, SUPPORTED_EXTENSIONS, get_save_folder, log_successful_save, \
    log_successful_load, OPEN_IMAGE_FILTERS, get_load_folder
from vmg.image_loader import ImageLoader
from vmg.interfaces import TiledImageLike, InputFormat
from vmg.ui.demosaic_dialog import DemosaicDialog
from vmg.ui.lens_dialog import LensDialog
from vmg.log import LogDialog
from vmg.natural_sort import natural_sort_key
from vmg.pixel_filter import PixelFilter, PixelNumerals
from vmg.progress import ProgressStatus, ProgressState
from vmg.display_projection import DisplayProjection
from vmg.recent_file import RecentFileList
from vmg.ui.ui_vimage import Ui_MainWindow
from vmg.version import __version__
from vmg.git_hash import vimage_git_hash
from vmg.resources import resource_filename


logger = logging.getLogger(__name__)

# Make PIL load larger images
_max_image_pixels = 1789569700
if Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS < _max_image_pixels:
    Image.MAX_IMAGE_PIXELS = _max_image_pixels
# Make PIL load apple .heic images
register_heif_opener()

# Convenience alias
QueuedConnection = Qt.ConnectionType.QueuedConnection


class ScopedWaitCursor(object):
    def __init__(self):
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)  # noqa

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        QtWidgets.QApplication.restoreOverrideCursor()


class VimageMainWindow(Ui_MainWindow, QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setupUi(self)
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)  # noqa
        self.image_list = []
        self.image_index = 0
        self.image: Optional[TiledImageLike] = None
        self.imageWidgetGL.request_message.connect(self.statusbar.showMessage)
        self.imageWidgetGL.signal_360.connect(self.set_is_360)
        self.imageWidgetGL.image_size_changed.connect(self.set_image_size)
        # Configure actions
        self.recent_files = RecentFileList(
            open_file_slot=self.load_main_image,
            settings_key="recent_files",
            menu=self.menuOpen_Recent,
        )
        self.imageWidgetGL.input_format_changed.connect(self.set_input_format)
        sel_rect = self.imageWidgetGL.view_state.sel_rect
        sel_rect.selection_shown.connect(self.enableCrop_to_Selection)
        self.actionNext.setIcon(QtGui.QIcon(resource_filename("vmg.images", "next_icon.png")))
        self.toolBar.widgetForAction(self.actionPrevious).setAutoRepeat(True)
        self.toolBar.widgetForAction(self.actionNext).setAutoRepeat(True)
        self.actionOpen.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.actionOpen.setIcon(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton))
        self.actionPrevious.setIcon(QtGui.QIcon(resource_filename("vmg.images", "previous_icon.png")))
        self.actionExit.setShortcut(QtGui.QKeySequence.StandardKey.Quit)
        self.actionSave_As.setIcon(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton))
        self.actionSave_As.setShortcut(QtGui.QKeySequence.StandardKey.SaveAs)
        sel_rect.selection_shown.connect(self.actionSelect_None.setEnabled)
        self.actionSelect_None.triggered.connect(sel_rect.clear)
        rect_icon_file = resource_filename("vmg.images", "box_icon.png")
        self.actionSelect_Rectangle.setIcon(QtGui.QIcon(rect_icon_file))
        self.actionSelect_Rectangle.triggered.connect(self.imageWidgetGL.start_rect_with_no_point)
        # Zoom In
        self.actionZoom_In.setIcon(QtGui.QIcon(
            resource_filename(package="vmg.images", resource_name="zoom_in.png")))
        self.toolBar.widgetForAction(self.actionZoom_In).setAutoRepeat(True)
        self.actionZoom_In.setShortcuts([
            QKeySequence(QKeySequence.StandardKey.ZoomIn),
            QKeySequence("Ctrl+="),  # "+" but without the shift key
        ])
        # Zoom Out
        self.actionZoom_Out.setIcon(QtGui.QIcon(
            resource_filename(package="vmg.images", resource_name="zoom_out.png")))
        self.toolBar.widgetForAction(self.actionZoom_Out).setAutoRepeat(True)
        self.actionZoom_Out.setShortcuts([
            QKeySequence(QKeySequence.StandardKey.ZoomOut),
        ])
        # Allow action shortcuts even when toolbar and menu bar are hidden
        self.addAction(self.actionNext)
        self.addAction(self.actionPrevious)
        self.addAction(self.actionFull_Screen)
        self.addAction(self.actionSharp)
        # Make projections mutually exclusive
        self.projection_group = QtGui.QActionGroup(self)
        self.projectionComboBox = CircularComboBox()
        for proj in (
                self.actionPerspective,
                self.actionStereographic,
                self.actionEquidistant,
                self.actionEquirectangular,
        ):
            self.projection_group.addAction(proj)
            self.projectionComboBox.addItem(proj.text(), proj)
            if proj.isChecked():
                self.projectionComboBox.setCurrentText(proj.text())
        self.projectionComboBox.setEnabled(False)
        self.projectionComboBox.currentIndexChanged.connect(self.projection_combo_box_current_index_changed)  # noqa
        #
        self.input_format_group = QtGui.QActionGroup(self)
        for proj in (
            self.actionPerspectiveInput,
            self.actionEquirectangularInput,
            self.actionSinusoidalInput,
            self.actionDual_FisheyeInput,
        ):
            self.input_format_group.addAction(proj)
        self.wrap_group = QtGui.QActionGroup(self)
        for wrap in (
            self.actionClamp_To_Edge,
            self.actionClamp_To_Border,
            self.actionMirrored_Repeat,
            self.actionRepeat,
            self.actionMirror_Clamp_To_Edge,
        ):
            self.wrap_group.addAction(wrap)
        self.numerals_group = QtGui.QActionGroup(self)
        for num in (
            self.actionHexadecimal,
            self.actionDecimal,
            self.actionNone,
        ):
            self.numerals_group.addAction(num)
        if PixelNumerals.DEFAULT == PixelNumerals.DECIMAL:
            self.actionDecimal.setChecked(True)
        elif PixelNumerals.DEFAULT == PixelNumerals.HEXADECIMAL:
            self.actionHexadecimal.setChecked(True)
        else:
            self.actionNone.setChecked(True)
        # Add image list progress label to toolbar
        self.list_label = QtWidgets.QLabel("0/0")
        self.list_label.setMinimumWidth(40)
        separators = [a for a in self.toolBar.actions() if a.isSeparator()]
        self.toolBar.insertWidget(separators[1], self.list_label)
        self.toolBar.addSeparator()
        self.toolBar.addAction(self.actionSelect_Rectangle)
        self.toolBar.addSeparator()
        self.toolBar.addWidget(self.projectionComboBox)
        self.toolBar.toggleViewAction().setEnabled(False)  # I did not like accidentally hiding it
        # Add progress bar to status bar
        self.progress_status = ProgressStatus(self)
        self.statusbar.addPermanentWidget(self.progress_status, stretch=0)
        # Add image dimensions to status bar
        self.size_label = QtWidgets.QLabel("0x0")
        self.size_label.setMinimumWidth(60)
        self.size_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight
            | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.statusbar.addPermanentWidget(self.size_label, stretch=0)
        # Clipboard actions
        self.clipboard = QtGui.QGuiApplication.clipboard()
        self.clipboard.dataChanged.connect(self.process_clipboard_change)  # noqa
        self.actionCopy.setEnabled(False)
        self.actionCopy.setShortcut(QtGui.QKeySequence.Copy)
        self.actionPaste.setEnabled(self.clipboard.image().width() > 0)
        self.actionPaste.setShortcut(QtGui.QKeySequence.Paste)
        self.clipboard.dataChanged.connect(self.process_clipboard_change)  # noqa
        # Undo actions
        self.undo_stack = QUndoStack()  # TODO: per-image undo stack
        self.undo_stack.cleanChanged.connect(self.undo_stack_clean_changed)  # noqa
        self.undo_stack.setActive()
        self.action_undo = self.undo_stack.createUndoAction(self)
        self.action_undo.setShortcut(QtGui.QKeySequence.Undo)
        self.action_redo = self.undo_stack.createRedoAction(self)
        self.action_redo.setShortcut(QtGui.QKeySequence.Redo)
        top_action = self.menuEdit.actions()[0]
        self.menuEdit.insertAction(top_action, self.action_undo)
        self.menuEdit.insertAction(top_action, self.action_redo)
        self.menuEdit.insertSeparator(top_action)
        # File loading thread
        self._current_file_name = None
        self.loading_thread = QtCore.QThread()
        self.image_loader = ImageLoader()
        self.image_loader.moveToThread(self.loading_thread)
        self.loading_thread.start()
        self.image_load_requested.connect(self.image_loader.load_from_file_name, QueuedConnection)
        self.pil_load_requested.connect(self.image_loader.load_from_pil_image, QueuedConnection)
        logger.debug(f"Connecting texture_created signal")
        self.image_loader.texture_created.connect(self.image_texture_created, QueuedConnection)
        self.image_loader.load_failed.connect(self.image_load_failed, QueuedConnection)
        self.image_loader.image_displayed.connect(self.image_displayed, QueuedConnection)
        #
        self.imageWidgetGL.load_failed.connect(self.image_load_failed, QueuedConnection)
        self.imageWidgetGL.context_created.connect(self.image_loader.on_context_created, QueuedConnection)
        # self.imageWidgetGL.image_displayed.connect(self.image_displayed, QueuedConnection)
        self.image_loader.image_displayed.connect(self.image_displayed, QueuedConnection)
        # progress tracking
        self.image_loader.progress_changed.connect(self.progress_status.set_value, QueuedConnection)
        self.imageWidgetGL.progress_changed.connect(self.progress_status.set_value, QueuedConnection)
        self.progress_status.view_log_requested.connect(self.on_actionView_Log_triggered)
        self.progress_status.open_image_requested.connect(self.on_actionOpen_triggered)
        # Three stages of cancel signaling
        self.progress_status.cancel_load_requested.connect(self.cancel_image_load)  # noqa
        self.cancel_load_requested.connect(self.image_loader.cancel_load, QueuedConnection)  # noqa
        #
        # Logging
        self.log_window = LogDialog(self)
        self.lens_dialog = None  # Instantiate just in time
        self.demosaic_dialog = None

    def activate_indexed_image(self):
        try:
            self.load_image_from_file(self.image_list[self.image_index])
        except PIL.UnidentifiedImageError as uie:
            self.statusbar.showMessage(str(uie), 5000)
        self.update_previous_next()

    def cancel_image_load(self):
        if self._current_file_name is None:
            return
        logger.info(f"Canceling image load.")
        self.cancel_load_requested.emit(self._current_file_name)  # noqa
        self._current_file_name = None
        self.progress_status.set_state(ProgressState.LOAD_CANCELLED)
        if QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()

    cancel_load_requested = QtCore.Signal(str)

    def _dialog_and_save_image(self, pil_image: Image.Image, default_name: str = "") -> str:
        file_path, _file_filter = QFileDialog.getSaveFileName(
            self,
            "Save Image to File",
            default_name,
            filter=SAVE_IMAGE_FILTERS,
            selectedFilter="PNG Files (*.png)",
        )
        if len(file_path) < 1:
            return ""
        try:
            self.save_image(file_path, pil_image)
            return file_path
        except ValueError as value_error:  # File without extension
            QtWidgets.QMessageBox.warning(
                self,
                "Error saving image with that name",
                f"Error: {str(value_error)}",
            )
        except OSError as os_error:  # cannot write mode RGBA as JPEG
            QtWidgets.QMessageBox.warning(
                self,
                "Error saving image",
                f"Error: {str(os_error)}",
            )
        except Exception as exception:
            QtWidgets.QMessageBox.warning(
                self,
                "Error saving image",
                f"Error: {str(exception)}",
            )
        return ""

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            for url in mime_data.urls():
                file = url.toLocalFile()
                if Path(file).is_file():
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        mime_data = event.mimeData()
        files = []
        if mime_data.hasUrls():
            for url in mime_data.urls():
                file = url.toLocalFile()
                if Path(file).is_file():
                    files.append(file)
        if len(files) < 1:
            return
        elif len(files) == 1:
            self.load_main_image(files[0])
        else:
            self.set_image_list(files, 0)
        event.acceptProposedAction()
        self.activateWindow()  # Take focus immediately after successful drop

    def enableCrop_to_Selection(self):
        enable = False
        if self.image is not None:
            if self.image.pil_image is not None:
                enable = True
        self.actionCrop_to_Selection.setEnabled(enable)

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.loading_thread.quit()
        self.loading_thread.wait()

    image_load_requested = QtCore.Signal(str)

    def load_image_from_memory(self, image: PIL.Image.Image, name: str) -> None:
        if QtWidgets.QApplication.overrideCursor() is None:
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        fn = str(name)
        self._current_file_name = fn
        self.progress_status.reset()
        self.pil_load_requested.emit(image, fn)  # noqa

    pil_load_requested = QtCore.Signal(Image.Image, str)

    def load_image_from_file(self, file_name: str) -> None:
        stem = pathlib.Path(file_name).stem
        self.progress_status.reset()
        logger.info(f"Loading image from file {file_name} ...")
        self.statusbar.showMessage(f"Loading {stem}...", 5000)
        if QtWidgets.QApplication.overrideCursor() is None:
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        fn = str(file_name)
        self._current_file_name = fn
        self.undo_stack.clear()
        self.image_load_requested.emit(fn)  # noqa

    @QtCore.Slot(TiledImageLike)  # noqa
    def image_texture_created(self, image: TiledImageLike):
        logger.info(f"Received image texture {image.md.file_name}")
        if image.md.file_name != self._current_file_name:
            logger.info(f"ignoring stale texture loaded for {image.md.file_name}")
            return
        self.image = image
        self.imageWidgetGL.set_image(image)
        fn = image.md.file_name
        assert isinstance(fn, str)
        self.set_current_image_path(fn)
        self.actionSave_As.setEnabled(True)
        self.actionSave_Current_View_As.setEnabled(True)
        self.actionCopy.setEnabled(True)
        self.actionReset_View.setEnabled(True)
        self.actionSelect_Rectangle.setEnabled(image.md.input_format == InputFormat.STANDARD_PHOTO)
        self.actionSelect_None.trigger()
        self.actionZoom_In.setEnabled(True)
        self.actionZoom_Out.setEnabled(True)
        self._check_lens_dialog()
        self.lens_dialog.set_image(image)

    @QtCore.Slot(str)  # noqa
    def image_load_failed(self, file_name: str):
        if file_name != self._current_file_name:
            logger.info(f"Loading stale image {file_name} failed")
            return
        logger.error(f"Loading image {file_name} failed")
        self.progress_status.set_state(ProgressState.LOAD_FAILED)
        if QtWidgets.QApplication.overrideCursor() is not None:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._current_file_name = None

    def load_main_image(self, file_name: str):
        logger.info(f"Loading image {file_name}")
        path = Path(file_name)
        name = path.name
        paths_list = [path, ]
        folder = path.parent.absolute()
        for file in folder.glob("*.*"):
            if file.name == name:
                continue  # Skip the triggering file
            # Accept all suffixes for supported image types
            if file.suffix.lower() in SUPPORTED_EXTENSIONS:
                paths_list.append(file)
        self.set_image_list(paths_list, 0)

    @QtCore.Slot()  # noqa
    def process_clipboard_change(self):
        has_image = self.clipboard.image().width() > 0
        self.actionPaste.setEnabled(has_image)

    def save_image(self, file_path: str, pil_image: Image.Image):
        # TODO: cancellable separate thread save? (at least after in-memory copy is made)
        with ScopedWaitCursor():
            self.statusbar.showMessage(f"Saving image {file_path}...", 5000)
            QtCore.QCoreApplication.processEvents()  # Make sure the message is shown
            # Avoid creating corrupt file by first writing to memory to check for writing errors
            in_memory_image = io.BytesIO()
            in_memory_image.name = file_path
            try:
                pil_image.save(in_memory_image, quality=95)
            except OSError:
                rgb_image = pil_image.convert("RGB")  # TODO: choose bg color or warn or whatever
                rgb_image.save(in_memory_image, quality=95)
            with open(file_path, "wb") as out:
                logging.info(f"Saving image {file_path}")
                in_memory_image.seek(0)
                out.write(in_memory_image.read())
                log_successful_save(file_path)
            if pil_image is self.image.pil_image:
                self.set_current_image_path(file_path)
                self.load_main_image(file_path)
            self.statusbar.showMessage(f"Saved image {file_path}", 5000)

    def set_current_image_path(self, path: str):
        self.setWindowFilePath(path)
        if os.path.exists(path):
            self.recent_files.add_file(path)
            log_successful_load(path)

    def set_image_list(self, image_list: list, current_index: int):
        if len(image_list) < 1:
            return
        current_item = image_list[current_index]
        self.image_list = sorted(image_list, key=natural_sort_key)
        self.image_index = None
        for index, item in enumerate(self.image_list):
            if item is current_item:
                self.image_index = index
        assert self.image_index is not None
        self.load_image_from_file(self.image_list[self.image_index])
        self.update_previous_next()

    def set_input_format(self, input_format: InputFormat):
        if input_format == InputFormat.STANDARD_PHOTO:
            self.actionPerspectiveInput.setChecked(True)
        elif input_format == InputFormat.DUAL_FISHEYE:
            self.actionDual_FisheyeInput.setChecked(True)
        elif input_format == InputFormat.EQUIRECTANGULAR:
            self.actionEquirectangularInput.setChecked(True)
        elif input_format == InputFormat.SINUSOIDAL:
            self.actionSinusoidalInput.setChecked(True)
        else:
            raise Exception("Unexpected InputProjection")

    def set_display_projection(self, projection: DisplayProjection, action: QtGui.QAction) -> None:
        if self.imageWidgetGL.view_state.display_projection == projection:
            return
        logger.info(f"Changing 360 projection to {projection.name}")
        self.imageWidgetGL.view_state.display_projection = projection
        if self.projectionComboBox.currentText() != action.text():
            self.projectionComboBox.setCurrentText(action.text())
        self.imageWidgetGL.update()

    @QtCore.Slot(int, int)  # noqa
    def set_image_size(self, w, h) -> None:
        self.size_label.setText(f"{w}x{h}")

    @QtCore.Slot(bool)  # noqa
    def set_is_360(self, is_360: bool) -> None:
        self.projectionComboBox.setEnabled(is_360)
        self.menu360_Projection.setEnabled(is_360)
        self.actionSelect_Rectangle.setEnabled(not is_360)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        now = datetime.now().astimezone()
        logger.info(f"vimage main window shown at {now.strftime('%H:%M:%S.%f %Z on %x')}")
        region_locale = locale.getdefaultlocale()[0]
        logger.info(f"region locale is {region_locale}")
        gui_locale = os.environ.get("LANG", None)  # Mac and Linux
        if gui_locale is None:
            try:
                kernel32 = ctypes.windll.kernel32  # Windows
                locale_id = kernel32.GetUserDefaultUILanguage()
                gui_locale = locale.windows_locale[locale_id]
            except AttributeError:
                logger.warning("Unable to identify app language locale")
        if gui_locale is not None:
            logger.info(f"app language locale is {gui_locale}")
        self.imageWidgetGL.create_offscreen_context()

    def update_previous_next(self):
        # Update progress label
        total = len(self.image_list)
        current = self.image_index + 1
        self.list_label.setText(f"{current}/{total}")
        #
        if len(self.image_list) < 2:
            for action in (self.actionPrevious, self.actionNext):
                action.setEnabled(False)
                action.setToolTip(action.text())
        else:
            next_index = (self.image_index + 1) % total
            prev_index = (self.image_index - 1 + total) % total
            assert next_index >= 0
            assert next_index < total
            assert prev_index >= 0
            assert prev_index < total
            self.actionNext.setToolTip(f"{self.actionNext.text()}: {Path(self.image_list[next_index]).name}")
            self.actionPrevious.setToolTip(f"{self.actionPrevious.text()}: {Path(self.image_list[prev_index]).name}")
            self.actionNext.setEnabled(True)
            self.actionPrevious.setEnabled(True)

    @QtCore.Slot(str)  # noqa
    def file_open_event(self, file_: str):
        self.load_main_image(file_)

    # TODO: nuanced QImage format for clipboard
    _qimage_format_for_pil_mode = {
        "RGBA": QtGui.QImage.Format.Format_RGBA8888,
    }

    @QtCore.Slot()  # noqa
    def on_actionAbout_triggered(self):  # noqa
        abb_hash = vimage_git_hash
        abb_hash = abb_hash.replace("-dirty", "")
        abb_hash = abb_hash.replace("-broken", "")
        if "unknown" not in abb_hash:  # Make hyperlink
            abb_hash = f"<a href=https://github.com/cmbruns/vimage/tree/{abb_hash}>{vimage_git_hash}</a>"
        else:
            abb_hash = vimage_git_hash
        msg = inspect.cleandoc(f"""
            <H2>Vimage Image Viewer</H2>
            <p>version {__version__}</p>
            <p>git hash: {abb_hash}</p>
            <p><a href='https://github.com/cmbruns/vimage/issues'>Report an issue</a></p>
            <p><a href='https://github.com/cmbruns/vimage'>Source code</a></p>
            <p><b>Maintainer</b>: Christopher Bruns</p>
        """)
        QMessageBox.information(self, "About", msg)

    @QtCore.Slot(bool)  # noqa
    def on_actionAnisotropic_Filtering_toggled(self, is_checked: bool):  # noqa
        vs = self.imageWidgetGL.view_state
        if is_checked == vs.anisotropic_filtering:
            return
        vs.anisotropic_filtering = is_checked
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionBrightnessMinus_triggered(self):
        self.imageWidgetGL.view_state.brightness -= 0.25
        self.imageWidgetGL.update()
        self.statusbar.showMessage(f"EV = {self.imageWidgetGL.view_state.brightness}")

    @QtCore.Slot()  # noqa
    def on_actionBrightnessPlus_triggered(self):
        self.imageWidgetGL.view_state.brightness += 0.25
        self.imageWidgetGL.update()
        self.statusbar.showMessage(f"EV = {self.imageWidgetGL.view_state.brightness}")

    @QtCore.Slot(bool)  # noqa
    def on_actionClamp_To_Border_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        vs = self.imageWidgetGL.view_state
        val = GL.GL_CLAMP_TO_BORDER
        if vs.texture_wrap == val:
            return
        vs.texture_wrap = val
        self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionClamp_To_Edge_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        vs = self.imageWidgetGL.view_state
        val = GL.GL_CLAMP_TO_EDGE
        if vs.texture_wrap == val:
            return
        vs.texture_wrap = val
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionCopy_triggered(self):  # noqa
        # TODO - create a separate container for images...
        # Copy selection only, if available
        sel_rect = self.imageWidgetGL.view_state.sel_rect
        if sel_rect.is_active:
            img = self.image.pil_image.crop(sel_rect.left_top_right_bottom)
        else:
            img = self.image.pil_image
        temp = img.convert("RGBA")
        qimage = QtGui.QImage(
            temp.tobytes("raw", "RGBA"),
            temp.size[0],
            temp.size[1],
            QtGui.QImage.Format.Format_RGBA8888,
        )
        self.clipboard.setImage(qimage)

    def _check_lens_dialog(self):
        if self.lens_dialog is None:
            self.lens_dialog = LensDialog(self)
            glw = self.imageWidgetGL
            self.lens_dialog.camera_settings_changed.connect(glw.update)

    @QtCore.Slot()  # noqa
    def on_actionConfigure_Dual_Fisheye_triggered(self):  # noqa
        self._check_lens_dialog()
        self.lens_dialog.show()
        self.lens_dialog.raise_()
        self.lens_dialog.activateWindow()

    @QtCore.Slot()  # noqa
    def on_actionCrop_to_Selection_triggered(self):  # noqa
        # TODO: virtual crop, metadata only
        # TODO: what about existing image list?
        if self.image.pil_image is None:
            return
        self.undo_stack.push(CropToSelection(
            self.image,
            self.imageWidgetGL.view_state.sel_rect,
            self,
            self.image_list[self.image_index],
        ))

    @QtCore.Slot(bool)  # noqa
    def on_actionDecimal_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        wgl = self.imageWidgetGL
        if wgl.view_state.pixel_numerals == PixelNumerals.DECIMAL:
            return
        wgl.view_state.pixel_numerals = PixelNumerals.DECIMAL
        wgl.update()

    @QtCore.Slot()
    def on_actionDemosaic_triggered(self):
        if self.demosaic_dialog is None:
            self.demosaic_dialog = DemosaicDialog(self)
            self.demosaic_dialog.show_cfa_colors_toggled.connect(self.imageWidgetGL.on_show_cfa_colors_toggled)
            self.demosaic_dialog.demosaic_method_changed.connect(self.imageWidgetGL.on_demosaic_method_changed)
        self.demosaic_dialog.show()

    @QtCore.Slot(bool)  # noqa
    def on_actionDual_FisheyeInput_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            if self.imageWidgetGL.set_input_format(InputFormat.DUAL_FISHEYE):
                self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionEquidistant_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            self.set_display_projection(DisplayProjection.EQUIDISTANT, self.actionEquidistant)

    @QtCore.Slot(bool)  # noqa
    def on_actionEquirectangularInput_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            if self.imageWidgetGL.set_input_format(InputFormat.EQUIRECTANGULAR):
                self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionEquirectangular_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            self.set_display_projection(DisplayProjection.EQUIRECTANGULAR, self.actionEquirectangular)

    @QtCore.Slot()  # noqa
    def on_actionExit_triggered(self):  # noqa
        QtWidgets.QApplication.quit()

    @QtCore.Slot(bool)  # noqa
    def on_actionFull_Screen_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            self.menubar.hide()
            self.toolBar.hide()
            self.statusbar.hide()
            self.showFullScreen()
        else:
            self.menubar.show()
            self.toolBar.show()
            self.statusbar.show()
            self.showNormal()

    @QtCore.Slot(bool)  # noqa
    def on_actionHexadecimal_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        wgl = self.imageWidgetGL
        if wgl.view_state.pixel_numerals == PixelNumerals.HEXADECIMAL:
            return
        wgl.view_state.pixel_numerals = PixelNumerals.HEXADECIMAL
        wgl.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionMirror_Clamp_To_Edge_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        vs = self.imageWidgetGL.view_state
        val = GL.GL_MIRROR_CLAMP_TO_EDGE
        if vs.texture_wrap == val:
            return
        vs.texture_wrap = val
        self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionMirrored_Repeat_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        vs = self.imageWidgetGL.view_state
        val = GL.GL_MIRRORED_REPEAT
        if vs.texture_wrap == val:
            return
        vs.texture_wrap = val
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionNext_triggered(self):  # noqa
        if not self.actionNext.isEnabled():
            return
        if len(self.image_list) < 2:
            self.actionNext.setEnabled(False)
            return
        if self.image_index >= len(self.image_list) - 1:
            self.actionNext.setEnabled(False)  # prevent further next actions until dialog is done
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Continue from first image?")
            box.setText(cleandoc("""
               This is the final image.
               Do you want to continue from the first image?
            """))
            box.setStandardButtons(
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            button_yes = box.button(QMessageBox.StandardButton.Yes)
            button_yes.setText("Continue")
            reply = box.exec()
            if reply != QMessageBox.StandardButton.Yes:
                self.actionNext.setEnabled(True)
                return
        self.image_index += 1
        if self.image_index >= len(self.image_list):
            self.image_index -= len(self.image_list)
        self.activate_indexed_image()

    @QtCore.Slot(bool)  # noqa
    def on_actionNone_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        wgl = self.imageWidgetGL
        if wgl.view_state.pixel_numerals == PixelNumerals.NONE:
            return
        wgl.view_state.pixel_numerals = PixelNumerals.NONE
        wgl.update()

    @QtCore.Slot()  # noqa
    def on_actionOpen_triggered(self):  # noqa
        default_folder = get_load_folder()
        file_name, filter_used = QFileDialog.getOpenFileName(
            parent=self,
            caption="Choose an image",
            dir=default_folder,
            filter=OPEN_IMAGE_FILTERS,
            selectedFilter="All files (*)",
        )
        if len(file_name) < 1:
            return
        self.load_main_image(file_name)

    @QtCore.Slot()  # noqa
    def on_actionPaste_triggered(self):  # noqa
        clip = ImageGrab.grabclipboard()
        try:
            file_name = clip[0]
            self.load_image_from_file(file_name)
            return
        except ... as exc:
            print(exc)
        if clip.width < 1:
            self.actionPaste.setEnabled(False)
            return
        file_name = clip.filename
        if file_name is None:
            time_str = time.strftime("%Y%m%d_%H%M%S")
            file_name = f"Clipboard{time_str}"
        self.image_list[:] = [file_name,]
        self.image_index = 0
        self.undo_stack.clear()
        self.undo_stack.resetClean()  # clipboard image has not been saved
        self._current_file_name = file_name
        self.load_image_from_memory(image=clip, name=file_name)

    @QtCore.Slot(bool)  # noqa
    def on_actionPerspective_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            self.set_display_projection(DisplayProjection.GNOMONIC, self.actionPerspective)

    @QtCore.Slot(bool)  # noqa
    def on_actionPerspectiveInput_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            if self.imageWidgetGL.set_input_format(InputFormat.STANDARD_PHOTO):
                self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionPrevious_triggered(self):  # noqa
        if not self.actionPrevious.isEnabled():
            return
        if len(self.image_list) < 2:
            self.actionPrevious.setEnabled(False)
            return
        if self.image_index <= 0:
            self.actionPrevious.setEnabled(False)
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Continue from final image?")
            box.setText(cleandoc("""
               This is the first image.
               Do you want to continue from the final image?
            """))
            box.setStandardButtons(
                QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Yes)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            button_yes = box.button(QMessageBox.StandardButton.Yes)
            button_yes.setText("Continue")
            reply = box.exec()
            if reply != QMessageBox.StandardButton.Yes:
                self.actionPrevious.setEnabled(True)
                return
        self.image_index -= 1
        if self.image_index < 0:
            self.image_index += len(self.image_list)
        self.activate_indexed_image()

    @QtCore.Slot(bool)  # noqa
    def on_actionRepeat_toggled(self, is_checked: bool):  # noqa
        if not is_checked:
            return
        vs = self.imageWidgetGL.view_state
        val = GL.GL_REPEAT
        if vs.texture_wrap == val:
            return
        vs.texture_wrap = val
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionReset_View_triggered(self):  # noqa
        self.imageWidgetGL.view_state.reset()
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionSave_As_triggered(self):  # noqa
        default_name = f"{pathlib.Path(self._current_file_name).stem}"
        default_folder = get_save_folder(self._current_file_name)
        file_path = self._dialog_and_save_image(
            self.image.pil_image,
            default_name=f"{default_folder}/{default_name}",
        )
        if os.path.exists(file_path):
            self.set_current_image_path(file_path)
            self.undo_stack.setClean()

    @QtCore.Slot()  # noqa
    def on_actionSave_Current_View_As_triggered(self):  # noqa
        pixmap = self.imageWidgetGL.grab()
        view_image = Image.fromqpixmap(pixmap)
        default_name = f"{pathlib.Path(self._current_file_name).stem}_view"
        default_folder = get_save_folder(self._current_file_name)
        file_path = self._dialog_and_save_image(
            view_image,
            default_name=f"{default_folder}/{default_name}",
        )
        self.set_current_image_path(file_path)

    @QtCore.Slot(bool)  # noqa
    def on_actionSharp_toggled(self, is_checked: bool):  # noqa
        vs = self.imageWidgetGL.view_state
        if is_checked and vs.pixel_filter == PixelFilter.SHARP:
            return
        if not is_checked and vs.pixel_filter == PixelFilter.CATMULL_ROM:
            return
        if is_checked:
            vs.pixel_filter = PixelFilter.SHARP
        else:
            vs.pixel_filter = PixelFilter.CATMULL_ROM
        self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionShow_Center_Guides_toggled(self, is_checked: bool):  # noqa
        vs = self.imageWidgetGL.view_state
        if is_checked == vs.show_center_guides:
            return
        vs.show_center_guides = is_checked
        self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionSinusoidalInput_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            if self.imageWidgetGL.set_input_format(InputFormat.SINUSOIDAL):
                self.imageWidgetGL.update()

    @QtCore.Slot(bool)  # noqa
    def on_actionStereographic_toggled(self, is_checked: bool):  # noqa
        if is_checked:
            self.set_display_projection(DisplayProjection.STEREOGRAPHIC, self.actionStereographic)

    @QtCore.Slot(bool)  # noqa
    def on_actionTile_Boundaries_toggled(self, is_checked: bool):  # noqa
        vs = self.imageWidgetGL.view_state
        if is_checked == vs.show_tile_boundaries:
            return
        vs.show_tile_boundaries = is_checked
        self.imageWidgetGL.update()

    @QtCore.Slot()  # noqa
    def on_actionView_Log_triggered(self):  # noqa
        self.log_window.show()

    @QtCore.Slot()  # noqa
    def on_actionZoom_In_triggered(self):
        wgl = self.imageWidgetGL
        wgl.view_state.zoom_relative(1.10, None)
        wgl.update()

    @QtCore.Slot()  # noqa
    def on_actionZoom_Out_triggered(self):
        wgl = self.imageWidgetGL
        wgl.view_state.zoom_relative(1.0/1.10, None)
        wgl.update()

    @QtCore.Slot(TiledImageLike)  # noqa
    def image_displayed(self, image: TiledImageLike):
        if image.md.file_name == self._current_file_name:
            logger.debug("Image displayed")
            stem = pathlib.Path(self._current_file_name).stem
            self.progress_status.set_value(percent_complete=100)
            self.statusbar.showMessage(f"Loaded {stem}", 5000)
            QtWidgets.QApplication.restoreOverrideCursor()

    @QtCore.Slot(int)  # noqa
    def projection_combo_box_current_index_changed(self, index: int):
        projection_action = self.projectionComboBox.itemData(index)
        projection_action.trigger()

    @QtCore.Slot(bool)  # noqa
    def undo_stack_clean_changed(self, clean: bool):
        self.setWindowModified(not clean)
