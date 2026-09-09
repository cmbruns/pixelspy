import traceback

import abc
import logging
from PIL import Image

import numpy
from OpenGL import GL
from OpenGL.GL.shaders import compileProgram, compileShader
from OpenGL.GL.EXT.texture_filter_anisotropic import GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT, GL_TEXTURE_MAX_ANISOTROPY_EXT

from vmg.tiled_image import DngTile, Tile
from vmg.interfaces import RenderStateLike, TiledImageLike, InputFormat, PhotometricScale, TileLike, ShaderProgramLike
from vmg.resources import resource_stream, resource_string
from vmg.shader_exception import compile_shader
from vmg.uniforms import Sampler2DUniform, DngUniforms, TileUniforms, ViewerUniforms, NumeralUniforms, PanoUniforms, \
    Uniform

logger = logging.getLogger(__name__)


class IImageShader(abc.ABC):
    @abc.abstractmethod
    def initialize_gl(self) -> None:
        pass

    @abc.abstractmethod
    def paint_gl(self, state: RenderStateLike, texture) -> None:
        pass


class NumeralShader(IImageShader):
    """Paints numeric intensity values onto very zoomed in pixels"""
    def __init__(self):
        self.program = None
        self.numeral_texture_id = None
        self.uTile = Sampler2DUniform("tile")
        self.uNumerals = Sampler2DUniform("numerals")
        self.uNdc_X_opx = Uniform("ndc_X_opx", GL.glUniformMatrix3fv)
        self.uNumeralData = NumeralUniforms()
        with resource_stream("vmg.images", "hex_digits_df.png") as df:
            numeral_pil = Image.open(df)
            self.numeral_array = numpy.array(numeral_pil)

    def initialize_gl(self) -> None:
        # Texturize numeral signed distance field
        self.numeral_texture_id = GL.glGenTextures(1)  # noqa
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.numeral_texture_id)
        assert len(self.numeral_array.shape) == 2  # monochrome
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_G, GL.GL_RED)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_B, GL.GL_RED)
        h, w = self.numeral_array.shape
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RED,
            w, h,
            0,
            GL.GL_RED,
            GL.GL_UNSIGNED_BYTE,
            self.numeral_array,
        )
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_BORDER)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_BORDER)

        self.program = compileProgram(
            compile_shader("vmg.glsl",
                           ["tile_rect.vert"], GL.GL_VERTEX_SHADER),
            compile_shader("vmg.glsl",
                           [
                               "shared.frag",
                               "numeral.frag",
                           ], GL.GL_FRAGMENT_SHADER),
        )
        for u in (
            self.uTile,
            self.uNumerals,
            self.uNdc_X_opx,
            self.uNumeralData,
        ):
            u.get_location(self.program)

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        if self.program is None:
            self.initialize_gl()
        GL.glUseProgram(self.program)
        self.uNdc_X_opx.set(1, True, state.ndc_xform_opx())
        self.uNumerals.set(1, self.numeral_texture_id)
        self.uNumeralData.set(state, image)
        for tile in image.tiles:
            assert tile.texture_id is not None
            self.uTile.set(0, tile.texture_id)
            assert tile.vao is not None
            # TODO: this is for standard photos only
            GL.glBindVertexArray(tile.vao)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)


class NumeralSphereShader(IImageShader):
    """Paints numeric intensity values onto very zoomed in pixels"""
    def __init__(self):
        self.program = None
        self.numeral_texture_id = None
        self.uTileData = TileUniforms()
        self.uNumerals = Sampler2DUniform("numerals")
        self.uNumeralData = NumeralUniforms()
        self.uPano = PanoUniforms()
        self.uRenderPass = Uniform("render_pass", GL.glUniform1i)
        with resource_stream("vmg.images", "hex_digits_df.png") as df:
            numeral_pil = Image.open(df)
            self.numeral_array = numpy.array(numeral_pil)

    def initialize_gl(self) -> None:
        # Texturize numeral signed distance field
        self.numeral_texture_id = GL.glGenTextures(1)  # noqa
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.numeral_texture_id)
        assert len(self.numeral_array.shape) == 2  # monochrome
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_G, GL.GL_RED)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_SWIZZLE_B, GL.GL_RED)
        h, w = self.numeral_array.shape
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RED,
            w, h,
            0,
            GL.GL_RED,
            GL.GL_UNSIGNED_BYTE,
            self.numeral_array,
        )
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_BORDER)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_BORDER)

        self.program = compileProgram(
            compile_shader("vmg.glsl",
                           ["sphere.vert"], GL.GL_VERTEX_SHADER),
            compile_shader("vmg.glsl",
                           [
                               "shared.frag",
                               "numeral_sphere.frag",
                           ], GL.GL_FRAGMENT_SHADER),
        )
        for u in (
            self.uTileData,
            self.uNumerals,
            self.uNumeralData,
            self.uPano,
            self.uRenderPass,
        ):
            u.get_location(self.program)

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike, render_pass=1) -> None:
        if self.program is None:
            self.initialize_gl()
        GL.glUseProgram(self.program)
        self.uNumerals.set(1, self.numeral_texture_id)
        self.uNumeralData.set(state, image)
        self.uPano.set(state, image)
        self.uRenderPass.set(render_pass)
        for tile in image.tiles:
            assert tile.texture_id is not None
            self.uTileData.set(tile)
            tile.initialize_arrays()
            assert tile.vao is not None
            GL.glBindVertexArray(tile.vao)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)


