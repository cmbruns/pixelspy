#version 410

// Set line numbers correctly for this file
#line 5 0

const float PI = 3.1415926535897932384626433832795;

// Keep these values in sync with PixelFilter enum in image_widget_gl.py
const int FILTER_NEAREST = 1;
const int FILTER_CATROM = 2;

// Keep these constants in sync with display_projection.py
const int GNOMONIC_DISPLAY_PROJECTION = 0;
const int STEREOGRAPHIC_DISPLAY_PROJECTION = 1;
const int AZ_EQ_DISPLAY_PROJECTION = 2;
const int EQUIRECT_DISPLAY_PROJECTION = 3;

// Keep these in sync with image_data.py
const int EQUIRECT_INPUT_FORMAT = 0;
const int DUAL_FISHEYE_INPUT_FORMAT = 1;
const int PERSPECTIVE_INPUT_FORMAT = 2;
const int SINUSOIDAL_INPUT_FORMAT = 3;

// 
const int NUMERALS_HEXADECIMAL = 1;
const int NUMERALS_DECIMAL = 2;
const int NUMERALS_NONE = 3;

// Sync with DemosaicMethod vmg.interfaces.py
const int DEMOSAIC_LANCZOS_7X7 = 1;
const int DEMOSAIC_MALVAR_HE_CUTLER = 2;
const int DEMOSAIC_LANCZOS_5x5_GREEN_MEDIAN_CHROMA = 3;
const int DEMOSAIC_BILINEAR = 4;

const vec3 INVALID_USR = vec3(0);

struct TexCoordPair {
    vec2 front_tc;
    vec2 rear_tc;
    float front_bias;  // range 0-1
};

struct TexCoordAlpha {
    vec2 p_rtc;  // full image texture coordinate
    float alpha;  // blending parameter
};

// Colorize raw grayscale bayer mosaic texel intensity
vec4 bayer_tint(
        ivec2 texel_rtc,  // must be full image texel index, because parity
        vec4 bayer_color,  // raw grayscale bayer mosaic intensity
        ivec4 cfa_pattern
) {
    bool rowEven = (texel_rtc.y & 1) == 0;
    bool colEven = (texel_rtc.x & 1) == 0;
    // RGGB Bayer pattern
    vec3 mask = vec3(1);

    const vec3[3] rgb_tint = vec3[3](
        vec3(1.0, 0.2, 0.1),  // red
        vec3(0.1, 1.0, 0.1),  // green
        vec3(0.1, 0.2, 1.0)  // blue
    );
    // prevent colors from getting completely washed out
    const vec3[3] rgb_pure = vec3[3](
        vec3(1.0, 0.0, 0.0),  // red
        vec3(0.0, 1.0, 0.0),  // green
        vec3(0.0, 0.0, 1.0)  // blue
    );

    vec3 pure;
    if      ( rowEven &&  colEven) {
        mask = rgb_tint[cfa_pattern[0]];
        pure = rgb_pure[cfa_pattern[0]];
    }  // red
    else if ( rowEven && !colEven) {
        mask = rgb_tint[cfa_pattern[1]];
        pure = rgb_pure[cfa_pattern[0]];
    }  // green
    else if (!rowEven &&  colEven) {
        mask = rgb_tint[cfa_pattern[2]];
        pure = rgb_pure[cfa_pattern[0]];
    }  // green
    else /* if (!rowEven && !colEven) */ {
        mask = rgb_tint[cfa_pattern[3]];
        pure = rgb_pure[cfa_pattern[0]];
    }  // blue
    return mix(bayer_color * vec4(mask, 1), vec4(0.5 * pure, 1), 0.0);
}

vec4 equirect_color(sampler2D image, vec2 tex_coord)
{
    // Use explicit gradients, to preserve anisotropic filtering during mipmap lookup
    vec2 dpdx = dFdx(tex_coord);
    vec2 dpdy = dFdy(tex_coord);

    if (dpdx.x > 0.5) dpdx.x -= 1; // use "repeat" wrapping on gradient
    if (dpdx.x < -0.5) dpdx.x += 1;
    if (dpdy.x > 0.5) dpdy.x -= 1; // use "repeat" wrapping on gradient
    if (dpdy.x < -0.5) dpdy.x += 1;

    return textureGrad(image, tex_coord, dpdx, dpdy);
}

