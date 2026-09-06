import sys
from typing import Optional

from PySide6 import QtCore
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QWidget
from PySide6.QtGui import QColor, QFont, QPainter


class ColoredSquareWidget(QFrame):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.setStyleSheet("border: 2px solid #FFaaaaaa")

    def sizeHint(self):
        return self.size()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.fillRect(rect, self.color)


class PixelColorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self.label = QLabel("<no color>")
        font = QFont("Consolas")
        font.setStyleHint(QFont.TypeWriter, QFont.PreferMatch)
        font.setFixedPitch(True)
        self.label.setFont(font)
        self.label.setFixedWidth(self.label.fontMetrics().boundingRect(self.label.text()).width())
        self.colored_square = ColoredSquareWidget(QColor(0, 0, 0, 0))  # Red square
        self.colored_square.setFixedHeight(self.label.sizeHint().height())
        self.colored_square.setFixedWidth(self.label.sizeHint().height())
        layout.addWidget(self.colored_square)
        layout.addWidget(self.label)

    @QtCore.Slot(tuple, int)
    def set_color(self, color: Optional[tuple] = None, format_max: int = 255):
        if color is None:
            self.label.setText("<no color>")
            self.colored_square.color = QColor(0, 0, 0, 0)
        else:
            try:
                if len(color) < 3:
                    color3 = [color[0]] * 3
                else:
                    color3 = color[:3]
            except TypeError:
                color = [color]
                color3 = color * 3
            if format_max <= 255:
                name = "#" + "".join([f"{r:02X}" for r in color])
                qcolor = QColor(*color3, 255)
            else:
                name = "#" + "".join([f"{r:04X}" for r in color])
                qcolor = QColor(*[r // 256 for r in color3], 255)
            self.colored_square.color = qcolor
            self.label.setText(name)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = PixelColorWidget()
    widget.set_color()
    widget.show()
    sys.exit(app.exec())