class RectangularTileShader(IImageShader, ShaderProgramLike):
    def __init__(self):
        self.shader = None
        self.ndc_x_opx_location = None
        self.pixelFilter_location = None
        self.sel_rect_opx_location = None
        self.background_color_location = None
        self.opx_scale_qwn_location = None
        self.tile_X_img_location = -1
        self.uv_bounds_location = -1
        self.uDng = DngUniforms()
        self.brightness = Uniform("brightness", GL.glUniform1f)
        self.input_is_linear = Uniform("input_is_linear", GL.glUniform1i)
        self.background_color = [0.5, 0.5, 0.5, 0.5]
        self.box_shader = SelectionBoxShader()
        self.tile_boundary_shader = TileBoundaryShader()
        self.numeral_shader = NumeralShader()

    def initialize_gl(self) -> None:
        try:
            vertex_shader = compileShader(resource_string(
                "vmg.glsl", "tile_rect.vert", ), GL.GL_VERTEX_SHADER)
            fragment_shader = compile_shader("vmg.glsl", [
                "shared.frag", "tile_rect.frag"], GL.GL_FRAGMENT_SHADER)
            self.shader = GL.glCreateProgram()
            GL.glAttachShader(self.shader, vertex_shader)
            GL.glAttachShader(self.shader, fragment_shader)
            GL.glLinkProgram(self.shader)
            self.ndc_x_opx_location = GL.glGetUniformLocation(self.shader, "ndc_X_opx")
            self.sel_rect_opx_location = GL.glGetUniformLocation(self.shader, "sel_rect_opx")
            self.background_color_location = GL.glGetUniformLocation(self.shader, "background_color")
            self.pixelFilter_location = GL.glGetUniformLocation(self.shader, "pixel_filter")
            self.opx_scale_qwn_location = GL.glGetUniformLocation(self.shader, "opx_scale_qwn")
            self.uDng.get_location(self.shader)
            self.brightness.get_location(self.shader)
            self.input_is_linear.get_location(self.shader)
            self.box_shader.initialize_gl()
            self.tile_boundary_shader.initialize_gl()
            self.numeral_shader.initialize_gl()
        except BaseException as exc:
            traceback.print_exception(exc)
            raise

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        GL.glUseProgram(self.shader)
        GL.glUniform1i(self.pixelFilter_location, state.pixel_filter.value)
        GL.glUniform4i(self.sel_rect_opx_location, *state.sel_rect.left_top_right_bottom)
        GL.glUniform4f(self.background_color_location, *state.background_color)
        GL.glUniformMatrix3fv(self.ndc_x_opx_location, 1, True, state.ndc_xform_opx())
        GL.glUniform1f(self.opx_scale_qwn_location, state.opx_scale_qwn())
        self.uDng.set(image)
        self.brightness.set(state.brightness + image.md.baseline_exposure)
        self.input_is_linear.set(image.md.photometric_scale == PhotometricScale.LINEAR)
        state.anisotropic_filtering = False
        image.paint_gl(self, state)
        do_numerals = state.opx_scale_qwn() < 0.2
        if do_numerals:
            self.numeral_shader.paint_gl(state, image)
        if state.show_tile_boundaries:
            self.tile_boundary_shader.paint_gl(state, image)
        self.box_shader.paint_gl(state, image)