vec4 catrom_weights(float t) {
    return 0.5 * vec4(
        -1*t*t*t + 2*t*t - 1*t,  // P0 weight
        3*t*t*t - 5*t*t + 2,  // P1 weight
        -3*t*t*t + 4*t*t + 1*t,  // P2 weight
        1*t*t*t - 1*t*t);  // P3 weight
}

vec4 catrom(sampler2D image, vec2 textureCoordinate, bool wrap) {
    vec2 texel = textureCoordinate * textureSize(image, 0) - vec2(0.5);
    ivec2 texel1 = ivec2(floor(texel));
    vec2 param = texel - texel1;
    vec4 weightsX = catrom_weights(param.x);
    vec4 weightsY = catrom_weights(param.y);
    vec4 combined = vec4(0);
    float rgb_weight = 0;  // for pseudo pre/post multiply alpha
    for (int y = 0; y < 4; ++y) {
        float wy = weightsY[y];
        for (int x = 0; x < 4; ++x) {
            float wx = weightsX[x];
            vec2 texel2 = vec2(x , y) + texel1 - vec2(0.5);
            vec2 tc = texel2 / textureSize(image, 0);
            vec4 rgba;
            if (wrap)
                rgba = equirect_color(image, tc);  // TODO: needs lod zero maybe?
            else
                rgba = textureLod(image, tc, 0);  // hard code to zero for Mac
            rgb_weight += wx * wy * rgba.a;
            combined += wx * wy * vec4(rgba.rgb * rgba.a, rgba.a);  // premultiply alpha
        }
    }
    if (rgb_weight > 0)
        combined.rgb /= rgb_weight;  // un-premultiply alpha
    return combined;
}

vec2 equirect_tex_coord(vec3 dir)
{
    float r = length(dir.xz);
    float latitude = -atan(dir.y, r); // radians range [-pi/2, +pi/2]
    float longitude = atan(dir.x, -dir.z); // radians  range [-pi, pi]
    float tx = 0.5 * longitude / PI + 0.5; // range [0-1]
    float ty = latitude / PI + 0.5; // range [0-1]
    vec2 tex_coord = vec2(tx, ty);
    return tex_coord;
}

vec2 sinusoidal_tex_coord(vec3 dir)
{
    float r = length(dir.xz);
    float latitude = -atan(dir.y, r);
    float longitude = atan(dir.x, -dir.z); // range [0-1]
    float tx_sinusoidal = cos(latitude) * 0.5 * longitude / PI + 0.5;
    float ty = latitude / PI + 0.5; // range [0-1]
    vec2 tex_coord = vec2(tx_sinusoidal, ty);
    return tex_coord;
}

TexCoordPair dual_fisheye_tex_coord(
        vec3 p_pcm,
        float fov_radians,
        float lens_rot_radians,
        vec4 front_center_scale,
        vec4 rear_center_scale)
{
    // input vector space is 3D unit sphere, x-right, y-up, z-back (i.e. -Z forward/center)
    // range [-1, +1]
    vec3 p_sph_front = p_pcm;
    vec3 p_sph_rear = p_pcm * vec3(-1, 1, -1);  // rotate 180 about Y/up

    // The two lenses can be slightly misaligned by an axial rotation
    float crot = cos(lens_rot_radians/2.0);
    float srot = sin(lens_rot_radians/2.0);
    mat2 rot_nfish = mat2(  // half rotation adjustment in the left/front fisheye
        crot, srot,
        -srot, crot);

    // Amount the two lenses overlap determines the blending region
    float z_limit = 0.4 * sin(fov_radians - radians(180));  // angular overlap region in z direction
    float front_bias = smoothstep(+z_limit, -z_limit, p_sph_front.z);

    // normalized fisheye space 2D x-right, y-up, range [-1, +1]
    float radius_nfish_front = acos(-p_sph_front.z) / fov_radians;  // TODO: nonlinear calibration
    float radius_nfish_rear = acos(-p_sph_rear.z) / fov_radians;  // TODO: nonlinear calibration
    vec2 p_nfish_front = (normalize(p_sph_front.xy) * radius_nfish_front) * rot_nfish;
    vec2 p_nfish_rear = (normalize(p_sph_rear.xy) * radius_nfish_rear) * rot_nfish;

    // output gl texture coordinates 2D x-right, y-down, range[0, 1]
    vec2 p_front_tc = front_center_scale.xy + p_nfish_front * front_center_scale.zw * vec2(1, -1);  // Translate and scale
    vec2 p_rear_tc = rear_center_scale.xy + p_nfish_rear * rear_center_scale.zw * vec2(1, -1);

    if (lessThan(abs(p_nfish_front), vec2(1.0)) != bvec2(true))
        front_bias = 0.0;
    if (lessThan(abs(p_nfish_rear), vec2(1.0)) != bvec2(true))
        front_bias = 1.0;

    return TexCoordPair(p_front_tc, p_rear_tc, front_bias);
}

