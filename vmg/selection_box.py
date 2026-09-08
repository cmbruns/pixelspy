import enum
from typing import Optional, Tuple

import numpy
from PySide6 import QtCore, QtGui
from PySide6.QtGui import Qt

from vmg.frame import LocationOpx
from vmg.resources import resource_filename


class SelState(enum.Enum):
    INACTIVE = 0
    FINDING_FIRST_POINT = 1
    FINDING_SECOND_POINT = 2
    COMPLETE = 3


class AdjustType(enum.Enum):
    NONE = 0
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4
    TOP_LEFT = 5
    BOTTOM_LEFT = 6
    TOP_RIGHT = 7
    BOTTOM_RIGHT = 8


class CursorHolder(object):
    """Hack to allow passing QCursor or None"""
    def __init__(self, cursor: Optional[Qt.CursorShape]):
        self.cursor = cursor


class SelectionBox(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.left_top_right_bottom = numpy.array(
            [0, 0, 0, 0],
            dtype=numpy.int32,
        )
        self._first_point_omp = [0, 0]
        self._second_point_omp = [0, 0]
        self.state = SelState.INACTIVE
        self.adjusting = AdjustType.NONE

    def __str__(self):
        return f"(left={self.left}, top={self.top}, right={self.right}, bottom={self.bottom})"

    def begin(self, p_omp: Optional[LocationOpx]):
        if p_omp is None:
            self.state = SelState.FINDING_FIRST_POINT
        else:
            self.first_point_omp = p_omp
            self.state = SelState.FINDING_SECOND_POINT
        self.cursor_changed.emit(CursorHolder(Qt.CrossCursor))  # noqa

    @property
    def bottom(self) -> int:
        return self.left_top_right_bottom[3]

    @bottom.setter
    def bottom(self, value):
        self.left_top_right_bottom[3] = int(value + 0.5)

    @QtCore.Slot()  # noqa
    def clear(self):
        self.state = SelState.INACTIVE
        self.left_top_right_bottom[:] = (0, 0, 0, 0)
        self.adjusting = AdjustType.NONE
        self.selection_shown.emit(False)  # noqa

    def context_menu_actions(self, p_omp: LocationOpx, is_360: bool) -> list:
        result = []
        if not is_360:
            start_action = StartRectAction(self)
            start_action.triggered.connect(lambda: self.begin(p_omp))  # noqa
            result.append(start_action)
        return result

    cursor_changed = QtCore.Signal(CursorHolder)

    @property
    def first_point_omp(self):
        return self._first_point_omp

    @first_point_omp.setter
    def first_point_omp(self, xy):
        self._first_point_omp[:] = xy[:]
        self._update_shape()

    def is_active(self):
        return self.state == SelState.COMPLETE and self.left != self.right

    def _is_near_edge(self, p_omp, hover_min_omp) -> AdjustType:
        best = AdjustType.LEFT
        ld = abs(p_omp.x - self.left)
        rd = abs(p_omp.x - self.right)
        td = abs(p_omp.y - self.top)
        bd = abs(p_omp.y - self.bottom)
        min_dist = ld
        if min_dist > rd:
            min_dist = rd
            best = AdjustType.RIGHT
        if min_dist > td:
            min_dist = td
            best = AdjustType.TOP
        if min_dist > bd:
            min_dist = bd
            best = AdjustType.BOTTOM
        if min_dist > hover_min_omp:
            return AdjustType.NONE  # No edges are close enough
        # Check for corners
        if best == AdjustType.LEFT:
            if td < 6 * hover_min_omp and bd > 12 * hover_min_omp:
                best = AdjustType.TOP_LEFT
            elif bd < 6 * hover_min_omp and td > 12 * hover_min_omp:
                best = AdjustType.BOTTOM_LEFT
        elif best == AdjustType.RIGHT:
            if td < 6 * hover_min_omp and bd > 12 * hover_min_omp:
                best = AdjustType.TOP_RIGHT
            elif bd < 6 * hover_min_omp and td > 12 * hover_min_omp:
                best = AdjustType.BOTTOM_RIGHT
        elif best == AdjustType.TOP:
            if ld < 6 * hover_min_omp and rd > 12 * hover_min_omp:
                best = AdjustType.TOP_LEFT
            elif rd < 6 * hover_min_omp and ld > 12 * hover_min_omp:
                best = AdjustType.TOP_RIGHT
        elif best == AdjustType.BOTTOM:
            if ld < 6 * hover_min_omp and rd > 12 * hover_min_omp:
                best = AdjustType.BOTTOM_LEFT
            elif rd < 6 * hover_min_omp and ld > 12 * hover_min_omp:
                best = AdjustType.BOTTOM_RIGHT
            # TODO: other corners
        return best

    def key_press_event(self, event: QtGui.QKeyEvent):
        if event.key() == Qt.Key_Shift:
            self.cursor_changed.emit(CursorHolder(Qt.CrossCursor))

    def key_release_event(self, event: QtGui.QKeyEvent):
        if event.key() == Qt.Key_Shift:
            self.cursor_changed.emit(CursorHolder(None))

    @property
    def left(self) -> int:
        return self.left_top_right_bottom[0]

    @left.setter
    def left(self, value):
        self.left_top_right_bottom[0] = int(value + 0.5)

    def mouse_move_event(self, event, p_omp, hover_min_omp) -> Tuple[bool, bool]:
        update_display = False
        event_consumed = False
        # Shift-dragging a new box takes priority over adjusting an old box
        # Is the user ready to shift-drag a new box?
        if self.state in (SelState.COMPLETE, SelState.INACTIVE) and event.modifiers() & Qt.ShiftModifier:
            pass
        elif self.state == SelState.FINDING_SECOND_POINT:
            self.second_point_omp = p_omp
            update_display = True
            event_consumed = True
        elif self.state == SelState.COMPLETE:
            if self.adjusting == AdjustType.NONE:
                # Show correct cursor when hovering
                adj = self._is_near_edge(p_omp, hover_min_omp)
                if adj == AdjustType.LEFT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeHorCursor))
                elif adj == AdjustType.RIGHT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeHorCursor))
                elif adj == AdjustType.TOP:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeVerCursor))
                elif adj == AdjustType.BOTTOM:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeVerCursor))
                elif adj == AdjustType.TOP_LEFT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeFDiagCursor))
                elif adj == AdjustType.BOTTOM_LEFT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeBDiagCursor))
                elif adj == AdjustType.TOP_RIGHT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeBDiagCursor))
                elif adj == AdjustType.BOTTOM_RIGHT:
                    self.cursor_changed.emit(CursorHolder(Qt.SizeFDiagCursor))
                else:
                    self.cursor_changed.emit(CursorHolder(None))
            else:
                if self.adjusting == AdjustType.LEFT:
                    self.left = p_omp.x
                elif self.adjusting == AdjustType.RIGHT:
                    self.right = p_omp.x
                elif self.adjusting == AdjustType.TOP:
                    self.top = p_omp.y
                elif self.adjusting == AdjustType.BOTTOM:
                    self.bottom = p_omp.y
                elif self.adjusting == AdjustType.TOP_LEFT:
                    self.top = p_omp.y
                    self.left = p_omp.x
                elif self.adjusting == AdjustType.BOTTOM_LEFT:
                    self.bottom = p_omp.y
                    self.left = p_omp.x
                elif self.adjusting == AdjustType.TOP_RIGHT:
                    self.top = p_omp.y
                    self.right = p_omp.x
                elif self.adjusting == AdjustType.BOTTOM_RIGHT:
                    self.bottom = p_omp.y
                    self.right = p_omp.x
                update_display = True
                event_consumed = True
        return event_consumed, update_display

    def mouse_press_event(self, event: QtGui.QMouseEvent, p_omp, hover_min_omp) -> bool:
        keep_cursor = False
        # Is the user trying to shift-drag a new box?
        if self.state in (SelState.COMPLETE, SelState.INACTIVE) and event.modifiers() & Qt.ShiftModifier:
            self.first_point_omp = p_omp
            self.second_point_omp = p_omp
            self.state = SelState.FINDING_SECOND_POINT
            keep_cursor = True
        # Is the user trying to adjust the box?
        elif self.state == SelState.COMPLETE and self._is_near_edge(p_omp, hover_min_omp) != AdjustType.NONE:
            self.adjusting = self._is_near_edge(p_omp, hover_min_omp)
            keep_cursor = True
        # Is the user trying to place the first box corner after triggering selection mode?
        elif self.state == SelState.FINDING_FIRST_POINT:
            # Click on image after activating box select mode
            self.first_point_omp = p_omp
            self.second_point_omp = p_omp
            self.state = SelState.FINDING_SECOND_POINT
            keep_cursor = True
        # Is the user trying to place the final box corner?
        elif self.state == SelState.FINDING_SECOND_POINT:
            self.second_point_omp = p_omp
            self.state = SelState.COMPLETE
            self.cursor_changed.emit(CursorHolder(None))  # noqa
            self.selection_shown.emit(True)
            keep_cursor = True
        return keep_cursor

    def mouse_release_event(self, _event, p_omp):
        if self.adjusting != AdjustType.NONE:
            self.adjusting = AdjustType.NONE
        elif self.state == SelState.FINDING_SECOND_POINT:
            self.second_point_omp = p_omp
            self.state = SelState.COMPLETE
            self.cursor_changed.emit(CursorHolder(None))  # noqa
            self.selection_shown.emit(True)

    @property
    def right(self) -> int:
        return self.left_top_right_bottom[2]

    @right.setter
    def right(self, value):
        self.left_top_right_bottom[2] = int(value + 0.5)

    @QtCore.Slot()  # noqa
    def start_rect_with_no_point(self):
        self.begin(None)

    @property
    def second_point_omp(self):
        return self._second_point_omp

    @second_point_omp.setter
    def second_point_omp(self, xy):
        self._second_point_omp[:] = xy[:]
        self._update_shape()

    selection_shown = QtCore.Signal(bool)

    @property
    def top(self) -> int:
        return self.left_top_right_bottom[1]

    @top.setter
    def top(self, value):
        self.left_top_right_bottom[1] = int(value + 0.5)

    def _update_shape(self):
        # Keep the values sorted
        self.left = min(
            self._first_point_omp[0],
            self._second_point_omp[0],
        )
        self.top = min(
            self._first_point_omp[1],
            self._second_point_omp[1],
        )
        self.right = max(
            self._first_point_omp[0],
            self._second_point_omp[0],
        )
        self.bottom = max(
            self._first_point_omp[1],
            self._second_point_omp[1],
        )


_rect_icon = None


class StartRectAction(QtGui.QAction):
    def __init__(self, parent=None):
        global _rect_icon
        if _rect_icon is None:
            _rect_icon = QtGui.QIcon(resource_filename("vmg.images", "box_icon.png"))
        super().__init__(
            parent=parent,
            text="Start selecting a rectangle here",
            icon=_rect_icon,
        )