class RectangularDngShader(IImageShader):
    uDng = DngUniforms()
    uViewer = ViewerUniforms()

    def __init__(self):
        self.shader = None
        self.ndc_x_opx_location = None
        self.sel_rect_opx_location = None
        self.background_color_location = None
        self.opx_scale_qwn_location = None
        self.background_color = [0.5, 0.5, 0.5, 0.5]
        self.box_shader = SelectionBoxShader()
        self.uBayerTile = Sampler2DUniform("bayer_tile")
        self.uDemosaicTile = Sampler2DUniform("demosaic_tile")
        self.tile_X_img_location = -1
        self.uv_bounds_location = -1
        self.tile_boundary_shader = TileBoundaryShader()
        self.numeral_shader = NumeralShader()

    def initialize_gl(self) -> None:
        try:
            vertex_shader = compileShader(resource_string(
                "vmg.glsl", "tile_rect.vert",
            ), GL.GL_VERTEX_SHADER)
            fragment_shader = compile_shader(
                "vmg.glsl", [
                    "shared.frag",
                    "tile_rect_dng.frag",
                ], GL.GL_FRAGMENT_SHADER)
            self.shader = GL.glCreateProgram()
            GL.glAttachShader(self.shader, vertex_shader)
            GL.glAttachShader(self.shader, fragment_shader)
            GL.glLinkProgram(self.shader)
            self.ndc_x_opx_location = GL.glGetUniformLocation(self.shader, "ndc_X_opx")
            self.sel_rect_opx_location = GL.glGetUniformLocation(self.shader, "sel_rect_opx")
            self.background_color_location = GL.glGetUniformLocation(self.shader, "background_color")
            self.opx_scale_qwn_location = GL.glGetUniformLocation(self.shader, "opx_scale_qwn")
            self.tile_X_img_location = GL.glGetUniformLocation(self.shader, "tile_X_img")
            self.uv_bounds_location = GL.glGetUniformLocation(self.shader, "uv_bounds")
            for uniform in (
                    self.uBayerTile,
                    self.uDemosaicTile,
                    self.uDng,
                    self.uViewer,
            ):
                uniform.get_location(self.shader)
            self.box_shader.initialize_gl()
            self.tile_boundary_shader.initialize_gl()
            self.numeral_shader.initialize_gl()
        except BaseException as exc:
            traceback.print_exception(exc)
            raise

    def paint_image(self, state: RenderStateLike, image: TiledImageLike):
        self.uDng.set(image)
        self.uViewer.set(state, image)
        is_complete = True  # start optimistic
        for tile in image.tiles:
            assert isinstance(tile, DngTile)
            if not self.paint_tile(tile):
                is_complete = False
        if is_complete:
            image.set_display_complete()

    def paint_tile(self, tile: DngTile) -> bool:
        assert isinstance(tile, DngTile)
        GL.glUniformMatrix3fv(self.tile_X_img_location, 1, True, tile.tile_X_img)
        GL.glUniform4f(self.uv_bounds_location, *tile.uv_bounds)
        self.uDemosaicTile.set(1, tile.demosaic_texture_id)
        self.uBayerTile.set(0, tile.bayer_texture_id)
        if not tile.is_ready_for_display():
            return False
        tile.initialize_arrays()
        GL.glBindVertexArray(tile.render_vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        return True

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        GL.glUseProgram(self.shader)
        self.uViewer.set(state, image)
        GL.glUniform4i(self.sel_rect_opx_location, *state.sel_rect.left_top_right_bottom)
        GL.glUniform4f(self.background_color_location, *state.background_color)
        GL.glUniformMatrix3fv(self.ndc_x_opx_location, 1, True, state.ndc_xform_opx())
        GL.glUniform1f(self.opx_scale_qwn_location, state.opx_scale_qwn())
        self.paint_image(state, image)
        do_numerals = state.opx_scale_qwn() < 0.2
        if do_numerals:
            self.numeral_shader.paint_gl(state, image)
        if state.show_tile_boundaries:
            self.tile_boundary_shader.paint_gl(state, image)
        self.box_shader.paint_gl(state, image)


class SelectionBoxShader(IImageShader):
    def __init__(self):
        self.shader = None
        self.ndc_x_opx_location = None
        self.sel_rect_opx_location = None
        self.background_color_location = None
        self.opx_scale_qwn_location = None

    def initialize_gl(self) -> None:
        vertex_shader = compileShader(resource_string(
            "vmg.glsl", "sel_box.vert", ), GL.GL_VERTEX_SHADER)
        fragment_shader = compileShader(
            resource_string("vmg.glsl", "shared.frag") +
            resource_string("vmg.glsl", "sel_box.frag"),
            GL.GL_FRAGMENT_SHADER)
        self.shader = GL.glCreateProgram()
        GL.glAttachShader(self.shader, vertex_shader)
        GL.glAttachShader(self.shader, fragment_shader)
        GL.glLinkProgram(self.shader)
        self.ndc_x_opx_location = GL.glGetUniformLocation(self.shader, "ndc_X_opx")
        self.sel_rect_opx_location = GL.glGetUniformLocation(self.shader, "sel_rect_opx")
        self.background_color_location = GL.glGetUniformLocation(self.shader, "background_color")
        self.opx_scale_qwn_location = GL.glGetUniformLocation(self.shader, "opx_scale_qwn")

    def paint_gl(self, state: RenderStateLike, texture) -> None:
        GL.glUseProgram(self.shader)
        GL.glUniform4i(self.sel_rect_opx_location, *state.sel_rect.left_top_right_bottom)
        GL.glUniform4f(self.background_color_location, *state.background_color)
        GL.glUniformMatrix3fv(self.ndc_x_opx_location, 1, True, state.ndc_xform_opx())
        GL.glUniform1f(self.opx_scale_qwn_location, state.opx_scale_qwn())
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 10)