vec4 nearest_nowrap(sampler2D image, vec2 tc) {
    return texture(image, tc);
}

vec4 nearest_wrap(sampler2D image, vec2 tc) {
    return equirect_color(image, tc);
}

vec4 clip_n_filter(sampler2D image, vec2 tc, int pixelFilter, bool wrap)
{
    // clip to image boundary
    if (tc.x < 0 || tc.y < 0 || tc.x > 1 || tc.y > 1) {
        return vec4(0);
    }

    float mipmapLevel = textureQueryLod(image, tc).x;
    if (mipmapLevel > 0 || pixelFilter == FILTER_NEAREST)
    {
        if (wrap)
            return equirect_color(image, tc);
        else
            return texture(image, tc);
    }
    else {
        return catrom(image, tc, wrap);
    }
}

bool azeqd_valid(vec2 xy) {
    return dot(xy, xy) < PI * PI;
}

vec3 azimuthal_equidistant_xyz(vec2 xy) {  // finite distance to edges
    float d = sqrt(dot(xy, xy));
    float sdd = sin(d) / d;
    float cd = cos(d);
    return vec3(xy.x * sdd, xy.y * sdd, -cd);
}

bool equirect_valid(vec2 xy) {
    if (abs(xy.y) > PI / 2)
        return false;
    return true;
}

vec3 equirect_xyz(vec2 xy) {
    float lat = xy.y;
    float lon = xy.x;
    float clat = cos(lat);
    return vec3(clat * sin(lon), sin(lat), -clat * cos(lon));
}

vec3 gnomonic_xyz(vec2 xy) {  // pinhole camera
    float d = sqrt(dot(xy, xy) + 1);
    return vec3(xy.x, xy.y, -1) / d;
}

float linear_from_srgb(in float srgb)
{
    if (srgb <= 0.04045)
        return srgb / 12.92;
    else
        return pow((srgb + 0.055) / 1.055, 2.4);
}

vec4 linear_from_srgb(in vec4 srgb)
{
    return vec4(
        linear_from_srgb(srgb.r),
        linear_from_srgb(srgb.g),
        linear_from_srgb(srgb.b),
        srgb.a);
}

vec3 linear_srgb_from_sensor(
        vec3 sensor,
        vec3 black_level,
        vec3 white_level,
        vec3 as_shot_neutral,
        mat3 lsr_X_wba)
{
    vec3 color = sensor;

    // black level sns -> bkc
    color.rgb = max(color.rgb - black_level, vec3(0));
    // white level bkc -> rfv (camera "linear reference value" in DNG spec)
    color.rgb = min(color.rgb/(white_level - black_level), vec3(1));

    // rfv -> wba  white balanced
    color.rgb /= as_shot_neutral;
    // clip so highlights are neutral, to avoid magenta sun
    color.rgb = min(color.rgb, vec3(1));

    // convert to linear sRGB
    color.rgb = lsr_X_wba * color.rgb;

    return color;
}

mat2 texel_rotation(vec2 tc)
{
    vec2 dx = dFdx(tc);
    vec2 dy = dFdy(tc);
    mat2 J = mat2(dx, dy);
    vec2 ex = normalize(dx);
    vec2 ey = normalize(dy);
    mat2 R = mat2(ex.yx, ey.yx);
    // The general rotation is far too noisy
    // so just see if a 180 degree rotation might help
    float trace = R[1][1] + R[0][0];
    if (trace > 0) return mat2(1);
    else return mat2(-1, 0, 0, -1);  // still too noisy!
}

