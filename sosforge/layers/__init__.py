"""Built-in layers; importing this package registers every layer type a recipe can name."""
from . import daylight, gridlayer, image, lit_globe, points, regions, tracks  # noqa: F401
from .base import Layer  # noqa: F401
