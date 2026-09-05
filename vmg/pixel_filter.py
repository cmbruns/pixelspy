from enum import Enum


class PixelFilter(Enum):
    SHARP = 1
    CATMULL_ROM = 2


class PixelNumerals(Enum):
    HEXADECIMAL = 1
    DECIMAL = 2
    NONE = 3
    DEFAULT = DECIMAL