vec4 numeral_color(
        vec2 p_ttc,
        sampler2D tile,
        sampler2D numerals,
        int channel_count,
        float format_max,
        float data_max,
        mat2 rotation,
        int pixel_numerals)
{
    const float left_margin   = 0.1;
    const float right_margin  = 0.9;
    const float top_margin    = 0.9;
    const float bottom_margin = 0.1;

    if (pixel_numerals == NUMERALS_NONE) {
        return vec4(0);
    }

    float lod = textureQueryLod(tile, p_ttc).y;
    float fade = smoothstep(-5.0, -8.0, lod);  // smoothly blend in at high zoom
    if (fade <= 0) return vec4(0);

    // pixel-relative texture coordinates
    vec2 texture_pixels = textureSize(tile, 0);
    vec2 local_coords = fract(texture_pixels * p_ttc);

    // additional rotation if texels are rotated on screen
    // TOO NOISY, need analytic jacobians...
    mat2 R = mat2(1); // texel_rotation(p_ttc);

    // rotate numbers
    local_coords -= vec2(0.5);
    local_coords  = R * rotation * local_coords;
    local_coords += vec2(0.5);

    // Trim to sub-region
    if (local_coords.x <= left_margin ||
        local_coords.x >= right_margin ||
        local_coords.y >= top_margin   ||
        local_coords.y <= bottom_margin)
        return vec4(0);

    // How many digits to show?
    float base = 16.0;  // prepare for hex display
    if (pixel_numerals == NUMERALS_DECIMAL) {
        base = 10.0;
    }
    float num_digits = ceil(log(format_max) / log(base));

    // Width of a single digit, in image pixels
    float w = (right_margin - left_margin) / num_digits;
    float h = w * 1.7; // number aspect ratio

    // Maybe reduce scale to fit all channels in the box height
    float total_height = float(channel_count) * h;
    float scale = (top_margin - bottom_margin) / total_height;
    scale = clamp(scale, 0.1, 1.0);
    w *= scale;
    h *= scale;

    // center justify number horizontally
    float hoffset    = right_margin - 0.5 * (right_margin - left_margin - w * num_digits);
    float place0     = (hoffset - local_coords.x) / w;
    float tens_place = floor(place0);
    if (tens_place < 0.0 || tens_place >= num_digits)
        return vec4(0);
    float dx = 1.0 - fract(place0);

    // center channels vertically
    float voffset = top_margin - 0.5 * (top_margin - bottom_margin - h * float(channel_count));
    float chan0   = (voffset - local_coords.y) / h;
    float channel = floor(chan0);

    // invert channels: red at top, alpha at bottom
    channel = float(channel_count) - channel - 1.0;

    if (channel < 0.0 || channel >= float(channel_count))
        return vec4(0);

    float dy = 1.0 - fract(chan0);
    if (dy > 0.95 || dy < 0.05)
        return vec4(0);
    dy = (dy - 0.05) / 0.90; // rescale to 0-1

    int c = int(channel);
    // 2-channel images store second channel in alpha (channel 3)
    if (channel_count == 2 && c == 1)
        c = 3;

    vec4 intensity_v = texture(tile, p_ttc);

    // Not needed because all vimage texture values are as-found.
    // if (srgb_gamma)
    //     intensity_v = sRGB_gamma_correct(intensity_v);

    intensity_v = floor(format_max * intensity_v + 0.5); // exact integer

    float intensity = intensity_v[c];

    bool zero_pad_left = pixel_numerals == NUMERALS_HEXADECIMAL;
    float p = pow(base, tens_place);
    if (tens_place > 0.0 && intensity < p && !zero_pad_left)
        return vec4(0); // number does not have this many digits

    float digit = floor(base * fract(intensity / (base * p)));

    // compensate for imperfections of this particular numeral texture
    // hex_digits_df.png (used for both hex and decimal display)
    const float fudge_scale = 0.998;  // Increase to move "9" or "F" to the left
    const float fudge_offset = 0.010;  // Increase to move "0" to the left
    const float stride = fudge_scale * 1.0 / 16.0;  // 16 characters in atlas, plus fudge factor
    const float tracking = 0.88;  // lower to push numerals closer together
    vec4 pixel = texture(numerals, vec2(fudge_offset + stride * (digit + tracking * dx), dy));

    // antialiasing
    // float radius = 18.0 * texels_per_pixel;
    float radius = 3000.0 * abs(dFdx(p_ttc.x))/w; // optional, slower

    const float l0 = 0.50; // outer edge of white outline
    const float l1 = 0.65; // inner border between white outline and black middle

    if (pixel.r < (l0 - radius))
        return vec4(0); // way outside numeral

    vec3 border_color = vec3(1.0);
    vec3 center_color = vec3(0.0);

    // tint the numerals per color channel
    if (channel_count > 1) {
        if (c == 0) {  // red
            border_color = vec3(1.0, 0.8, 0.8);
            center_color = vec3(0.3, 0.0, 0.0);
        }
        else if (c == 1) {  // green
            border_color = vec3(0.8, 1.0, 0.8);
            center_color = vec3(0.0, 0.2, 0.0);
        }
        else if (c == 2) {  // blue
            border_color = vec3(0.8, 0.8, 1.0);
            center_color = vec3(0.0, 0.0, 0.4);
        }
    }

    float wb_ratio = smoothstep(l0 - radius, l1 + radius, pixel.r);
    vec3 color     = mix(border_color, center_color, wb_ratio);
    float alpha    = smoothstep(l0 - radius, l0 + radius, pixel.r);

    return vec4(color, 0.75 * alpha * fade);
}

