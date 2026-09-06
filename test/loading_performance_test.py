"""
Compare speed of different ways of handling images

  * pre-multiply alpha
    * using numpy
    * using PIL
    * using OpenGL
    * not pre-multiplying

  * srgb to linear
    * not doing that
    * numpy
    * ?

  * pre-rotate orientation
    * not
    * PIL
    * OpenGL

  * load large jpeg
    * PIL
    * (intermediate) PIL thumbnail/draft
    * PIL-SIMD
    * Turbo-JPEG
"""

from ctypes import byref, c_ubyte, cast
from contextlib import ExitStack
from io import BytesIO
import os

import platform

import numpy
from OpenGL import GL
from PIL.Image import Resampling
from PySide6.QtGui import QSurfaceFormat, QOpenGLContext, QOffscreenSurface
from PySide6.QtWidgets import QApplication
import turbojpeg
from turbojpeg import TJFLAG_FASTUPSAMPLE, TJFLAG_FASTDCT
from ctj import *

# from vmg.image_data import ImageData
from vmg.elapsed_time import ElapsedTime
from vmg.resources import resource_filename

with ElapsedTime(message="Initialize TurboJPEG", indent=0):
    jpeg = turbojpeg.TurboJPEG()


# https://github.com/LiberTEM/LiberTEM/blob/582812b875f231d3bca009614f7c900e38fa664a/benchmarks/io/utils.py#L23
if platform.system() == "Windows":
    import win32file

    def drop_cache(flist):
        for fname in flist:
            # See https://stackoverflow.com/a/7113153/13082795
            # CreateFile:
            # https://docs.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilea
            # Direct IO: https://docs.microsoft.com/en-us/windows/win32/fileio/file-buffering
            # FILE_FLAG_NO_BUFFERING opens the file for Direct IO.
            # This drops the cache for this file. This behavior is not explicitly documented,
            # but works consistently and makes sense from a technical point of view:
            # First, writing with Direct IO inherently invalidates the cache, and second,
            # an application that opens a file with Direct IO wants to do its own buffering
            # or doesn't need buffering, so the memory can be freed up for other purposes.
            f = win32file.CreateFile(
                fname,  # fileName
                win32file.GENERIC_READ,  # desiredAccess
                win32file.FILE_SHARE_READ
                | win32file.FILE_SHARE_WRITE
                | win32file.FILE_SHARE_DELETE,  # shareMode
                None,  # attributes
                win32file.OPEN_EXISTING,  # CreationDisposition
                win32file.FILE_FLAG_NO_BUFFERING,  # flagsAndAttributes
                0,  # hTemplateFile
            )
            f.close()
else:
    def drop_cache(flist):
        for fname in flist:
            with open(fname, "rb") as f:
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def get_file(file_name: str, hot_cache=False):
    with ExitStack() as exit_stack:
        if hot_cache:
            with open(file_name, "rb") as fh0:
                fh = BytesIO(fh0.read())  # Use in-memory file-like object
        else:
            drop_cache([file_name, ])  # Use cold cache for file load timings
            fh = exit_stack.enter_context(open(file_name, "rb"))
        yield fh


def main():
    hopper_name = resource_filename("vmg.images", "hopper.gif")
    for file_name in [
        # hopper_name,
        # r"\\diskstation\Public\Pictures\2024\WaterLeak\R0016689.JPG",
        # r"C:\Users\cmbruns\Pictures\Space_Needle_panorama_large.jpg",
        r"C:\Users\cmbruns\Pictures\_Bundles_for_Berlin__More_production!.jpg",  # 30kx42k
        # r"C:\Users\cmbruns\Pictures\borf3.jpg",
    ]:
        hot_cache = True  # preload file?
        for method in [
            # decode_turbo_jpeg_full,
            decode_ctjpeg,
        ]:
            for fh in get_file(file_name, hot_cache):
                method(fh)


def profile_image(file_name):
    print(f"Loading file {file_name} :")
    with ElapsedTime(message="check existence", indent=1):
        image_data = ImageData(str(file_name))
        _file_exists = image_data.file_is_readable()
    with ElapsedTime(message="open PIL image", indent=1):
        image_data.open_pil_image()
    with ElapsedTime(message="read PIL metadata", indent=1):
        image_data.read_pil_metadata()
    with ElapsedTime(message="load PIL image", indent=1):
        # image_data.pil_image.thumbnail([200, 200], resample=Resampling.NEAREST)
        bytes1 = image_data.pil_image.tobytes()
    with ElapsedTime(message="convert to numpy", indent=1):
        numpy_image = numpy.array(image_data.pil_image)


