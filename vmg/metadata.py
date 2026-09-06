from typing import Optional

import json
import logging
from math import asin, atan2, cos, degrees, radians, sin
import re
import struct


import exiftool
import numpy
from numpy import linalg
from numpy.typing import NDArray
import PIL
from PIL import ExifTags, Image
from tifffile import TiffPage

from vmg.dng_color import LightSource, calculate_dng_t
from vmg.exif_orientation import ExifOrientation
from vmg.frame import DimensionsOpx
from vmg.interfaces import ImageMetadataLike, InputFormat, PhotometricScale

logger = logging.getLogger(__name__)


tiff_key_t = int | str


class TiffKeys:
    """
    Search both the selected page and the root page (IFD 0) for tags
    """
    def __init__(self, page, root_page):
        self.page = page
        self.root_page = root_page

    def __contains__(self, key: tiff_key_t) -> bool:
        result = key in self.page.tags
        if not result:
            result = key in self.root_page.tags
        return result

    def __getitem__(self, key: tiff_key_t):
        if key in self.page.tags:
            return self.page.tags[key].value
        return self.root_page.tags[key].value

    def get(self, key: tiff_key_t, default: Optional[tiff_key_t] = None):
        try:
            return self[key].value
        except KeyError:
            return default


class ImageMetadata(ImageMetadataLike):
    """
    Sketch of class to contain image metadata.

    Responsibilities:
      * Store image metadata
      * Load image metadata
      * NOT signaling
    """

    def __init__(self):
        # Reasonable defaults wherever possible
        # General metadata
        self.file_name: Optional[str] = None
        self.size_opx = DimensionsOpx(1, 1)  # logical size, exif oriented
        self.size_rpx = (1, 1)  # raw array size
        self.orientation: ExifOrientation = ExifOrientation.ROTATE_0
        self.rpx_R_opx = numpy.eye(2, dtype=numpy.float32)
        self.input_format = InputFormat.STANDARD_PHOTO
        self.photometric_scale = PhotometricScale.SRGB
        self.upper_bound = 255
        self.channel_count = 3
        self.data_max = 255  # Set data_max AFTER loading image bytes
        # Pano metadata
        self.initial_heading_degrees = 0.0
        self.initial_pitch_degrees = 0.0
        self.initial_roll_degrees = 0.0
        self.pose_heading_degrees = 0.0
        self.pose_pitch_degrees = 0.0
        self.pose_roll_degrees = 0.0
        self.pcm_R_geo = numpy.eye(3, dtype=numpy.float32)
        # Dual fisheye lens parameters
        self.inscribed_fov_radians = radians(195.0)
        self.df_lens_rot_radians = radians(0.0)
        self.df_front_center_scale = (0.75, 0.5, 0.5, 1.0)  # cx, cy, sx, sy in rtc frame
        self.df_rear_center_scale = (0.25, 0.5, 0.5, 1.0)
        # Dng metadata
        self.is_cfa = False
        self.cfa_pattern = (-1, -1, -1, -1)  # All -1 means "not CFA"
        self.black_level = (0.0, 0.0, 0.0)
        self.white_level = (1.0, 1.0, 1.0)
        self.as_shot_neutral = (1.0, 1.0, 1.0)
        self.color_matrix1 = numpy.eye(3, dtype=numpy.float32)
        self.color_matrix2 = None
        self.calibration_illuminant1 = LightSource.STANDARD_LIGHT_A
        self.calibration_illuminant2 = LightSource.D65
        # Convert camera sensor reference values to linear srgb
        self.lsr_X_wba = numpy.eye(3, dtype=numpy.float32)
        self.baseline_exposure = 0.0

    def load_tifffile_page(self, page: TiffPage, root_page: TiffPage):
        tk = TiffKeys(page, root_page)
        debug = False
        if debug:
            # print(page.dims)
            # print(page.eer_tags)
            # print(page.geotiff_tags)
            # print(page.get_resolution())
            # print(page.imagelength)
            # print(page.imagewidth)
            # print(page.is_dng)
            # print(repr(page.dtype))
            # print(page.is_tiled)  # TODO
            # print(page.photometric)  # 32803?
            # print(page.resolution)
            # print(page.size)
            print("*** PAGE ATTRIBUTES ***:")
            for att in dir(page):
                if att.startswith("_"):
                    continue
                print(att)
        # SIZE
        self.size_opx = DimensionsOpx(int(page.imagewidth), int(page.imagelength))
        # same, unless we find an exif orientation tag later
        self.size_rpx = int(self.size_opx[0]), int(self.size_opx[1])
        self.channel_count = page.samplesperpixel
        self.upper_bound = numpy.iinfo(page.dtype).max
        xmp = {}
        if "XMP" in tk:
            xmp = tk["XMP"]
        elif "XMLPacket" in tk:
            xmp = tk["XMLPacket"]
        if len(xmp) > 0:
            logger.info("Found XMP metadata")
        exif = {}
        if "ExifTag" in tk:
            exif = tk["ExifTag"]
        if debug:
            print("*** EXIF ATTRIBUTES ***:")
            for item in exif:
                print(item)
        if "Model" in tk:
            model = tk['Model']
            self._update_model(model)
        if 'CFAPattern' in tk:
            cfa = tk['CFAPattern']
            assert len(cfa) == 4
            self.cfa_pattern = tuple(int(x) for x in cfa)
            # assert cfa == [0, 1, 1, 2]
            self.is_cfa = True
            self.photometric_scale = PhotometricScale.LINEAR
        user_comment = ""
        if "UserComment" in exif:
            try:
                user_comment = exif["UserComment"].decode()
            except AttributeError:
                user_comment = exif["UserComment"]  # Already a string
        if "Orientation" in exif:
            orientation_code = int(exif["Orientation"])
            self._update_orientation(orientation_code)
        elif "Orientation" in tk:
            orientation_code = int(tk["Orientation"])
            self._update_orientation(orientation_code)
        # black level, white level
        if "BlackLevel" in tk:
            self._parse_black_level(tk["BlackLevel"])
        if "WhiteLevel" in tk:
            self._parse_white_level(tk["WhiteLevel"])
        if 'AsShotNeutral' in tk:
            asn = tk['AsShotNeutral']
            assert len(asn) == 6
            self.as_shot_neutral = asn[0]/asn[1], asn[2]/asn[3], asn[4]/asn[5]
        if "ColorMatrix1" in tk:
            cm1 = tk['ColorMatrix1']
            assert len(cm1) == 18
            a = numpy.array(cm1, numpy.float32).reshape(9, 2)
            self.color_matrix1 = (a[:, 0] / a[:, 1]).reshape(3, 3)
        if "ColorMatrix2" in tk:
            cm2 = tk['ColorMatrix2']
            assert len(cm2) == 18
            a = numpy.array(cm2, numpy.float32).reshape(9, 2)
            self.color_matrix2 = (a[:, 0] / a[:, 1]).reshape(3, 3)
        if 'CalibrationIlluminant1' in tk:
            cal1 = tk['CalibrationIlluminant1']
            cal1 = LightSource(int(cal1))
            if cal1 == LightSource.UNKNOWN:
                logger.info("Unknown calibration illuminant")
            else:
                self.calibration_illuminant1 = cal1
        if 'CalibrationIlluminant2' in tk:
            cal2 = tk['CalibrationIlluminant2']
            cal2 = LightSource(int(cal2))
            if cal2 == LightSource.UNKNOWN:
                logger.info("Unknown calibration illuminant")
            else:
                self.calibration_illuminant2 = cal2
        if "BaselineExposure" in tk:
            exp = tk['BaselineExposure']
            self.baseline_exposure = exp[0] / exp[1]
        self._compute_forward_matrix()
        # Panorama metadata
        w, h = self.size_opx
        # TODO: GPano orientation, if we find a tiff that has some
        if w == 2 * h or 2 * w == h:
            if 2 * w == h:  # vertical dual fisheye
                self.df_front_center_scale = (0.5, 0.25, 1.0, 0.5)
                self.df_rear_center_scale = (0.5, 0.75, 1.0, 0.5)
                self.input_format = InputFormat.DUAL_FISHEYE
            elif self.cfa_pattern != (-1, -1, -1, -1) and self.channel_count == 1:
                # The rawest of DNGs are not equirectangular
                self.input_format = InputFormat.DUAL_FISHEYE
            else:
                self.input_format = InputFormat.EQUIRECTANGULAR
            if re.search(r'\sIMUHEX=([0-9a-fA-F]{36})\s', user_comment):  # QooCam3 Ultra raw dng
                m = re.search(r'\sIMUHEX=([0-9a-fA-F]{36})\s', user_comment)
                assert m
                imu_hex = m.group(1)
                self.pose_roll_degrees, self.pose_pitch_degrees = get_roll_pitch_from_imu(imu_hex)
            if "ricoh" in model.lower():
                if "MakerNote" in exif:
                    maker_note = exif["MakerNote"]
                    if maker_note.startswith(b'Ricoh'):
                        roll, pitch, heading = parse_ricoh_makernote(maker_note)
                        if roll is not None:
                            self.pose_roll_degrees = roll
                        if pitch is not None:
                            self.pose_pitch_degrees = pitch
                        if heading is not None:
                            self.pose_heading_degrees = heading
            if "insta360" in model.lower():
                fields = user_comment.split("_")
                # print(len(fields))
                # TODO:
            if self.pose_heading_degrees != 0 or self.pose_pitch_degrees != 0 or self.pose_roll_degrees != 0:
                logger.info(
                    f"Pose heading, pitch, roll = ({self.pose_heading_degrees}, {self.pose_pitch_degrees}, {self.pose_roll_degrees})")
                self.update_pcm_rot_geo()
        else:
            self.input_format = InputFormat.STANDARD_PHOTO

    def load_pil_image(self, pil_image: Image.Image) -> None:
        w, h = pil_image.size
        self.size_rpx = int(w), int(h)  # Unrotated dimension
        # TODO: move away from DimensionsOmp and other frame vectors
        self.size_opx = DimensionsOpx(w, h)
        self.channel_count = channel_count_for_pil_mode.get(pil_image.mode, 3)
        exif0 = pil_image.getexif()
        exif = {
            PIL.ExifTags.TAGS[k]: v
            for k, v in exif0.items()
            if k in PIL.ExifTags.TAGS
        }
        for ifd_id in PIL.ExifTags.IFD:
            try:
                ifd = exif0.get_ifd(ifd_id)
                if ifd_id == PIL.ExifTags.IFD.GPSInfo:
                    resolve = PIL.ExifTags.GPSTAGS
                else:
                    resolve = PIL.ExifTags.TAGS
                for k, v in ifd.items():
                    tag = resolve.get(k, k)
                    exif[tag] = v
            except KeyError:
                pass
        try:
            xmp = pil_image.getxmp()  # noqa
        except AttributeError:
            xmp = {}
        for k in xmp:
            logger.debug(f"XMP {k} = '{xmp[k]}'")
        for k in exif:
            logger.debug(f"EXIF {k} = '{exif[k]}'")
        orientation_code: int = exif.get("Orientation", 1)
        self._update_orientation(orientation_code)
        w, h = self.size_opx
        model = exif.get("Model", "").lower()
        self._update_model(exif.get("Model", ""))
        logger.debug(f"Camera model = '{model}'")
        if w != 2 * h:
            self.input_format = InputFormat.STANDARD_PHOTO  # Non-2:1 aspect is always a regular photo
        else:
            # 2016 Gear 360 unstitched image has certain sizes
            if model == "sm-c200" and ((w, h) == (7776, 3888) or (w, h) == (5792, 2896)):
                self.input_format = InputFormat.DUAL_FISHEYE
            elif model.startswith("ricoh theta"):
                self.input_format = InputFormat.EQUIRECTANGULAR
            else:
                self.input_format = InputFormat.EQUIRECTANGULAR  # Too inclusive...
            try:
                # TODO: InitialViewHeadingDegrees
                desc = xmp["xmpmeta"]["RDF"]["Description"]
                # Normalize to a list
                if isinstance(desc, dict):
                    desc_list = [desc]
                else:
                    desc_list = desc
                for d in desc_list:
                    if "PoseHeadingDegrees" in d:
                        self.pose_heading_degrees = float(d["PoseHeadingDegrees"])
                        _is_pano = True
                    if "PosePitchDegrees" in d:
                        self.pose_pitch_degrees = float(d["PosePitchDegrees"])
                    if "PoseRollDegrees" in d:
                        self.pose_roll_degrees = float(d["PoseRollDegrees"])
                    if "InitialViewHeadingDegrees" in d:
                        self.initial_heading_degrees = float(d["InitialViewHeadingDegrees"])
                    if "InitialViewPitchDegrees" in d:
                        self.initial_pitch_degrees = float(d["InitialViewPitchDegrees"])
                    if "InitialViewRollDegrees" in d:
                        self.initial_roll_degrees = float(d["InitialViewRollDegrees"])
                Use360PanoReferenceConvention = False
                if Use360PanoReferenceConvention:
                    self.pose_roll_degrees = -self.pose_roll_degrees
                # Restrict to documented XMP range
                self.pose_roll_degrees = (self.pose_roll_degrees + 180.0) % 360.0 - 180.0
                self.pose_pitch_degrees = max(-90.0, min(90.0, self.pose_pitch_degrees))
                self.pose_heading_degrees = self.pose_heading_degrees % 360.0
                if self.pose_heading_degrees != 0 or self.pose_pitch_degrees != 0 or self.pose_roll_degrees != 0:
                    logger.info(
                        f"Pose heading, pitch, roll = ({self.pose_heading_degrees}, {self.pose_pitch_degrees}, {self.pose_roll_degrees})")
                self.update_pcm_rot_geo()
            except (KeyError, TypeError):
                pass

    def update_pcm_rot_geo(self):
        # Photographer's camera pose
        roll = radians(self.pose_roll_degrees)
        pitch = radians(self.pose_pitch_degrees)
        heading = radians(self.pose_heading_degrees)
        pcm_rot_geo = numpy.array([
            [cos(roll), -sin(roll), 0],
            [sin(roll), cos(roll), 0],
            [0, 0, 1],
        ], dtype=numpy.float32)
        pcm_rot_geo = pcm_rot_geo @ [
            [1, 0, 0],
            [0, cos(pitch), sin(pitch)],
            [0, -sin(pitch), cos(pitch)],
        ]
        pcm_rot_geo = pcm_rot_geo @ [
            [cos(heading), 0, sin(heading)],
            [0, 1, 0],
            [-sin(heading), 0, cos(heading)],
        ]
        self.pcm_R_geo = pcm_rot_geo

    def _update_model(self, model_name):
        # Dual fisheye parameters
        # It's OK to put whatever if the camera doesn't have fisheyes,
        # so these string checks can be somewhat broad
        low = model_name.lower()
        # Inscribed fov determined by looking at a distant feature in one image
        if "insta360 oners" in low:
            self.inscribed_fov_radians = radians(193.8)  # "Insta360 OneRS"
        if "insta360 x6" in low:
            self.inscribed_fov_radians = radians(196.8)  # "Insta360 X6"
        elif "qoocam" in low:
            self.inscribed_fov_radians = radians(196.8)  # "QooCam 3 Ultra"
        elif "qjxj01fj" in low:
            self.inscribed_fov_radians = radians(197.6)  # "QJXJ01FJ" Xiaomi Misphere
        elif "ricoh theta" in low:
            self.inscribed_fov_radians = radians(191.2)  # "RICOH THETA Z1"
        elif "sm-c200" in low:
            self.inscribed_fov_radians = radians(193.8)  # "SM-C200" 2016 Gear 360

    def _update_orientation(self, orientation_code: int):
        self.orientation = ExifOrientation(orientation_code)
        logger.debug(f"Image EXIF orientation = {self.orientation}")
        self.rpx_R_opx = rotation_for_exif_orientation.get(orientation_code, numpy.eye(2, dtype=numpy.float32))
        self.size_opx = DimensionsOpx(*[abs(x) for x in (self.rpx_R_opx.T @ self.size_rpx)])

    def _parse_bw(self, value) -> tuple[float, float, float]:
        try:
            # If it's a string, convert it to numbers
            try:  # Is it a string?
                value = [x for x in value.split()]
            except AttributeError as exc:
                pass  # Not a string I guess

            # Make sure the elements are numbers
            try:
                value = [float(x) for x in value]
            except TypeError:
                # it's not an array, so make it one
                value = [float(value), ]

            # If it got an even length other than 4, treat it as rational
            if len(value) != 4 and len(value) % 2 == 0:
                value = [float(n) / float(d) for n, d, in zip(value[::2], value[1::2])]

            # If it's a single value, make it 3
            if len(value) == 1:
                value = [float(value[0])] * 3

            # Convert CFA RGGB to RGB
            if len(value) == 4:
                value = [value[0], 0.5 * (value[1] + value[2]), value[3]]

            # Insta360 X6 linear RGB DNG has 12 rational values
            if len(value) > 3:
                stride = len(value) // 3
                value = value[::stride]  # should be mean actually but whatever

            # Normalize
            value = [float(x) / self.upper_bound for x in value]

            assert len(value) == 3
            return value
        except BaseException as exc:
            logger.error(exc)
            raise

    def _parse_black_level(self, value):
        self.black_level = self._parse_bw(value)

    def _parse_white_level(self, value: str):
        self.white_level = self._parse_bw(value)

    def load_exiftool(self, file_name):
        with exiftool.ExifTool() as et:
            raw = et.execute("-j", file_name)
            exif = json.loads(raw)[0]
        debug = False
        if debug:
            print(json.dumps(exif, indent=2, sort_keys=True))
        w, h = exif["EXIF:ImageWidth"], exif["EXIF:ImageHeight"]
        self.size_rpx = int(w), int(h)
        self.size_opx = DimensionsOpx(w, h)
        self.channel_count = exif["EXIF:SamplesPerPixel"]
        orientation_code: int = exif["EXIF:Orientation"]
        self._update_orientation(orientation_code)
        # Camera model specific values
        if "EXIF:Model" in exif:
            model = exif["EXIF:Model"]
            self._update_model(model)
        # Color adjustments, especially for DNG files
        if "EXIF:CFAPattern2" in exif:
            # assert exif["EXIF:CFAPattern2"] == "0 1 1 2"  # We only know RGGB
            self.cfa_pattern = tuple(int(x) for x in exif["EXIF:CFAPattern2"].split())
        if "EXIF:BlackLevel" in exif:
            self._parse_black_level(exif["EXIF:BlackLevel"])
        if "EXIF:WhiteLevel" in exif:
            self._parse_white_level(exif["EXIF:WhiteLevel"])
        if "EXIF:AsShotNeutral" in exif:
            self.as_shot_neutral = [float(x) for x in exif["EXIF:AsShotNeutral"].split()]
            assert len(self.as_shot_neutral) == 3
        if "EXIF:ColorMatrix1" in exif:
            cm1 = exif["EXIF:ColorMatrix1"].split()
            assert len(cm1) == 9
            self.color_matrix1 = numpy.array(cm1, dtype=numpy.float32).reshape((3, 3))
        if "EXIF:ColorMatrix2" in exif:
            cm1 = exif["EXIF:ColorMatrix2"].split()
            assert len(cm1) == 9
            self.color_matrix2 = numpy.array(cm1, dtype=numpy.float32).reshape((3, 3))
        if "EXIF:CalibrationIlluminant1" in exif:
            ci = LightSource(int(exif["EXIF:CalibrationIlluminant1"]))
            if ci == LightSource.UNKNOWN:
                logger.info("Unknown calibration illuminant")
            else:
                self.calibration_illuminant1 = ci
        if "EXIF:CalibrationIlluminant2" in exif:
            ci = LightSource(int(exif["EXIF:CalibrationIlluminant2"]))
            if ci == LightSource.UNKNOWN:
                logger.info("Unknown calibration illuminant")
            else:
                self.calibration_illuminant2 = ci
        if "EXIF:BaselineExposure" in exif:
            self.baseline_exposure = float(exif["EXIF:BaselineExposure"])
        # TODO: some dng might have a ForwardMatrix available...
        self._compute_forward_matrix()
        # Panorama metadata
        user_comment = ""
        if "EXIF:UserComment" in exif:
            user_comment = exif["EXIF:UserComment"]
        w, h = self.size_opx
        if w != 2 * h:
            self.input_format = InputFormat.STANDARD_PHOTO  # Non-2:1 aspect is always a regular photo
        else:  # Panorama
            if "EXIF:DNGVersion" in exif:
                self.input_format = InputFormat.DUAL_FISHEYE
            else:
                self.input_format = InputFormat.EQUIRECTANGULAR
            if "EXIF:PoseHeadingDegrees" in exif:
                self.pose_heading_degrees = float(exif["EXIF:PoseHeadingDegrees"])
            elif "EXIF:GPSImgDirection" in exif:
                self.pose_heading_degrees = float(exif["EXIF:GPSImgDirection"])
            if "EXIF:PosePitchDegrees" in exif:
                self.pose_pitch_degrees = float(exif["EXIF:PosePitchDegrees"])
            elif "Composite:RicohPitch" in exif:  # Ricoh Theta Z1 raw dng
                self.pose_pitch_degrees = float(exif["Composite:RicohPitch"])
            elif re.search(r'\sIMUHEX=([0-9a-fA-F]{36})\s', user_comment):  # QooCam3 Ultra raw dng
                m = re.search(r'\sIMUHEX=([0-9a-fA-F]{36})\s', user_comment)
                assert m
                imu_hex = m.group(1)
                self.pose_roll_degrees, self.pose_pitch_degrees = get_roll_pitch_from_imu(imu_hex)
            if "EXIF:PoseRollDegrees" in exif:
                self.pose_roll_degrees = float(exif["EXIF:PoseRollDegrees"])
            elif "Composite:RicohRoll" in exif:
                self.pose_roll_degrees = float(exif["Composite:RicohRoll"])
            if "EXIF:InitialViewHeadingDegrees" in exif:
                self.initial_heading_degrees = float(exif["EXIF:InitialViewHeadingDegrees"])
            if "EXIF:InitialViewPitchDegrees" in exif:
                self.initial_pitch_degrees = float(exif["EXIF:InitialViewPitchDegrees"])
            if "EXIF:InitialViewRollDegrees" in exif:
                self.initial_roll_degrees = float(exif["EXIF:InitialViewRollDegrees"])
            if self.pose_heading_degrees != 0 or self.pose_pitch_degrees != 0 or self.pose_roll_degrees != 0:
                logger.info(
                    f"Pose heading, pitch, roll = ({self.pose_heading_degrees}, {self.pose_pitch_degrees}, {self.pose_roll_degrees})")
                self.update_pcm_rot_geo()

    def _compute_forward_matrix(self):
        # Interpolate color matrix
        if self.color_matrix2 is None:
            rfv_X_d50 = self.color_matrix1
        else:
            dng_t = calculate_dng_t(
                self.as_shot_neutral,
                self.color_matrix1, self.color_matrix2,
                self.calibration_illuminant1, self.calibration_illuminant2,
            )
            rfv_X_d50 = self.color_matrix1 * (1 - dng_t) + self.color_matrix2 * dng_t
        # ColorMatrix requires us to undo white balance first, then invert the matrix.
        # wb_gain_matrix = numpy.diag([1/x for x in self.as_shot_neutral])
        # xyz_X_sensor = linalg.inv(color_matrix) @ wb_gain_matrix
        lsr_X_d65 = numpy.array([
            [+3.2404542, -1.5371385, -0.4985314],
            [-0.9692660,  1.8760108,  0.0415560],
            [+0.0556434, -0.2040259,  1.0572252],
        ])
        d65_X_d50 = numpy.array([  # Bradford
            [+0.9555766, -0.0230393,  0.0631636],
            [-0.0283858,  1.0099416,  0.0210077],
            [+0.0123140, -0.0205076,  1.3299115]
        ])
        d50_X_rfv = linalg.inv(rfv_X_d50)
        rfv_X_wba = numpy.diag(self.as_shot_neutral)
        self.lsr_X_wba = lsr_X_d65 @ d65_X_d50 @ d50_X_rfv @ rfv_X_wba


