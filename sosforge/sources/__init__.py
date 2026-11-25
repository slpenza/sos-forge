"""Built-in data sources; importing this package registers every source type a recipe can name."""
from . import aurora, files, firms, ghsl, overpass, regions, tracks  # noqa: F401
from .base import PointTable, Track, TrackSet, load_source  # noqa: F401
