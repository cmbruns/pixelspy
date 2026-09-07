import logging
import platform
import sys

from PySide6 import QtWidgets, QtCore
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QSurfaceFormat

from vmg.ui.main_window import VimageMainWindow
from .except_hook import ExceptHook
from vmg.resources import resource_filename


logger = logging.getLogger(__name__)


class VimageApplication(QtWidgets.QApplication):
    def event(self, event):
        if event.type() == QEvent.FileOpen:
            file_name = event.file()
            if file_name.endswith("Contents/plugins/python-ce/helpers/pydev/pydevd.py"):
                pass  # Elide strange signal when debugging in PyCharm on Mac
            else:
                self.on_file_open_event.emit(event.file())  # noqa
        return super().event(event)

    on_file_open_event = QtCore.Signal(str)


class VimageApp(object):
    def __init__(self):
        # import vmg.except_hook
        # Top level logger must be created before this point (see vmg.__init__.py)
        # with StdIoRedirector():
        with ExceptHook():
            logger.info("Launching PixelSpy app")
            app = self.init_app()
            self.run_main_window(app)

    @staticmethod
    def run_main_window(app):
        with VimageMainWindow() as window:
            image_list = app.arguments()[1:]
            if len(image_list) == 1:
                window.load_main_image(image_list[0])
            else:
                window.set_image_list(app.arguments()[1:], 0)
            window.show()
            icon_file = resource_filename("vmg", "images/vimage2.ico")
            icon = QIcon(icon_file)
            app.setWindowIcon(icon)
            window.setWindowIcon(icon)
            app.on_file_open_event.connect(window.file_open_event)
            sys.exit(app.exec())

    @staticmethod
    def init_app():
        QtWidgets.QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        f = QSurfaceFormat()
        f.setProfile(QSurfaceFormat.CoreProfile)
        f.setVersion(4, 1)  # Limit to OpenGL 4.1 for macOS compatibility
        QSurfaceFormat.setDefaultFormat(f)
        # Respect dark mode setting on Windows
        if platform.system() == "Windows":
            sys.argv += ['-platform', 'windows:darkmode=2']
        app = VimageApplication(sys.argv)
        app.setStyle("fusion")  # Maybe looks better than default Vista style?
        app.setAttribute(Qt.AA_EnableHighDpiScaling)  # No effect on custom cursor size
        app.setOrganizationName("rotatingpenguin.com")
        app.setApplicationName("vimage")
        app.setApplicationDisplayName("PixelSpy")
        return app


__all__ = [
    "VimageApp"
]