// modify image color to show selection box
vec4 selection_box(
    in vec2 p_omp,
    in vec4 image_color,
    in vec4 background_color,
    in ivec4 sel_rect_omp,
    in float omp_scale_qwn)
{
    if (sel_rect_omp.x == 0 && sel_rect_omp.z == 0)
        return image_color;  // uninitialized box, don't draw anything
    else {
        const float line_width_qwn = 1.8;// box outline line width in window pixels
        float hlw = 0.5 * omp_scale_qwn * line_width_qwn;// half line width, in image pixels
        // Is this pixel on the box outline?
        if (p_omp.x >= sel_rect_omp.x - hlw  // left clip
            && p_omp.x <= sel_rect_omp.z + hlw  // right clip
            && p_omp.y >= sel_rect_omp.y - hlw  // top clip
            && p_omp.y <= sel_rect_omp.w + hlw  // bottom clip
            && (abs(p_omp.x - sel_rect_omp.x) <= hlw  // on left edge
                || abs(p_omp.x - sel_rect_omp.z) <= hlw  // on right edge
                || abs(p_omp.y - sel_rect_omp.y) <= hlw  // on top edge
                || abs(p_omp.y - sel_rect_omp.w) <= hlw  // on bottom edge
        )) {
            // invert color, similar to irfanview
            vec4 base_color = mix(background_color, image_color, image_color.a);
            vec3 box_color = vec3(1) - base_color.rgb;
            // but inverted can be invisible on gray backgrounds
            if (length(box_color.rgb - image_color.rgb) < 0.5) {
                if (length(base_color.rgb) > 0.5)
                box_color = vec3(0);// black box for light gray image
                else
                box_color = vec3(1);// white box for dark gray image
            }
            return vec4(box_color, 1);
        }
        else
            return image_color;
    }
}

vec4 show_boundaries(vec4 baseColor, vec2 texelCoord,
        float edgeThickness,
        vec3 color1, vec3 color2
) {
    // Derivatives: texture-space delta per screen pixel
    vec2 dx = dFdx(texelCoord);
    vec2 dy = dFdy(texelCoord);

    // Size of one screen pixel in texture space
    float texelsPerPixel = min(length(dx), length(dy));
    // Screen pixels per texture pixel
    float pixelsPerTexel = 1.0 / texelsPerPixel;

    // Fade-in factor: 0 at 10px/texel, 1 at 20px/texel
    float fade = smoothstep(12.0, 100.0, pixelsPerTexel);
    // If fully faded out, skip work
    if (fade <= 0.0) return baseColor;

    // Compute fractional position inside a texel
    float fx = fract(texelCoord.x);
    float fy = fract(texelCoord.y);
    float distX = min(fx, 1.0 - fx) * pixelsPerTexel;
    float distY = min(fy, 1.0 - fy) * pixelsPerTexel;
    // Boundary thickness in texture space
    // const float edgeThickness = 0.7;

    bool isEdge = (distX < edgeThickness) || (distY < edgeThickness);
    if (!isEdge) return baseColor;

    // Black/white double line pattern
    float stripe = step(0.5, fract((texelCoord.x + texelCoord.y) * 4.0));
    vec4 edgeColor = vec4(mix(color1, color2, stripe), 1);

    // Final opacity: fade * something
    return mix(baseColor, edgeColor, fade * 0.4);
}