channel_count_for_pil_mode = {
    "1": 1,      # bilevel
    "L": 1,      # 8‑bit grayscale
    "P": 1,      # palette (indexed)
    "LA": 2,     # L + alpha
    "RGB": 3,
    "RGBA": 4,
    "CMYK": 4,
    "YCbCr": 3,
    "I": 1,      # 32‑bit signed integer
    "F": 1,      # 32‑bit float
}

rotation_for_exif_orientation: dict[int, NDArray[numpy.float32]] = {
    1: numpy.array([[1, 0], [0, 1]], dtype=numpy.float32),
    2: numpy.array([[-1, 0], [0, 1]], dtype=numpy.float32),
    3: numpy.array([[-1, 0], [0, -1]], dtype=numpy.float32),
    4: numpy.array([[1, 0], [0, -1]], dtype=numpy.float32),
    5: numpy.array([[0, 1], [1, 0]], dtype=numpy.float32),
    6: numpy.array([[0, 1], [-1, 0]], dtype=numpy.float32),
    7: numpy.array([[0, -1], [-1, 0]], dtype=numpy.float32),
    8: numpy.array([[0, -1], [1, 0]], dtype=numpy.float32),
}


def get_roll_pitch_from_imu(imu_hex_string: str):
    """
    Extracts the Roll angle in degrees from a 36-character QooCam3 IMU hex string.
    """

    # 2. Extract the 4 bytes controlling the Roll axis (Bytes 7 to 10)
    # Character indices: Bytes 7-8 are chars 12-16, Bytes 9-10 are chars 16-20
    roll_bytes_hex = imu_hex_string[12:20]
    binary_roll = bytes.fromhex(roll_bytes_hex)

    # 3. Unpack into two 16-bit signed integers (Little-Endian 'h')
    # val_y is the first pair, val_x is the second pair
    roll_y, roll_x = struct.unpack('<hh', binary_roll)
    # Avoid division by zero errors if both values are flattened
    if roll_x == 0 and roll_y == 0:
        roll_radians = 0.0
    else:
        roll_radians = atan2(-roll_y, -roll_x)
    roll_degrees = degrees(roll_radians) % 360
    if roll_degrees > 180:
        roll_degrees -= 360

    pitch_bytes_hex = imu_hex_string[20:28]
    binary_pitch = bytes.fromhex(pitch_bytes_hex)
    pitch_y, pitch_x = struct.unpack('<hh', binary_pitch)
    if pitch_x == 0 and pitch_y == 0:
        pitch_radians = 0.0
    elif pitch_x == 0:
        # Compute the shallow angle against the internal gravity constant
        pitch_radians = atan2(pitch_y, 16384)
    else:
        pitch_radians = atan2(pitch_y, pitch_x)
    pitch_degrees = degrees(pitch_radians)

    return roll_degrees, pitch_degrees


