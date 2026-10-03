"""
Intended as partial Replacement for ImageData, Texture
"""

from ctypes import c_float, c_void_p, cast, sizeof

import imagecodecs
import logging
from tifffile import TiffFileError
from typing import Iterator

import numpy
from numpy.typing import NDArray
from OpenGL import GL
from OpenGL.GL.EXT.texture_filter_anisotropic import (
    GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT,
    GL_TEXTURE_MAX_ANISOTROPY_EXT,
)
from OpenGL.GL.shaders import compileProgram, compileShader
import PIL
from PIL import Image
from PySide6 import QtCore
import tifffile

from vmg.load_progress import LoadProgress
from vmg.metadata import ImageMetadata
from vmg.exif_orientation import ExifOrientation
from vmg.interfaces import TiledImageLike, TileLike, PhotometricScale, DemosaicMethod
from vmg.uniforms import DngUniforms
from vmg.shader_exception import compile_shader

logger = logging.getLogger(__name__)
GLenum = int
GLint = int


TILE_SIZE = 512

gl_type_for_numpy_dtype = {
    numpy.dtype("int8"): GL.GL_BYTE,
    numpy.dtype("uint8"): GL.GL_UNSIGNED_BYTE,
    numpy.dtype("int16"): GL.GL_SHORT,
    numpy.dtype("uint16"): GL.GL_UNSIGNED_SHORT,
    numpy.dtype("int32"): GL.GL_INT,
    numpy.dtype("uint32"): GL.GL_UNSIGNED_INT,
    numpy.dtype("float16"): GL.GL_HALF_FLOAT,
    numpy.dtype("float32"): GL.GL_FLOAT,
    numpy.dtype("float64"): GL.GL_DOUBLE,
}

internal_format_for_channel_count = {
    1: GL.GL_RED,
    2: GL.GL_RG,
    3: GL.GL_RGB,
    4: GL.GL_RGBA,
}

internal_format_for_channel_count16 = {
    1: GL.GL_R16,
    2: GL.GL_RG16,
    3: GL.GL_RGB16,
    4: GL.GL_RGBA16,
}

rotation_for_exif_orientation = {
    1: numpy.array([[1, 0], [0, 1]], dtype=numpy.float32),
    2: numpy.array([[-1, 0], [0, 1]], dtype=numpy.float32),
    3: numpy.array([[-1, 0], [0, -1]], dtype=numpy.float32),
    4: numpy.array([[1, 0], [0, -1]], dtype=numpy.float32),
    5: numpy.array([[0, 1], [1, 0]], dtype=numpy.float32),
    6: numpy.array([[0, 1], [-1, 0]], dtype=numpy.float32),
    7: numpy.array([[0, -1], [-1, 0]], dtype=numpy.float32),
    8: numpy.array([[0, -1], [1, 0]], dtype=numpy.float32),
}


class ImageSignaller(QtCore.QObject):
    progress_changed = QtCore.Signal(int, TiledImageLike)
    image_displayed = QtCore.Signal(TiledImageLike)


