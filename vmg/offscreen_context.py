from PySide6 import QtCore, QtGui


class OffscreenContext(QtCore.QObject):
    def __init__(self, parent, gl_context, gl_format):
        super().__init__(parent)
        self.parent_context = gl_context
        self.format = gl_format
        self.surface = None
        self.context = None

    # Delaying construction until just-in-time avoids a crash with makeCurrent()...
    def init_gl(self):
        if self.context is not None:
            return
        self.surface = QtGui.QOffscreenSurface()
        self.surface.setFormat(self.format)
        self.surface.create()
        assert self.surface.isValid()
        self.context = QtGui.QOpenGLContext()
        self.context.setShareContext(self.parent_context)
        self.context.setFormat(self.surface.requestedFormat())
        self.context.create()
        assert self.context.isValid()

    def __enter__(self):
        self.make_current()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.done_current()

    def done_current(self):
        self.context.doneCurrent()

    def make_current(self):
        if self.context is None:
            self.init_gl()
        self.context.makeCurrent(self.surface)