class SphericalShader(IImageShader, ShaderProgramLike):
    def __init__(self):
        self.shader = None
        self.uTile = TileUniforms()
        self.uPano = PanoUniforms()
        self.uDng = DngUniforms()
        self.pixelFilter_location = None
        self.tile_X_img_location = None
        self.uv_bounds_location = None
        self.brightness = Uniform("brightness", GL.glUniform1f)
        self.input_is_linear = Uniform("input_is_linear", GL.glUniform1i)
        self.uRenderPass = Uniform("render_pass", GL.glUniform1i)
        self.numeral_shader = NumeralSphereShader()

    def initialize_gl(self) -> None:
        try:
            vertex_shader = compileShader(resource_string(
                "vmg.glsl", "sphere.vert", ), GL.GL_VERTEX_SHADER)
            fragment_shader = compileShader(
                resource_string("vmg.glsl", "shared.frag") +
                resource_string("vmg.glsl", "sphere.frag"),
                GL.GL_FRAGMENT_SHADER)
        except BaseException as exc:
            logger.error(exc)
            raise
        self.shader = GL.glCreateProgram()
        GL.glAttachShader(self.shader, vertex_shader)
        GL.glAttachShader(self.shader, fragment_shader)
        GL.glLinkProgram(self.shader)
        self.pixelFilter_location = GL.glGetUniformLocation(self.shader, "pixelFilter")
        self.tile_X_img_location = GL.glGetUniformLocation(self.shader, "tile_X_img")
        self.uv_bounds_location = GL.glGetUniformLocation(self.shader, "uv_bounds")
        for u in (
                self.brightness,
                self.input_is_linear,
                self.uRenderPass,
                self.uPano,
                self.uTile,
                self.uDng,
        ):
            u.get_location(self.shader)
        self.numeral_shader.initialize_gl()

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        # both nearest and catmull-rom use nearest at the moment.
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_MIRRORED_REPEAT)
        f_largest = GL.glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)  # noqa
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, f_largest)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)

        GL.glUseProgram(self.shader)
        self.uPano.set(state, image)
        GL.glUniform1i(self.pixelFilter_location, state.pixel_filter.value)
        self.uDng.set(image)
        self.brightness.set(state.brightness + image.md.baseline_exposure)
        self.input_is_linear.set(image.md.photometric_scale == PhotometricScale.LINEAR)
        self.uRenderPass.set(1)
        self.paint_image(image)
        do_numerals = state.opx_scale_qwn() < 0.2
        if do_numerals:
            self.numeral_shader.paint_gl(state, image, render_pass=1)
        if image.md.input_format == InputFormat.DUAL_FISHEYE:
            # second render pass for rear lens
            GL.glUseProgram(self.shader)
            self.uRenderPass.set(2)
            self.paint_image(image)
            if do_numerals:
                self.numeral_shader.paint_gl(state, image, render_pass=2)

    def paint_tile(self, tile: TileLike):
        if not tile.is_ready_for_display():
            return False
        tile.initialize_arrays()
        self.uTile.set(tile)
        GL.glBindVertexArray(tile.vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        return True

    def paint_image(self, image: TiledImageLike):
        is_complete = True  # start optimistic
        for tile in image.tiles:
            if not self.paint_tile(tile):
                is_complete = False
            if is_complete:
                image.set_display_complete()


class SphericalDngShader(IImageShader):
    uBayerTile = Sampler2DUniform("bayer_tile")
    uDemosaicTile = Sampler2DUniform("demosaic_tile")
    uViewer = ViewerUniforms()
    uPano = PanoUniforms()
    uDng = DngUniforms()
    uRenderPass = Uniform("render_pass", GL.glUniform1i)
    uTile = TileUniforms()

    def __init__(self):
        self.shader = None
        self.numeral_shader = NumeralSphereShader()

    def initialize_gl(self) -> None:
        try:
            self.shader = compileProgram(
                compileShader(
                    resource_string("vmg.glsl", "sphere.vert"),
                    GL.GL_VERTEX_SHADER),
                compileShader(
                    resource_string("vmg.glsl", "shared.frag") +
                    resource_string("vmg.glsl", "sphere_dng.frag"),
                    GL.GL_FRAGMENT_SHADER),
            )
            for uniform in (
                self.uViewer,
                self.uPano,
                self.uBayerTile,
                self.uDemosaicTile,
                self.uRenderPass,
                self.uDng,
                self.uTile,
            ):
                uniform.get_location(self.shader)
        except BaseException as exc:
            logger.error(exc)
            raise

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        if self.shader is None:
            self.initialize_gl()
        GL.glUseProgram(self.shader)
        self.uViewer.set(state, image)
        self.uPano.set(state, image)
        self.uDng.set(image)
        # Be selective about numeral painting to avoid tile bounary artifacts at lower zoom
        do_numerals = state.opx_scale_qwn() < 0.2
        self.uRenderPass.set(1)
        self._paint_one_pass(image)
        if do_numerals:
            self.numeral_shader.paint_gl(state, image, render_pass=1)
        if image.md.input_format == InputFormat.DUAL_FISHEYE:
            # second render pass for rear lens
            GL.glUseProgram(self.shader)
            self.uRenderPass.set(2)
            self._paint_one_pass(image)
            if do_numerals:
                self.numeral_shader.paint_gl(state, image, render_pass=2)

    def paint_tile(self, tile: DngTile) -> bool:
        assert isinstance(tile, DngTile)
        self.uDemosaicTile.set(1, tile.demosaic_texture_id)
        self.uBayerTile.set(0, tile.bayer_texture_id)
        if not tile.is_ready_for_display():
            return False
        tile.initialize_arrays()
        self.uTile.set(tile)
        GL.glBindVertexArray(tile.render_vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        return True

    def _paint_one_pass(self, image: TiledImageLike):
        is_complete = True
        for tile in image.tiles:
            assert isinstance(tile, DngTile)
            if not self.paint_tile(tile):
                is_complete = False
        if is_complete:
            image.set_display_complete()


class TileBoundaryShader(IImageShader):
    def __init__(self):
        self.program = None
        self.uViewport = Uniform("uViewportSize", GL.glUniform2f)
        self.ndc_X_opx = Uniform("ndc_X_opx", GL.glUniformMatrix3fv)

    def initialize_gl(self) -> None:
        self.program = compileProgram(
            compile_shader("vmg.glsl",
                           ["tile_rect.vert"], GL.GL_VERTEX_SHADER),
            compile_shader("vmg.glsl",
                           ["tile_boundary.geom"], GL.GL_GEOMETRY_SHADER),
            compile_shader("vmg.glsl",
                           ["tile_boundary.frag"], GL.GL_FRAGMENT_SHADER),
        )
        self.uViewport.get_location(self.program)
        self.ndc_X_opx.get_location(self.program)

    def paint_gl(self, state: RenderStateLike, image: TiledImageLike) -> None:
        GL.glUseProgram(self.program)
        self.uViewport.set(*state.window_size)
        self.ndc_X_opx.set(1, True, state.ndc_xform_opx())
        for tile in image.tiles:
            assert isinstance(tile, Tile)
            tile.paint_boundary()