class TiledImage(TiledImageLike):
    """
    August 2026 refactor to replace ImageLikes with one class
    """
    def __init__(self):
        self.sq = ImageSignaller()
        self.md = ImageMetadata()
        self.tiles: list[TileLike] = []
        self.load_progress = LoadProgress.NONE
        self.array = None
        self.pil_image = None
        self.demosaic_method = DemosaicMethod.DEFAULT

    def initialize_gl(self):
        if self.md.cfa_pattern != (-1, -1, -1, -1):
            assert self.array is not None
            assert self.array.dtype == numpy.uint16
            for tile in generate_tiles(
                    image=self,
                    pad=6,
                    tex_format=GL.GL_R16,
                    tile_class=DngTile,
            ):
                self.tiles.append(tile)
        else:
            for tile in generate_tiles(self):
                self.tiles.append(tile)

    def load_from_file(self, file_name: str) -> bool:
        # Try tifffile first, so we can get the DNG, not the thumbnail
        try:
            with tifffile.TiffFile(file_name) as dng:
                self.load_from_tifffile(dng, file_name)
                return True
        except TiffFileError:
            pass
        try:
            # Load as a PIL Image
            pil_image = Image.open(file_name)
            self.load_from_pil_image(pil_image, file_name)
            return True
        except PIL.UnidentifiedImageError:
            pass
        self.set_progress(LoadProgress.ERROR)
        return False

    def load_from_pil_image(self, pil_image: Image.Image, file_name: str):
        self.md.file_name = file_name
        # TODO: create a palette shader to avoid munging pixels here
        if pil_image.mode in ["P", ]:  # Palette image
            pil_image = pil_image.convert("RGBA")
        self.pil_image = pil_image
        self.set_progress(LoadProgress.FILE_OPENED)
        # self.md.load_exiftool(file_name)
        self.md.load_pil_image(pil_image)
        self.set_progress(LoadProgress.METADATA_LOADED)
        self.sq.progress_changed.emit(2, self)  # noqa
        self.array = numpy.array(pil_image)
        self.set_progress(LoadProgress.ARRAY_CREATED)

    def load_from_tifffile(self, dng: tifffile.TiffFile, file_name: str):
        self.md.file_name = file_name
        self.set_progress(LoadProgress.FILE_OPENED)
        root_page = dng.pages[0]
        # Find raw image in ricoh theta Z1
        page = None
        for ix, series in enumerate(dng.series):
            # print(f"Series {ix}: Shape {series.shape}, Dtype {series.dtype}")  # noqa
            if series.dtype == numpy.uint16:
                raw_page = series
                page = raw_page.pages[0]
        if page is None:
            page = root_page
        # print(root_page.tags.get("AsShotNeutral").value)
        # Populate metadata
        self.md.photometric_scale = PhotometricScale.LINEAR
        self.md.upper_bound = numpy.iinfo(page.dtype).max  # noqa
        # self.md.load_exiftool(file_name)  # takes longer but life is short
        self.md.load_tifffile_page(page, root_page)
        self.set_progress(LoadProgress.METADATA_LOADED)
        # Slurp the raw bytes
        try:
            self.array = page.asarray()
        except imagecodecs.DelayedImportError as exc:
            logger.error(exc)
            raise
        self.set_progress(LoadProgress.ARRAY_CREATED)

    def paint_gl(self, program, view_state):
        is_complete = True  # start optimistic
        for tile in self.tiles:
            GL.glUniformMatrix3fv(program.tile_X_img_location, 1, True, tile.tile_X_img)
            GL.glUniform4f(program.uv_bounds_location, *tile.uv_bounds)
            if not tile.paint_gl(view_state):
                is_complete = False
            if is_complete and self.load_progress != LoadProgress.DISPLAYED:
                self.load_progress = LoadProgress.DISPLAYED
                self.sq.image_displayed.emit(self)  # noqa
            # break  # just one tile for testing

    def set_display_complete(self):
        if self.load_progress == LoadProgress.DISPLAYED:
            return  # Already done
        self.load_progress = LoadProgress.DISPLAYED
        self.sq.image_displayed.emit(self)  # noqa

    def set_progress(self, progress: LoadProgress):
        self.load_progress = progress
        self.sq.progress_changed.emit(progress.value, self)  # noqa

    def update_demosaic_method_gl(self, demosaic_method: DemosaicMethod):
        result = False
        self.demosaic_method = demosaic_method
        for tile in self.tiles:
            if tile.update_demosaic_method_gl(demosaic_method):
                result = True
        return result


class TileCreateInfo:
    """Parameters for creating a renderable image tile"""
    def __init__(self, image: TiledImage, pad: int = 2):
        self.image: TiledImage = image
        self.left: int = 0
        self.top: int = 0
        self.width: int = 0
        self.height: int = 0
        self.left_pad: int = pad
        self.top_pad: int = pad
        self.right_pad: int = pad
        self.bottom_pad: int = pad
        self.internal_format: GLenum = GL.GL_RGBA
        self.tex_format: GLenum = self.internal_format
        self.data_type: GLenum = GL.GL_UNSIGNED_BYTE