def decode_turbo_jpeg_eighth(file_stream):
    with ElapsedTime(message="copy jpeg file to memory", indent=1):
        jpeg_bytes = file_stream.read()
    with ElapsedTime(message="load with turbojpeg SCALE (1, 8)", indent=1):
        bgr_array = jpeg.decode(jpeg_bytes, scaling_factor=(1, 8))


def decode_turbo_jpeg_full(file_stream):
    with ElapsedTime(message="copy jpeg file to memory", indent=1):
        jpeg_bytes = file_stream.read()
    with ElapsedTime(message="load with turbojpeg", indent=1):
        bgr_array = jpeg.decode(jpeg_bytes)


def decode_ctjpeg(file_stream):
    with ElapsedTime(message="load with ctjpeg", indent=1):
        with PyJpegSource(file_stream) as pjs:
            c_info = pjs.c_info
            header_status = jpeg_read_header(c_info, True)
            pjs.check_error()
            assert header_status == JPEG_HEADER_OK
            c_info.out_color_space = J_COLOR_SPACE.JCS_YCbCr  # TODO: grayscale
            not_suspended = jpeg_start_decompress(c_info)
            pjs.check_error()
            assert not_suspended
            row_stride = c_info.output_width * c_info.output_components
            assert c_info.data_precision != 12  # we aren't handling 12-bit jpeg at the moment...
            # Make a one-row-high sample array that will go away when done with image
            row_count = 100
            out_buffer = (JSAMPLE * (row_stride * row_count))()
            in_buffer = cast(out_buffer, JSAMPROW)
            output = BytesIO()
            while c_info.output_scanline < c_info.output_height:
                # jpeg_read_raw_data()  # TODO: how much faster would direct yuv be?
                num_lines_read = jpeg_read_scanlines(c_info, byref(in_buffer), row_count)
                pjs.check_error()
                assert num_lines_read == 1
                bytes_read = (c_ubyte * row_stride * num_lines_read).from_buffer(out_buffer)
                output.write(bytes_read)
            not_suspended = jpeg_finish_decompress(c_info)
            pjs.check_error()
            assert not_suspended
            image_bytes = output.getvalue()


def turbo_jpeg_case(file_stream):
    with ElapsedTime(message="copy jpeg file to memory", indent=1):
        jpeg_bytes = file_stream.read()
    with ElapsedTime(message="load with turbojpeg SCALE (1, 8)", indent=1):
        bgr_array = jpeg.decode(jpeg_bytes, scaling_factor=(1, 8))
    with ElapsedTime(message="load with turbojpeg", indent=1):
        bgr_array = jpeg.decode(jpeg_bytes)


class OpenGLSurface(object):
    def __init__(self):
        if not QApplication.instance():
            _app = QApplication()  # somehow necessary...
        gl_format = QSurfaceFormat()
        gl_format.setMajorVersion(4)
        gl_format.setMinorVersion(1)  # MacOS limited to 4.1
        self.gl_context = QOpenGLContext()
        self.gl_context.setFormat(gl_format)
        self.gl_context.create()
        assert self.gl_context.isValid()
        self.surface = QOffscreenSurface()
        self.surface.setFormat(gl_format)
        self.surface.create()
        assert self.surface.isValid()
        self.gl_context.makeCurrent(self.surface)
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        color_texture = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, color_texture)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGB,
            800, 600,
            0,
            GL.GL_RGB,
            GL.GL_UNSIGNED_BYTE,
            None,
        )
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0);
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER,
            GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D,
            color_texture,
            0
        )
        assert GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) == GL.GL_FRAMEBUFFER_COMPLETE
        # GL.glClearColor(1, 1, 1, 1)
        # GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        #

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.gl_context.makeCurrent(self.surface)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glDeleteFramebuffers(1, [self.fbo, ])
        self.gl_context.doneCurrent()


if __name__ == "__main__":
    main()
    with OpenGLSurface() as gl_surface:
        pass
