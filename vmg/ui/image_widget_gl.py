import logging
from typing import cast, Optional

import numpy
from numpy.typing import NDArray
from OpenGL import GL
from PySide6 import QtCore, QtGui, QtOpenGLWidgets, QtWidgets
from PySide6.QtCore import QEvent, Qt, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QAction
from PySide6.QtWidgets import QGestureEvent, QSwipeGesture, QPinchGesture

from vmg.interfaces import TiledImageLike, InputFormat, PhotometricScale, DemosaicMethod, RenderStateLike
from vmg.offscreen_context import OffscreenContext
from vmg.selection_box import (CursorHolder)
from vmg.state import ViewState
from vmg.shader import IImageShader, SphericalShader, RectangularTileShader, SphericalDngShader, RectangularDngShader

logger = logging.getLogger(__name__)
Float = float  # suppress PyCharm's "helpful" "| int" suggestions everyfuckingwhere


class ImageWidgetGL(QtOpenGLWidgets.QOpenGLWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.grabGesture(Qt.GestureType.PinchGesture)
        # self.grabGesture(Qt.PanGesture)
        self.grabGesture(Qt.GestureType.SwipeGesture)
        self.image: Optional[TiledImageLike] = None
        self.setMinimumSize(10, 10)
        self.vao = None
        self.sphere_shader = SphericalShader()
        self.rect_tile_shader = RectangularTileShader()
        self.sphere_dng_shader = SphericalDngShader()
        self.rect_dng_shader = RectangularDngShader()
        self.program: IImageShader = self.rect_tile_shader
        self.view_state: RenderStateLike = ViewState(window_size=self.size())
        self.view_state.vss.cursor_changed.connect(self.change_cursor)
        self.view_state.vss.request_message.connect(self.request_message)
        self.view_state.sel_rect.selection_shown.connect(self.update)
        self.raw_rot_ont2 = numpy.eye(2, dtype=numpy.float32)  # For flatty images
        self.raw_rot_ont3 = numpy.eye(3, dtype=numpy.float32)  # For spherical panos
        self.offscreen_context_is_ready = False
        self._has_size = False

    @QtCore.Slot(CursorHolder)
    def change_cursor(self, cursor_holder: CursorHolder):
        if cursor_holder.cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor_holder.cursor)

    context_created = QtCore.Signal(OffscreenContext)

    def event(self, event: QEvent):
        # if event.type() == QEvent.Type.Gesture:
        if isinstance(event, QGestureEvent):
            pinch = event.gesture(Qt.GestureType.PinchGesture)
            swipe = event.gesture(Qt.GestureType.SwipeGesture)
            if isinstance(swipe, QSwipeGesture):
                print(swipe)  # noqa
            elif isinstance(pinch, QPinchGesture):
                zoom = pinch.scaleFactor()
                self.view_state.zoom_relative(zoom, None)
                self.update()
                return True

        return super().event(event)

    image_size_changed = QtCore.Signal(int, int)

    def initializeGL(self) -> None:
        if not self._has_size:
            return
        logger.debug("Starting initializeGL()...")
        # Use native-like background color
        bg_color = cast(
            tuple[Float, Float, Float, Float],
            self.palette().color(self.backgroundRole()).getRgbF())
        GL.glClearColor(*bg_color)
        self.vao = GL.glGenVertexArrays(1)  # noqa
        GL.glBindVertexArray(self.vao)
        self.rect_tile_shader.initialize_gl()
        self.rect_dng_shader.initialize_gl()
        self.sphere_shader.initialize_gl()
        self.sphere_dng_shader.initialize_gl()

    input_format_changed = QtCore.Signal(InputFormat)

    def keyPressEvent(self, event):
        self.view_state.key_press_event(event)

    def keyReleaseEvent(self, event):
        self.view_state.key_release_event(event)

    load_failed = QtCore.Signal(str)

    def mouseMoveEvent(self, event):
        if event.pos() is None:
            return
        if self.image is None:
            return
        if event.source() != Qt.MouseEventSource.MouseEventNotSynthesized:
            return
        if self.view_state.mouse_move_event(event):
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.customContextMenuRequested.emit(event.pos())
            return
        else:
            self.view_state.mouse_press_event(event)

    def mouseReleaseEvent(self, event):
        self.view_state.mouse_release_event(event)

    def create_offscreen_context(self):
        display_ctx = self.context()
        offscreen_context = OffscreenContext(self, display_ctx, self.format())
        offscreen_context.init_gl()
        main_window = self.window()
        if main_window is not None and hasattr(main_window, "loading_thread"):
            offscreen_context.context.moveToThread(main_window.loading_thread)
        logger.debug("Created shared offscreen OpenGL context")
        self.context_created.emit(offscreen_context)  # noqa

    @QtCore.Slot(DemosaicMethod)
    def on_demosaic_method_changed(self, demosaic_method: DemosaicMethod):
        if self.view_state.demosaic_method == demosaic_method:
            return
        self.view_state.demosaic_method = demosaic_method
        try:
            self.makeCurrent()
            if self.image.update_demosaic_method_gl(demosaic_method):
                self.update()
        finally:
            self.doneCurrent()

    @QtCore.Slot(bool)
    def on_show_cfa_colors_toggled(self, is_on: bool):
        if self.view_state.show_cfa_colors == is_on:
            return
        self.view_state.show_cfa_colors = is_on
        self.update()

    def paint_guide_lines(self, painter:QPainter):
        # --- now draw Qt overlay ---
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        dash_length = 10
        pattern = [dash_length, dash_length]

        w = self.width()
        h = self.height()
        w2 = w // 2
        h2 = h // 2

        for co in [((255, 64, 64), 0), ((128, 0, 0), dash_length)]:
            color, offset = co
            pen = QPen(QColor(*color, 64))  # Transparent black
            pen.setWidth(3)
            pen.setDashPattern(pattern)
            pen.setDashOffset(offset)
            painter.setPen(pen)
            # horizontal center line
            cg = 15  # half the size of the center gap
            painter.drawLine(0, h2, w2 - cg, h2)
            painter.drawLine(w2 + cg, h2, w, h2)
            # vertical center line
            painter.drawLine(w2, 0, w2, h2 - cg)
            painter.drawLine(w2, h2 + cg, w2, h)

        painter.end()

    def paintGL(self) -> None:
        self._internal_paint_gl()

    def _internal_paint_gl(self):
        try:
            if self.vao is None:
                self.initializeGL()
            logger.debug("Starting paintGL()")
            painter = QPainter(self)
            painter.beginNativePainting()
            # TODO: Should this vary by image PhotometricScale? need tests
            # Make transparent images transparent
            # Framebuffer is premultiplied alpha
            # but textures are straight alpha
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFuncSeparate(
                GL.GL_SRC_ALPHA,  # simulate premultiplied alpha on srcRGB
                GL.GL_ONE_MINUS_SRC_ALPHA,  # blend dstRGB
                GL.GL_ONE,  # combine srcAlpha as-is
                GL.GL_ONE_MINUS_SRC_ALPHA  # blend dstAlpha
            )
            self.view_state.background_color = self.palette().color(self.backgroundRole()).getRgbF()
            GL.glClearColor(*self.view_state.background_color)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            if self.image is None:
                logger.debug("image_data is None")
                return
            GL.glBindVertexArray(self.vao)
            self.program.paint_gl(self.view_state, self.image)
            painter.endNativePainting()
            if self.view_state.show_center_guides:
                self.paint_guide_lines(painter)
            logger.debug("Finished paintGL()")
        except BaseException:
            raise  # sufficient to get traceback to log, via except_hook

    progress_changed = QtCore.Signal(int)

    request_message = QtCore.Signal(str, int)

    def resizeGL(self, w, h):
        if not self._has_size:
            self._has_size = True
        self.view_state.set_window_size(w, h)

    @staticmethod
    def _linear_from_srgb(image: NDArray):
        return numpy.where(image >= 0.04045, ((image + 0.055) / 1.055)**2.4, image/12.92)

    def set_input_format(self, input_format: InputFormat) -> bool:
        if input_format == InputFormat.STANDARD_PHOTO:
            if self.image and self.image.md.cfa_pattern != (-1, -1, -1, -1):
                self.program = self.rect_dng_shader
            else:
                self.program = self.rect_tile_shader
        else:
            if self.image and self.image.md.cfa_pattern != (-1, -1, -1, -1):
                self.program = self.sphere_dng_shader
            else:
                self.program = self.sphere_shader
        if self.image is None:
            return False
        # if self.image.md.input_format == input_format:
        #     return False
        self.image.md.input_format = input_format
        self.signal_360.emit(input_format != InputFormat.STANDARD_PHOTO)  # noqa
        logger.debug(f"input projection = {input_format}")
        self.view_state.update_input_format()
        self.input_format_changed.emit(input_format)  # noqa
        return True

    def set_image(self, image: TiledImageLike):
        logger.debug("Received image data")
        self.image = image
        self.view_state.reset()
        assert self.image is not None
        self.view_state.set_image(self.image)
        self.set_input_format(self.image.md.input_format)
        w, h = self.image.md.size_opx
        self.image_size_changed.emit(int(w), int(h))  # noqa
        self.update()

    def center_on_point(self, qpoint: QPoint):
        if self.view_state.center_on_point(qpoint):
            self.update()

    def reset_view(self):
        self.view_state.reset()
        self.update()

    @QtCore.Slot(QPoint)
    def show_context_menu(self, qpoint: QPoint):
        menu = QtWidgets.QMenu("Context menu", parent=self)
        menu.addSeparator()
        if self.image is not None:
            opx = self.view_state.opx_for_qpoint(qpoint)
            heading_action = QAction(f"Pixel [{int(opx[0])}, {int(opx[1])}]:", self)
            heading_action.setSeparator(True)  # It's just a label
            menu.addAction(heading_action)
            menu.addSeparator()
            #
            center_point_action = QtGui.QAction(text="Center on this point", parent=self)
            center_point_action.triggered.connect(lambda: self.center_on_point(qpoint))  # noqa
            menu.addAction(center_point_action)
            #
            reset_view_action = QtGui.QAction(text="Reset view", parent=self)
            reset_view_action.triggered.connect(lambda: self.reset_view())  # noqa
            menu.addAction(reset_view_action)
            #
            for action in self.view_state.context_menu_actions(qpoint):
                menu.addAction(action)
        menu.addSeparator()
        menu.addAction(QtGui.QAction("Cancel [ESC]", self))
        menu.exec(self.mapToGlobal(qpoint))

    signal_360 = QtCore.Signal(bool)

    @QtCore.Slot()
    def start_rect_with_no_point(self):
        self.view_state.sel_rect.begin(None)

    def wheelEvent(self, event: QtGui.QWheelEvent):
        d_scale = event.angleDelta().y() / 120.0
        if d_scale == 0:
            return
        d_scale = 1.12 ** d_scale
        self.view_state.zoom_relative(d_scale, event.position())
        self.update()