class Tile(TileLike):
    def __init__(self, tci: TileCreateInfo):
        self.tci = tci
        self.vao = None
        self.vbo = None
        self.padded_width = tci.width + tci.left_pad + tci.right_pad
        self.padded_height = tci.height + tci.top_pad + tci.bottom_pad
        # Convert to oriented image pixel coordinates (opx)
        left_rmp = tci.left
        right_rmp = left_rmp + tci.width
        top_rmp = tci.top
        bottom_rmp = top_rmp + tci.height
        left_opx, top_opx = opx_for_rmp((left_rmp, top_rmp), tci.image.md.size_rpx, tci.image.md.orientation)
        right_opx, bottom_opx = opx_for_rmp((right_rmp, bottom_rmp), tci.image.md.size_rpx, tci.image.md.orientation)
        left_tc = tci.left_pad / self.padded_width
        right_tc = 1 - tci.right_pad / self.padded_width
        top_tc = tci.top_pad / self.padded_height
        bottom_tc = 1 - tci.bottom_pad / self.padded_height
        if tci.image.md.orientation in [
            ExifOrientation.FLIP_HORIZONTAL_ROTATE_90_CCW,
            ExifOrientation.ROTATE_90_CW,
            ExifOrientation.FLIP_HORIZONTAL_ROTATE_90_CW,
            ExifOrientation.ROTATE_90_CCW,
        ]:
            # swap upper right and lower left
            self.vertexes = numpy.array(
                [
                    # opx_x, opx_y, txc_x, txc_y
                    [left_opx, top_opx, left_tc, top_tc],  # upper left
                    [left_opx, bottom_opx, right_tc, top_tc],  # lower left
                    [right_opx, top_opx, left_tc, bottom_tc],  # upper right
                    [right_opx, bottom_opx, right_tc, bottom_tc],  # lower right
                ],
                dtype=numpy.float32,
            ).flatten()
        else:
            self.vertexes = numpy.array(
                [
                    # opx_x, opx_y, txc_x, txc_y
                    [left_opx, top_opx, left_tc, top_tc],  # upper left
                    [left_opx, bottom_opx, left_tc, bottom_tc],  # lower left
                    [right_opx, top_opx, right_tc, top_tc],  # upper right
                    [right_opx, bottom_opx, right_tc, bottom_tc],  # lower right
                ],
                dtype=numpy.float32,
            ).flatten()
        self.texture_id = None
        self.load_sync = None

        iw, ih = tci.image.md.size_rpx
        self._tile_X_img = numpy.array([
            [iw / self.padded_width, 0, -(tci.left - tci.left_pad)/self.padded_width],
            [0, ih / self.padded_height, -(tci.top - tci.top_pad)/self.padded_height],
            [0, 0, 1],
        ], dtype=numpy.float32)
        self.uv_bounds = (  # (u_min, v_min, u_max, v_max)
            tci.left_pad / self.padded_width,
            tci.top_pad / self.padded_height,
            (tci.left_pad + tci.width) / self.padded_width,
            (tci.top_pad + tci.height) / self.padded_height)
        self.boundary_ebo = None

    def initialize_gl(self):
        self.vbo = GL.glGenBuffers(1)  # noqa
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(self.vertexes) * sizeof(c_float), self.vertexes, GL.GL_STATIC_DRAW)
        self.texture_id = GL.glGenTextures(1)  # noqa
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)  # In case width is odd
        # Show monochrome images as gray, not red
        if self.tci.internal_format == GL.GL_RED:
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_G, GL.GL_RED)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_B, GL.GL_RED)
        # TODO: use preferred internal format in image data...
        # row stride required for horizontal tiling
        iw, ih = self.tci.image.md.size_rpx
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, iw)
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_PIXELS, self.tci.left - self.tci.left_pad)
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_ROWS, self.tci.top - self.tci.top_pad)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            self.tci.internal_format,
            self.padded_width,
            self.padded_height,
            0,
            self.tci.tex_format,
            self.tci.data_type,
            self.tci.image.array,
        )
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        # Anisotropic filtering
        f_largest = GL.glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)  # noqa
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, f_largest)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        # TODO: test and debug 360 boundary conditions with tiled image
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        # Restore normal unpack settings
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_PIXELS, 0)
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_ROWS, 0)

        # Tile boundaries
        self.boundary_ebo = GL.glGenBuffers(1)  # noqa
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.boundary_ebo)
        indices = numpy.array([
            0, 1, 3, 2,
        ], dtype=numpy.uint32)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)
        self.load_sync = GL.glFenceSync(GL.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        GL.glFlush()

    def initialize_arrays(self):
        if self.vao is not None:
            return
        self.render_vao = GL.glGenVertexArrays(1)  # noqa
        self.vao = self.render_vao
        GL.glBindVertexArray(self.render_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        f_size = sizeof(c_float)
        GL.glVertexAttribPointer(  # normalized image coordinates
            1,  # attribute index
            2,  # size (#components)
            GL.GL_FLOAT,  # type
            False,  # normalized
            f_size * 4,  # stride (bytes)
            cast(0 * f_size, c_void_p),  # pointer offset
        )
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(  # texture coordinates
            2,  # attribute index
            2,  # size (#components)
            GL.GL_FLOAT,  # type
            False,  # normalized
            f_size * 4,  # stride (bytes)
            cast(2 * f_size, c_void_p),  # pointer offset
        )
        GL.glEnableVertexAttribArray(2)

    def is_ready(self) -> bool:
        if self.load_sync is None:
            return False
        load_status = GL.glGetSynciv(self.load_sync, GL.GL_SYNC_STATUS, 1)[1]  # noqa
        return load_status == GL.GL_SIGNALED

    def is_ready_for_display(self) -> bool:
        if self.load_sync is None:
            return False
        status = GL.glClientWaitSync(
            self.load_sync,
            GL.GL_SYNC_FLUSH_COMMANDS_BIT,
            0,
        )
        return status in (GL.GL_ALREADY_SIGNALED, GL.GL_CONDITION_SATISFIED)

    def paint_gl(self, view_state) -> bool:
        if not self.is_ready_for_display():
            return False
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
        # Debuggable texture parameters
        # Anisotropic filtering AFTER texture binding
        if view_state.anisotropic_filtering:
            aniso = GL.glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)  # noqa
        else:
            aniso = 1
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, aniso)
        #
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, view_state.texture_wrap)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, view_state.texture_wrap)
        #
        # VAO must be created here, in the render thread
        if self.vao is None:
            self.vao = GL.glGenVertexArrays(1)  # noqa
            GL.glBindVertexArray(self.vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
            f_size = sizeof(c_float)
            GL.glVertexAttribPointer(  # normalized image coordinates
                1,  # attribute index
                2,  # size (#components)
                GL.GL_FLOAT,  # type
                False,  # normalized
                f_size * 4,  # stride (bytes)
                cast(0 * f_size, c_void_p),  # pointer offset
            )
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(  # texture coordinates
                2,  # attribute index
                2,  # size (#components)
                GL.GL_FLOAT,  # type
                False,  # normalized
                f_size * 4,  # stride (bytes)
                cast(2 * f_size, c_void_p),  # pointer offset
            )
            GL.glEnableVertexAttribArray(2)
        GL.glBindVertexArray(self.vao)
        # outlines_only = False
        # if outlines_only:
        #     self.paint_tile_boundary()
        # else:
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)  # Full screen quad
        return True

    def paint_boundary(self):
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.boundary_ebo)
        GL.glDrawElements(GL.GL_LINE_LOOP, 4, GL.GL_UNSIGNED_INT, None)

    @property
    def tile_X_img(self) -> NDArray[numpy.floating]:
        return self._tile_X_img