float srgb_from_linear(in float linear)
{
    if (linear <= 0.0031308)
        return linear * 12.92;
    else
        return pow(linear, 1.0/2.4) * 1.055 - 0.055;
}

vec4 srgb_from_linear(in vec4 linear)
{
    return vec4(
        srgb_from_linear(linear.r),
        srgb_from_linear(linear.g),
        srgb_from_linear(linear.b),
        linear.a);
}

vec3 stereographic_xyz(vec2 xy) {  // conformal
    float d = dot(xy, xy) + 4;
    return vec3(4 * xy.x, 4 * xy.y, dot(xy, xy) - 4) / d;
}

// Full image texture coordinates from camera direction
TexCoordAlpha rtc_for_pcm(
        vec3 p_pcm,
        int input_format,
        float df_fov_radians,
        float df_lens_rot_radians,
        vec4 df_front_center_scale,
        vec4 df_rear_center_scale,
        int render_pass)
{
    TexCoordAlpha result = TexCoordAlpha(vec2(0), 1.0);

    switch(input_format) {
        case DUAL_FISHEYE_INPUT_FORMAT:
            TexCoordPair pair = dual_fisheye_tex_coord(
                    p_pcm,
                    df_fov_radians,  // fisheye field of view
                    df_lens_rot_radians,  // lens rotation offset
                    df_front_center_scale,
                    df_rear_center_scale
            );
            if (render_pass == 1) {
                result.alpha = 1.0;  // first pass fully overwrites every valid pixel
                result.p_rtc = pair.front_tc;
                if (pair.front_bias <= 0) {
                    result.alpha = 0.0;
                    return result;
                }
            }
            else if (render_pass == 2)  {
                result.alpha = 1.0 - pair.front_bias;  // blend second pass
                result.p_rtc = pair.rear_tc;
                if (pair.front_bias >= 1) {
                    result.alpha = 0.0;
                    return result;
                }
            }
            else {
                result.alpha = 0.0;
                return result;
            }
            break;
        case SINUSOIDAL_INPUT_FORMAT:
            result.p_rtc = sinusoidal_tex_coord(p_pcm);
            break;
        case EQUIRECT_INPUT_FORMAT:
        default:
            result.p_rtc = equirect_tex_coord(p_pcm);
            break;
    }
    return result;
}

// computes tile texture coordinate for a full image texture coordinate
vec2 ttc_for_rtc(mat3 tile_X_img, vec2 rtc)
{
    rtc = rtc - floor(rtc); // Shift to range 0-1
    return (tile_X_img * vec3(rtc, 1)).xy;
}

vec4 texel_boundaries(vec4 baseColor, vec2 texelCoord) {
    return show_boundaries(baseColor, texelCoord,
            1.2,  // edge thickness
            vec3(0, 0, 0.3), vec3(1, 1, 0.7)  // color1, color2
    );
}

// Convert normalized image screen coordinates (nic) to
// app-view-modified world 3D coordinates (usr).
// If the point is invalid, (0,0,0) is returned
vec3 usr_for_nic(vec2 nic, int display_projection)
{
    switch(display_projection) {
        case STEREOGRAPHIC_DISPLAY_PROJECTION:
            return stereographic_xyz(nic);
        case AZ_EQ_DISPLAY_PROJECTION:
            if (! azeqd_valid(nic))
                return INVALID_USR;
            return azimuthal_equidistant_xyz(nic);
        case GNOMONIC_DISPLAY_PROJECTION:
            return gnomonic_xyz(nic);
        case EQUIRECT_DISPLAY_PROJECTION:
        default :
            if (! equirect_valid(nic))
                return INVALID_USR;
            return equirect_xyz(nic);
    }
    return INVALID_USR;
}

// Prepare to set line numbers correctly for the next file
#line 1 1
