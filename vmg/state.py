from OpenGL import GL
from math import acos, asin, atan2, cos, degrees, pi, radians, sin
from typing import Optional

import numpy
from numpy.typing import NDArray
from PySide6 import QtCore, QtGui
from PySide6.QtCore import QPoint, QSize, QObject, QPointF
from PySide6.QtGui import Qt, QAction

from vmg.action.copy_pixel_action import CopyPixelAction, copy_pixel_value
from vmg.frame import DimensionsQwn, LocationHpd, LocationUsr, LocationNic, LocationOpx, LocationGeo, \
    LocationPrj, LocationQwn, LocationRelative, DimensionsOpx
from vmg.interfaces import TiledImageLike, RenderStateLike, InputFormat, DemosaicMethod
from vmg.pixel_filter import PixelFilter, PixelNumerals
from vmg.display_projection import DisplayProjection
from vmg.selection_box import SelectionBox, CursorHolder


class ViewStateSignaller(QObject):
    cursor_changed = QtCore.Signal(CursorHolder)
    pixel_color_changed = QtCore.Signal(tuple, int)
    request_message = QtCore.Signal(str, int)


class ViewState(
    # QObject,
    RenderStateLike,  # only during linting, not runtime
):
    """
    Q: Is there one ViewState per gl_widget? Or one per image?
    A: One per gl_widget. So the image could change during the lifetime of this ViewState.
    """

    def __init__(self, window_size: QSize):
        super().__init__()
        self.vss = ViewStateSignaller()
        self._copy_pixel_action = CopyPixelAction(self.vss)
        self._background_color = [0.5, 0.5, 0.5, 0]
        self.brightness = 0.0  # EV
        self.demosaic_method = DemosaicMethod.DEFAULT
        self._size_qwn = DimensionsQwn(window_size.width(), window_size.height())
        self.display_projection = DisplayProjection.STEREOGRAPHIC
        self._zoom = 1.0  # windows per image
        self._center_rel = LocationRelative(0.5, 0.5)
        self.image = None
        self._update_aspect_scale()
        self.pixel_filter = PixelFilter.CATMULL_ROM
        self.sel_rect = SelectionBox()
        self.sel_rect.cursor_changed.connect(self.on_rect_cursor_changed)
        self._is_dragging = False
        self._previous_mouse_position = None
        self.asc_qwn = 1
        self.asc_opx = 1
        self.show_cfa_colors = True
        self.show_tile_boundaries = False
        self.show_center_guides = False
        self.anisotropic_filtering = True
        self.texture_wrap = GL.GL_CLAMP_TO_EDGE
        self.pixel_numerals = PixelNumerals.DEFAULT
        # self.input_is_linear = False

    @property
    def background_color(self):
        return self._background_color

    @background_color.setter
    def background_color(self, color):
        self._background_color[:3] = color[:3]

    @property
    def center_opx(self) -> LocationOpx:
        return LocationOpx(*self._center_rel * self._size_opx(), 1)

    @property
    def center_rel(self) -> LocationRelative:
        return self._center_rel

    def center_on_point(self, qpoint: QPoint) -> bool:
        if self.image is None:
            return False
        if self.image.md.input_format == InputFormat.STANDARD_PHOTO:
            opx = self.opx_for_qpoint(qpoint)
            w, h = self.image.md.size_opx
            self._center_rel[:] = opx[0]/w, opx[1]/h
        else:
            hpd = self.hpd_for_qwn(LocationQwn.from_qpoint(qpoint))
            self.view_heading_degrees = hpd[0]
            self.view_pitch_degrees = hpd[1]
        return True

    def _clamp_center(self):
        # TODO: we can still drag to the aspect padding...
        # Keep the center point on the actual image itself
        cx, cy = self._center_rel
        cx = max(0.0, cx)
        cy = max(0.0, cy)
        cx = min(1.0, cx)
        cy = min(1.0, cy)
        self._center_rel = LocationRelative(cx, cy)

    def context_menu_actions(self, qpoint: QPoint) -> list:
        result = []
        p_opx = self.opx_for_qpoint(qpoint)
        self.image.md.rpx_for_opx(p_opx)
        px, py = int(p_opx.x), int(p_opx.y)
        try:
            rx, ry = self.image.md.rpx_for_opx(p_opx)
            color = self.image.array[ry, rx]
            try:
                color[0]
            except IndexError:
                # Single channel needs special care.
                color = numpy.array([color], dtype=self.image.array.dtype)
            try:
                self._copy_pixel_action.disconnect()
            except TypeError:
                pass
            self._copy_pixel_action.triggered.connect(lambda: copy_pixel_value(color))
            result.append(self._copy_pixel_action)
        except IndexError:
            pass
        result.extend(self.sel_rect.context_menu_actions(
            p_opx,
            self._input_format() != InputFormat.STANDARD_PHOTO))
        return result

    @QtCore.Slot(CursorHolder)  # noqa
    def on_rect_cursor_changed(self, cursor_holder: CursorHolder):
        if cursor_holder.cursor is None:
            if self._is_dragging:
                self.vss.cursor_changed.emit(CursorHolder(Qt.ClosedHandCursor))  # noqa
            else:
                self.vss.cursor_changed.emit(CursorHolder(Qt.OpenHandCursor))  # noqa
        else:
            self.vss.cursor_changed.emit(cursor_holder)  # noqa

    def drag_relative(self, prev: QPoint, curr: QPoint):
        prev_qwn = LocationQwn.from_qpoint(prev)
        curr_qwn = LocationQwn.from_qpoint(curr)
        if self._input_format() in (
            InputFormat.EQUIRECTANGULAR,
            # This actually works for fisheye too, the pitch and heading
            # are view state parameters the superficially resemble
            # the EQUIRECTANGULAR format coordinates, but do not
            # actually depend on the input format.
            InputFormat.DUAL_FISHEYE,
            InputFormat.SINUSOIDAL,
        ):
            prev_hpd = self.hpd_for_qwn(prev_qwn)
            curr_hpd = self.hpd_for_qwn(curr_qwn)
            d_hpd = curr_hpd - prev_hpd
            # print(d_hpd)
            new_heading = self.view_heading_degrees + d_hpd.heading
            while new_heading <= -180:
                new_heading += 360
            while new_heading > 180:
                new_heading -= 360
            self.view_heading_degrees = new_heading
            new_pitch = self.view_pitch_degrees + d_hpd.pitch
            new_pitch = numpy.clip(new_pitch, -90, 90)
            self.view_pitch_degrees = new_pitch
            # print(f"New view direction heading={self.view_heading_degrees:.1f}° pitch={self.view_pitch_degrees:.1f}°")
        else:
            prev_opx = self.opx_for_qwn(prev_qwn)
            curr_opx = self.opx_for_qwn(curr_qwn)
            d_opx = curr_opx - prev_opx
            if self._size_opx().y == 0:
                return
            d_rel = (d_opx.x / self._size_opx().x, d_opx.y / self._size_opx().y)
            new_center = LocationRelative(self._center_rel.x + d_rel[0], self._center_rel.y + d_rel[1])
            self._center_rel[:] = new_center[:]
            self._clamp_center()
            # print(f"new way image center {self._center_rel}")

    @property
    def hover_min_opx(self):
        hover_min_qwn = 5  # How close do we need to be to start dragging?
        return self.opx_for_qwn(LocationQwn(hover_min_qwn, hover_min_qwn, 0)).x

    @staticmethod
    def hpd_for_geo(p_geo: LocationGeo) -> LocationHpd:
        return LocationHpd(
            degrees(atan2(p_geo.x, -p_geo.z)),
            degrees(max(-1, min(1, p_geo.y))),
        )

    def _input_format(self) -> InputFormat:
        if self.image is None:
            return InputFormat.STANDARD_PHOTO
        else:
            return self.image.md.input_format

    def hpd_for_qwn(self, p_qwn: LocationQwn) -> LocationHpd:
        return self.hpd_for_geo(self.geo_for_qwn(p_qwn))

    def key_press_event(self, event: QtGui.QKeyEvent) -> None:
        if self._input_format() == InputFormat.STANDARD_PHOTO:
            self.sel_rect.key_press_event(event)

    def key_release_event(self, event: QtGui.QKeyEvent) -> None:
        if self._input_format() == InputFormat.STANDARD_PHOTO:
            self.sel_rect.key_release_event(event)

    def mouse_move_event(self, event) -> bool:
        # Rectangular selection is only valid in non-360 mode
        update_display = False
        event_consumed = False
        p_opx = self.opx_for_qpoint(event.pos())
        if self._input_format() == InputFormat.STANDARD_PHOTO:
            event_consumed, update_display = self.sel_rect.mouse_move_event(event, p_opx, self.hover_min_opx)
        if event_consumed:
            pass
        elif self._is_dragging:
            self.drag_relative(event.pos(), self._previous_mouse_position)
            self._previous_mouse_position = event.pos()
            update_display = True
        else:
            p_qwn = LocationQwn.from_qpoint(event.pos())
            px, py = int(p_opx.x), int(p_opx.y)

            try:
                rx, ry = self.image.md.rpx_for_opx(p_opx)
                color = self.image.array[ry, rx]
            except IndexError:
                return update_display
            if self._input_format() in (
                    InputFormat.EQUIRECTANGULAR,
                    InputFormat.DUAL_FISHEYE,
                    InputFormat.SINUSOIDAL,
            ):
                p_hpd = self.hpd_for_qwn(p_qwn)
                self.vss.request_message.emit(  # noqa
                    f"image pixel = [{px}, {py}] heading = {p_hpd.heading:.1f}°  pitch = {p_hpd.pitch:.1f}°",
                    2000,
                )
            else:
                self.vss.request_message.emit(  # noqa
                    f"image pixel = [{px}, {py}]",
                    2000,
                )
            self.vss.pixel_color_changed.emit(color, self.image.md.upper_bound)  # noqa
        return update_display

    def mouse_press_event(self, event):
        keep_cursor = self.sel_rect.mouse_press_event(
                event,
                self.opx_for_qpoint(event.pos()),
                self.hover_min_opx,
        )
        self._is_dragging = True
        self._previous_mouse_position = event.pos()
        if not keep_cursor:
            self.vss.cursor_changed.emit(CursorHolder(Qt.ClosedHandCursor))  # noqa

    def mouse_release_event(self, event):
        self._is_dragging = False
        self._previous_mouse_position = None
        self.vss.cursor_changed.emit(CursorHolder(Qt.OpenHandCursor))  # noqa
        p_opx = self.opx_for_qpoint(event.pos())
        self.sel_rect.mouse_release_event(event, p_opx)

    def ndc_xform_opx(self) -> numpy.ndarray:
        s1 = 2.0 * self.asc_qwn * self.zoom / self.asc_opx
        w_qwn, h_qwn = self._size_qwn
        return numpy.array([
            [s1 / w_qwn, 0, -s1 * self.center_opx.x / w_qwn],
            [0, -s1 / h_qwn, s1 * self.center_opx.y / h_qwn],
            [0, 0, 1],
        ], dtype=numpy.float32)

    def nic_for_qwn(self, p_qwn: LocationQwn) -> LocationNic:
        w_qwn, h_qwn = self._size_qwn
        zoom = self.zoom
        scale = 1.0 / self.asc_qwn / zoom
        nic_xform_qwn = numpy.array([
            [2*scale, 0, -w_qwn*scale],
            [0, -2*scale, h_qwn*scale],
            [0, 0, 1],
        ], dtype=numpy.float32)
        return LocationNic(*nic_xform_qwn @ p_qwn)

    def usr_for_prj(self, p_prj: LocationPrj) -> LocationUsr:
        if self.display_projection == DisplayProjection.GNOMONIC:
            d = 1.0 / (p_prj[0] ** 2 + p_prj[1] ** 2 + 1) ** 0.5
            p_usr = numpy.array([  # sphere orientation as viewed on screen
                d * p_prj[0],
                d * p_prj[1],
                -d,
            ], dtype=numpy.float32)
        elif self.display_projection == DisplayProjection.EQUIDISTANT:
            r = (p_prj[0] ** 2 + p_prj[1] ** 2) ** 0.5
            d = sin(r) / r
            p_usr = numpy.array([  # sphere orientation as viewed on screen
                d * p_prj[0],
                d * p_prj[1],
                -cos(r),
            ], dtype=numpy.float32)
        elif self.display_projection == DisplayProjection.EQUIRECTANGULAR:
            cy = cos(p_prj[1])
            p_usr = numpy.array([  # sphere orientation as viewed on screen
                sin(p_prj[0]) * cy,
                sin(p_prj[1]),
                -cos(p_prj[0]) * cy,
            ], dtype=numpy.float32)
        elif self.display_projection == DisplayProjection.STEREOGRAPHIC:
            d = p_prj[0] ** 2 + p_prj[1] ** 2 + 4
            p_usr = numpy.array([  # sphere orientation as viewed on screen
                4 * p_prj[0] / d,
                4 * p_prj[1] / d,
                (d - 8) / d,
            ], dtype=numpy.float32)
        else:
            assert False  # What projection is this?
        return LocationUsr(*p_usr)

    def opx_for_qpoint(self, qpoint: QPoint) -> LocationOpx:
        return self.opx_for_qwn(LocationQwn.from_qpoint(qpoint))

    def opx_for_qwn(self, p_qwn: LocationQwn) -> LocationOpx:
        if self.image is None:
            return LocationOpx(-1, -1, 1)
        md = self.image.md
        if md.input_format == InputFormat.STANDARD_PHOTO:
            p_nic = self.nic_for_qwn(p_qwn)
            center_opx = self.center_opx
            scale = self.asc_opx / 2
            opx_xform_nic = numpy.array([
                [scale, 0, center_opx.x],
                [0, -scale, center_opx.y],
                [0, 0, 1],
            ], dtype=numpy.float32)
            return LocationOpx(*opx_xform_nic @ p_nic)
        else:
            p_geo = self.geo_for_qwn(p_qwn)
            p_pcm = self.image.md.pcm_R_geo @ p_geo
            x, y, z = p_pcm
            if md.input_format in [
                InputFormat.EQUIRECTANGULAR,
                InputFormat.SINUSOIDAL,  # TODO:
            ]:
                lon = degrees(atan2(x, -z))
                y = max(-1.0, min(1.0, y))
                lat = degrees(asin(y))
                p_otc = (
                    md.size_opx[0] * (lon + 180) / 360,
                    md.size_opx[1] * (-lat + 90) / 180,
                    1,
                )
                return LocationOpx(*p_otc)
            else:
                assert md.input_format == InputFormat.DUAL_FISHEYE
                if z <= 0:  # front lens
                    # fisheye center in right half of image
                    cx = 0.75
                    cy = 0.5
                else:  # rear lens
                    # fisheye center in left half of image
                    cx = 0.25
                    cy = 0.5
                    # Rotate 180 degrees about Y
                    x = -x
                    z = -z
                rho = acos(-z)
                d = sin(rho) / rho
                # As if one fisheye per tile
                x_aed = x / d
                y_aed = y / d
                # Virtual tile texture coordinates per fisheye ttc
                # scale by fov
                s = pi / md.inscribed_fov_radians  # big fov means smaller scale
                x_ttc = 0.5 + s * x_aed / pi
                y_ttc = 0.5 + s * y_aed / pi
                # Full dual fisheye image texture coordinates otc
                x_otc = cx + (x_ttc - 0.5) / 2
                y_otc = cy + y_ttc - 0.5
                # image pixels opx
                x_opx = x_otc * md.size_opx[0]
                y_opx = y_otc * md.size_opx[1]
                return LocationOpx(x_opx, y_opx, 1)

    def opx_scale_qwn(self) -> float:
        return self._size_opx()[1] / self._size_qwn[1] / self.zoom

    def opx_xform_ndc(self) -> NDArray[numpy.float32]:
        scale = self.asc_opx / 2.0 / self.asc_qwn / self.zoom
        w_qwn, h_qwn = self._size_qwn
        return numpy.array([
            [scale * w_qwn, 0, self.center_opx.x],
            [0, -scale * h_qwn, self.center_opx.y],
            [0, 0, 1],
        ], dtype=numpy.float32)

    def geo_for_usr(self, p_usr: LocationUsr) -> LocationGeo:
        return LocationGeo(*self.geo_rot_usr @ p_usr)

    @property
    def geo_rot_usr(self) -> NDArray[numpy.float32]:
        c = cos(radians(self.view_heading_degrees))
        s = sin(radians(self.view_heading_degrees))
        rot_heading = numpy.array([
            [c, 0, -s],
            [0, 1, 0],
            [s, 0, c],
        ], dtype=numpy.float32)
        c = cos(radians(self.view_pitch_degrees))
        s = sin(radians(self.view_pitch_degrees))
        rot_pitch = numpy.array([
            [1, 0, 0],
            [0, c, -s],
            [0, s, c],
        ], dtype=numpy.float32)
        return rot_heading @ rot_pitch

    def geo_for_qwn(self, p_qwn: LocationQwn) -> LocationGeo:
        p_prj = self.prj_for_qwn(p_qwn)
        p_usr = self.usr_for_prj(p_prj)
        return self.geo_for_usr(p_usr)

    def prj_for_qwn(self, p_qwn: LocationQwn) -> LocationPrj:
        p_nic = self.nic_for_qwn(p_qwn)
        prj_xform_nic = numpy.array([
            [pi/2, 0, 0],
            [0, pi/2, 0],
            [0, 0, 1],
        ], dtype=numpy.float32)
        return LocationPrj(*prj_xform_nic @ p_nic)

    def reset(self) -> None:
        self._zoom = 1.0  # windows per image
        self._center_rel = LocationRelative(0.5, 0.5)
        if self.image is not None:
            self.view_heading_degrees = self.image.md.initial_heading_degrees
            self.view_pitch_degrees = self.image.md.initial_pitch_degrees
            # Ignoring roll for now; we don't have nor want a view roll control
        else:
            self.view_heading_degrees = 0.0
            self.view_pitch_degrees = 0.0
        self.brightness = 0.0

    def update_input_format(self) -> None:
        self._update_aspect_scale()

    def set_image(self, image: TiledImageLike):
        if self.image is image:
            return
        # TODO: store image and delegate
        self.image = image
        self._update_aspect_scale()
        self.reset()

    def set_window_size(self, width, height):
        self._size_qwn = DimensionsQwn(width, height)
        self._update_aspect_scale()

    def _size_opx(self) -> DimensionsOpx:
        if self.image is None:
            return DimensionsOpx(1, 1)
        return self.image.md.size_opx

    @QtCore.Slot()  # noqa
    def start_rect_with_no_point(self):
        self.sel_rect.begin(None)

    def _update_aspect_scale(self):
        w_opx, h_opx = self._size_opx()
        if w_opx == 0:
            return
        if h_opx == 0:
            return
        w_qwn, h_qwn = self._size_qwn
        if self._input_format() in (
            InputFormat.EQUIRECTANGULAR,
            InputFormat.DUAL_FISHEYE,
            InputFormat.SINUSOIDAL,
        ):
            if 1 > w_qwn/h_qwn:
                # window aspect is thin
                # So use width in scaling factor
                self.asc_opx = w_opx
                self.asc_qwn = w_qwn
            else:
                # Use height in scaling factor
                self.asc_opx = h_opx
                self.asc_qwn = h_qwn
        else:  # rectangular image
            if w_opx/h_opx > w_qwn/h_qwn:
                # Image aspect is wider than window aspect
                # So use width in scaling factor
                self.asc_opx = w_opx
                self.asc_qwn = w_qwn
            else:
                # Use height in scaling factor
                self.asc_opx = h_opx
                self.asc_qwn = h_qwn

    @property
    def view_heading_degrees(self):
        # interpret center point as heading/pitch in 360 mode
        return (self._center_rel.x - 0.5) * 360.0

    @view_heading_degrees.setter
    def view_heading_degrees(self, value):
        while value > 180.0:
            value -= 360.0
        while value <= -180.0:
            value += 360.0
        self._center_rel[0] = value / 360.0 + 0.5

    @property
    def view_pitch_degrees(self):
        # interpret center point as heading/pitch in 360 mode
        return (self._center_rel.y - 0.5) * 180.0

    @view_pitch_degrees.setter
    def view_pitch_degrees(self, value: float):
        value = numpy.clip(value, -90.0, 90.0)
        self._center_rel[1] = value / 180.0 + 0.5

    @property
    def window_size(self) -> DimensionsQwn:
        return self._size_qwn

    @property
    def zoom(self) -> float:
        return self._zoom

    def zoom_relative(self, zoom_factor: float, zoom_center: Optional[QPointF]):
        old_zoom = self._zoom
        new_zoom = self._zoom * zoom_factor
        # Limit zoom-out because you never need more than twice the image dimension to move around
        if new_zoom <= 0.30:
            new_zoom = 0.30
        self._zoom = new_zoom
        if zoom_center is not None:
            p_qwn = LocationQwn(zoom_center.x(), zoom_center.y(), 1)
            if self._input_format() in (
                    InputFormat.EQUIRECTANGULAR,
                    InputFormat.DUAL_FISHEYE,  # TODO: close enough?
                    InputFormat.SINUSOIDAL,
            ):
                self._zoom = old_zoom
                before_hpd = self.hpd_for_qwn(p_qwn)  # Before position
                self._zoom = new_zoom
                after_hpd = self.hpd_for_qwn(p_qwn)  # After position
                dh = after_hpd.heading - before_hpd.heading
                dp = after_hpd.pitch - before_hpd.pitch
                self.view_heading_degrees -= dh
                self.view_pitch_degrees -= dp
            else:
                self._zoom = old_zoom
                before_opx = self.opx_for_qwn(p_qwn)  # Before position
                self._zoom = new_zoom
                after_opx = self.opx_for_qwn(p_qwn)  # After position
                dx = after_opx.x - before_opx.x
                dy = after_opx.y - before_opx.y
                if self._size_opx().x:
                    self._center_rel = self._center_rel - (dx/self._size_opx().x, dy/self._size_opx().y)
        if self._input_format() == InputFormat.STANDARD_PHOTO:
            self._clamp_center()