def generate_tiles(
        image: TiledImage,
        tile_size: int = TILE_SIZE,
        pad: int = 2,
        tex_format=None,
        tile_class: type = Tile,
) -> Iterator[Tile]:
    max_texture_size = GL.glGetIntegerv(GL.GL_MAX_TEXTURE_SIZE)  # noqa
    assert max_texture_size >= tile_size
    # Loop over tiles
    w, h = (int(x) for x in image.md.size_rpx)
    assert image.array is not None
    data_type = gl_type_for_numpy_dtype[image.array.dtype]
    channel_count = image.md.channel_count
    if data_type in [GL.GL_SHORT, GL.GL_UNSIGNED_SHORT]:
        internal_format = internal_format_for_channel_count16[channel_count]
    else:
        internal_format = internal_format_for_channel_count[channel_count]
    if tex_format is None:
        tex_format = internal_format_for_channel_count[channel_count]  # TODO: BGR, GL_RGB16 etc.
    top = 0
    top_pad = 0
    while top < h:
        # determine height
        height = min(tile_size, h - top)
        # determine bottom_pad
        if top + tile_size >= h:
            bottom_pad = 0
        else:
            bottom_pad = min(pad, h - top - tile_size)
        left = 0
        left_pad = 0
        while left < w:
            tci = TileCreateInfo(image, pad)
            tci.left = left
            tci.top = top
            tci.width = min(tile_size, w - left)
            tci.height = height
            tci.left_pad = left_pad
            tci.top_pad = top_pad
            if left + tile_size >= w:
                tci.right_pad = 0
            else:
                tci.right_pad = min(pad, w - left - tile_size)
            tci.bottom_pad = bottom_pad
            tci.internal_format = internal_format
            tci.tex_format = tex_format
            tci.data_type = data_type
            tile = tile_class(tci)
            tile.initialize_gl()
            yield tile
            # advance
            left += tile_size
            left_pad = pad
        top += tile_size
        top_pad = pad


