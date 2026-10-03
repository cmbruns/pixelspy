from typing import OrderedDict, Callable

from OpenGL import GL

from vmg.interfaces import TiledImageLike, TileLike, RenderStateLike


class Uniform:
    def __init__(self, name: str, set_fn: Callable):
        self.name = name
        self.location = None
        self.set_fn = set_fn

    def get_location(self, program):
        self.location = GL.glGetUniformLocation(program, self.name)

    def set(self, *args):
        if self.location == -1:
            return
        self.set_fn(self.location, *args)


class Sampler2DUniform(Uniform):
    def __init__(self, name: str):
        super().__init__(name, GL.glUniform1i)

    def set(self, unit: int, texture_id: int):
        GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture_id)
        GL.glUniform1i(self.location, unit)


class UniformGroup:
    def __init__(self):
        self._index = OrderedDict()

    def __getitem__(self, key):
        return self._index[key]

    def add(self, uniform):
        self._index[uniform.name] = uniform

    def get_location(self, program: int):
        for u in self._index.values():
            u.get_location(program)


class DngUniforms(UniformGroup):
    def __init__(self):
        super().__init__()
        self.add(Uniform("black_level", GL.glUniform3f))
        self.add(Uniform("white_level", GL.glUniform3f))
        self.add(Uniform("as_shot_neutral", GL.glUniform3f))
        self.add(Uniform("lsr_X_wba", GL.glUniformMatrix3fv))
        self.add(Uniform("cfa_pattern", GL.glUniform4i))
        self.add(Uniform("demosaic_method", GL.glUniform1i))

    def set(self, image: TiledImageLike):
        self["black_level"].set(*image.md.black_level)
        self["white_level"].set(*image.md.white_level)
        self["as_shot_neutral"].set(*image.md.as_shot_neutral)
        self["lsr_X_wba"].set(1, True, image.md.lsr_X_wba)
        self["cfa_pattern"].set(*image.md.cfa_pattern)
        self["demosaic_method"].set(image.demosaic_method.value)


class TileUniforms(UniformGroup):
    def __init__(self):
        super().__init__()
        self.add(Uniform("tile_X_img", GL.glUniformMatrix3fv))
        self.add(Uniform("uv_bounds", GL.glUniform4f))
        self.add(Sampler2DUniform("tile"))

    def set(self, tile: TileLike, unit: int = 0):
        self["tile_X_img"].set(1, True, tile.tile_X_img)
        self["uv_bounds"].set(*tile.uv_bounds)
        self["tile"].set(unit, tile.texture_id)


class ViewerUniforms(UniformGroup):
    def __init__(self):
        super().__init__()
        self.add(Uniform("brightness", GL.glUniform1f))
        self.add(Uniform("pixelFilter", GL.glUniform1i))
        self.add(Uniform("show_cfa_colors", GL.glUniform1i))

    def set(self, state: RenderStateLike, image: TiledImageLike):
        self["brightness"].set(state.brightness + image.md.baseline_exposure)
        self["pixelFilter"].set(state.pixel_filter.value)
        self["show_cfa_colors"].set(state.show_cfa_colors)


class NumeralUniforms(UniformGroup):
    def __init__(self):
        super().__init__()
        self.add(Uniform("channel_count", GL.glUniform1i))
        self.add(Uniform("data_max", GL.glUniform1f))
        self.add(Uniform("format_max", GL.glUniform1f))
        self.add(Uniform("pixel_numerals", GL.glUniform1i))
        self.add(Uniform("rotation", GL.glUniformMatrix2fv))

    def set(self, state: RenderStateLike, image: TiledImageLike):
        # state
        self["pixel_numerals"].set(state.pixel_numerals.value)
        # image
        self["channel_count"].set(image.md.channel_count)
        self["data_max"].set(image.md.data_max)
        self["format_max"].set(image.md.upper_bound)
        self["rotation"].set(1, False, image.md.rpx_R_opx)


class PanoUniforms(UniformGroup):
    def __init__(self):
        super().__init__()
        self.add(Uniform("window_size", GL.glUniform2i))
        self.add(Uniform("window_zoom", GL.glUniform1f))
        self.add(Uniform("display_projection", GL.glUniform1i))
        self.add(Uniform("geo_rot_usr", GL.glUniformMatrix3fv))
        self.add(Uniform("pcm_rot_geo", GL.glUniformMatrix3fv))
        self.add(Uniform("input_format", GL.glUniform1i))
        self.add(Uniform("df_fov_radians", GL.glUniform1f))
        self.add(Uniform("df_lens_rot_radians", GL.glUniform1f))
        self.add(Uniform("df_front_center_scale", GL.glUniform4f))
        self.add(Uniform("df_rear_center_scale", GL.glUniform4f))
        # NOT render_pass, because it's set with a different cadence

    def set(self, state: RenderStateLike, image: TiledImageLike):
        self["window_size"].set(*[int(x) for x in state.window_size])
        self["window_zoom"].set(state.zoom)
        self["display_projection"].set(state.display_projection.value)
        self["geo_rot_usr"].set(1, True, state.geo_rot_usr)
        self["pcm_rot_geo"].set(1, True, image.md.pcm_R_geo)
        self["input_format"].set(image.md.input_panorama_format.value)
        self["df_fov_radians"].set(image.md.inscribed_fov_radians)
        self["df_lens_rot_radians"].set(image.md.df_lens_rot_radians)
        self["df_front_center_scale"].set(*image.md.df_front_center_scale)
        self["df_rear_center_scale"].set(*image.md.df_rear_center_scale)