def parse_ricoh_makernote(makernote_bytes: bytes):
    # 1. Determine Endianness and Base Offset
    # Ricoh usually uses Little Endian ("<").
    # The sub-IFD usually starts after the text header "RICOH\x00\x00\x00" (8 bytes)
    # or similar variations. Let's find where the IFD structure actually begins.

    header_offset = 0
    if makernote_bytes.startswith(b'Ricoh'):
        header_offset = 8  # Skip "RICOH\x00\x00\x00"

    endian = '>'

    # 2. Read the number of fields in this directory (First 2 bytes)
    num_fields = struct.unpack(f'{endian}H', makernote_bytes[header_offset:header_offset + 2])[0]

    roll = None
    pitch = None
    heading = None

    # 3. Loop through each 12-byte tag entry
    for i in range(num_fields):
        # Calculate where this specific entry starts
        entry_start = header_offset + 2 + (i * 12)
        entry_bytes = makernote_bytes[entry_start:entry_start + 12]

        # Unpack the 12-byte layout
        tag, data_type, count, val_or_offset = struct.unpack(f'{endian}HHI4s', entry_bytes)

        # 4. Check if it matches your Accelerometer tag (0x0003)
        if tag == 0x0003:
            # Since the data is a rational64s (16 bytes), val_or_offset is a pointer
            _data_offset0 = struct.unpack(f'{endian}I', val_or_offset)[0]
            data_offset = 836  # determined empirically

            # NOTE: Depending on how tifffile sliced the MakerNote,
            # this offset might be relative to the START of the maker note
            # or relative to the start of the sub-IFD table. Let's try relative to MakerNote start:
            raw_data = makernote_bytes[data_offset:data_offset + 16]

            # Unpack 4 signed 32-bit integers (2 numerators, 2 denominators)
            n1, d1, n2, d2 = struct.unpack(f'{endian}iiii', raw_data)

            # Safe division
            roll = n1 / d1 if d1 != 0 else 0.0
            if roll > 180.0:
                roll -= 360.0
            pitch = n2 / d2 if d2 != 0 else 0.0

            # Heading at 0x0004
            data_offset2 = data_offset + 16
            raw_data2 = makernote_bytes[data_offset2:data_offset2 + 8]
            n3, d3 = struct.unpack(f'{endian}II', raw_data2)
            heading = n3 / d3 if d3 != 0 else 0.0

            return roll, pitch, heading


if __name__ == "__main__":
    # --- Quick Test Verification ---
    # Testing your stable 45-degree anchor string:
    print(f"Roll, Pitch: {get_roll_pitch_from_imu('000000000000808080800000000000000000')}°")
    # Target Output: 45.0°

    # Testing our calculated 10-degree target string:
    print(f"Roll, Pitch: {get_roll_pitch_from_imu('00000000000085E980800000000000000000')}°")
    # Target Output: 10.0°

    for imu in ("808080807BFF0000", "40404040e3061027",
                "2020202082e91027", "20204040a9358813",
                "404020206290c409", "808040407BFF0000",
                "404080807BFF0000", "52E2F00B7BFF0000",
                ):
        print(imu, get_roll_pitch_from_imu(f"000000000000{imu}00000000"))