class DngTile(Tile):
    # Loader thread resources:
    demosaic_framebuffer = None
    demosaic_program = None
    demosaic_vao = None
    uDng = DngUniforms()

    def __init__(self, tci: TileCreateInfo):
        super().__init__(tci)
        self.bayer_texture_id = None
        self.texture_id = None  # alias for bayer_texture_id
        self.bayer_array = tci.image.array
        self.demosaic_texture_id = None
        self.render_vao = None
        self.demosaic_method = DemosaicMethod.DEFAULT

    def update_demosaic_method_gl(self, demosaic_method: DemosaicMethod) -> bool:
        if self.demosaic_method == demosaic_method:
            return False
        self.demosaic_method = demosaic_method
        self._update_demosaic_gl()
        return True

    def _update_demosaic_gl(self):
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.demosaic_framebuffer)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.demosaic_texture_id)
        # Attach texture to framebuffer
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER,
            GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D,
            self.demosaic_texture_id,
            0  # mip level
        )
        # Set draw buffers
        GL.glDrawBuffers(1, [GL.GL_COLOR_ATTACHMENT0])
        # Check completeness
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"Framebuffer incomplete: 0x{status:X}")
        # Populate the demosaic texture
        GL.glBindVertexArray(self.demosaic_vao)
        demosaic_w = max(1, self.padded_width)
        demosaic_h = max(1, self.padded_height)
        GL.glViewport(0, 0, demosaic_w, demosaic_h)
        # Render
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.bayer_texture_id)
        GL.glUseProgram(self.demosaic_program)
        self.uDng.set(self.tci.image)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        # Generate demosaic mipmaps
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.demosaic_texture_id)
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        # We do catrom filtering in-shadero, so use GL_NEAREST for now
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        # Anisotropic filtering
        f_largest = GL.glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)  # noqa
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, f_largest)

    def initialize_gl(self):
        self.vbo = GL.glGenBuffers(1)  # noqa
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(self.vertexes) * sizeof(c_float), self.vertexes, GL.GL_STATIC_DRAW)
        self.bayer_texture_id = GL.glGenTextures(1)  # noqa
        self.texture_id = self.bayer_texture_id
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.bayer_texture_id)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)  # In case width is odd
        bayer_w, bayer_h = self.tci.image.md.size_rpx
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, int(bayer_w))
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_PIXELS, self.tci.left - self.tci.left_pad)
        GL.glPixelStorei(GL.GL_UNPACK_SKIP_ROWS, self.tci.top - self.tci.top_pad)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,  # base mipmap
            GL.GL_R16,  # single channel
            self.padded_width,
            self.padded_height,
            0,  # border
            GL.GL_RED,
            GL.GL_UNSIGNED_SHORT,  # 16 bit
            self.bayer_array,
        )
        # We always want literally exact texel values, and no mipmapping
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        # Make all fetches outside the texture return transparent black
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_BORDER)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_BORDER)
        GL.glTexParameterfv(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_BORDER_COLOR, [0, 0, 0, 0])
        # Fill all three channels R, G, B with the one intensity
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_R, GL.GL_RED)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_G, GL.GL_RED)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_B, GL.GL_RED)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_A, GL.GL_ONE)

        # Create framebuffer
        if self.demosaic_framebuffer is None:
            self.demosaic_framebuffer = GL.glGenFramebuffers(1)  # noqa
            self.demosaic_vao = GL.glGenVertexArrays(1)  # noqa
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.demosaic_framebuffer)
        if self.demosaic_program is None:
            self.demosaic_program = compileProgram(
                compile_shader("vmg.glsl",
                               ["demosaic.vert"],
                               GL.GL_VERTEX_SHADER),
                compile_shader("vmg.glsl",
                               ["shared.frag", "demosaic.frag"],
                               GL.GL_FRAGMENT_SHADER),
            )
            GL.glUseProgram(self.demosaic_program)
            self.uDng.get_location(self.demosaic_program)
            self.uDng.set(self.tci.image)
        # Construct a second downsampled demosaic texture for zoomed out visualization
        self.demosaic_texture_id = GL.glGenTextures(1)  # noqa
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.demosaic_texture_id)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)  # In case width is odd
        # Allocate storage for level 0 of demosaic tile (RGB float)
        demosaic_w = max(1, self.padded_width)
        demosaic_h = max(1, self.padded_height)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,  # mip level
            GL.GL_RGBA16,  # internal format
            demosaic_w,
            demosaic_h,
            0,  # border
            GL.GL_RGBA,  # upload format
            GL.GL_UNSIGNED_SHORT,  # upload type
            None  # no initial data
        )

        self._update_demosaic_gl()

        # TODO: so much duplicated code
        self.boundary_ebo = GL.glGenBuffers(1)  # noqa
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.boundary_ebo)
        indices = numpy.array([
            0, 1, 3, 2,
        ], dtype=numpy.uint32)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)

        # Clean up
        # GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self.load_sync = GL.glFenceSync(GL.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        GL.glFlush()  # macOS probably
        logger.debug("DNG demosaic complete")

    def paint_gl(self, _view_state) -> bool:
        """Run in ui thread"""
        if not self.is_ready_for_display():
            return False
        if self.render_vao is None:
            self.render_vao = GL.glGenVertexArrays(1)  # noqa
            self.vao = self.render_vao
            GL.glBindVertexArray(self.render_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
            f_size = sizeof(c_float)
            GL.glVertexAttribPointer(  # normalized image coordinates
                1,  # attribute index
                2,  # size (#components)
                GL.GL_FLOAT,  # type
                False,  # normalized
                f_size * 4,  # stride (bytes)
                cast(0 * f_size, c_void_p),  # pointer offset
            )
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(  # texture coordinates
                2,  # attribute index
                2,  # size (#components)
                GL.GL_FLOAT,  # type
                False,  # normalized
                f_size * 4,  # stride (bytes)
                cast(2 * f_size, c_void_p),  # pointer offset
            )
            GL.glEnableVertexAttribArray(2)
        assert self.render_vao is not None
        GL.glBindVertexArray(self.render_vao)
        # TODO bind textures
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)  # Tile
        return True

    def paint_boundary(self):
        GL.glBindVertexArray(self.render_vao)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.boundary_ebo)
        GL.glDrawElements(GL.GL_LINE_LOOP, 4, GL.GL_UNSIGNED_INT, None)


def opx_for_rmp(rmp: tuple[int, int], size_rmp: tuple[int, int], orientation: ExifOrientation) -> tuple[int, int]:
    opx_x_rmp = numpy.eye(3, dtype=numpy.int32)  # default transform is identity
    w, h = size_rmp

    if orientation == ExifOrientation.FLIP_HORIZONTAL:  # 2
        opx_x_rmp = numpy.array([
            [-1, 0, w],
            [0, +1, 0],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.ROTATE_180:  # 3
        opx_x_rmp = numpy.array([
            [-1, 0, w],
            [0, -1, h],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.FLIP_VERTICAL:  # 4
        opx_x_rmp = numpy.array([
            [+1, 0, 0],
            [0, -1, h],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.FLIP_HORIZONTAL_ROTATE_90_CCW:  # 5
        opx_x_rmp = numpy.array([
            [0, +1, 0],
            [+1, 0, 0],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.ROTATE_90_CW:  # 6
        opx_x_rmp = numpy.array([
            [0, -1, h],
            [+1, 0, 0],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.FLIP_HORIZONTAL_ROTATE_90_CW:  # 7
        opx_x_rmp = numpy.array([
            [0, -1, h],
            [-1, 0, w],
            [0, 0, +1],
        ], dtype=numpy.int32)
    elif orientation == ExifOrientation.ROTATE_90_CCW:  # 8
        opx_x_rmp = numpy.array([
            [0, +1, 0],
            [-1, 0, w],
            [0, 0, +1],
        ], dtype=numpy.int32)

    result = opx_x_rmp @ (*rmp, 1)

    assert result[2] == 1
    assert result[0] >= 0
    assert result[1] >= 0
    assert result[0] <= max(w, h)
    assert result[1] <= max(w, h)
    return int(result[0]), int(result[1])
