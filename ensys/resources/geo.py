"""
Site location.

A Location is the anchor for every resource lookup: it carries the
coordinates a provider needs, the elevation that sets air density, and the
UTC offset without which solar position is wrong by up to an hour.

The UTC offset is deliberately not guessed from a timezone database, because
the standard library has no such database on every platform and Pyodide has
none at all. It is either supplied, or estimated from longitude with the
estimate flagged so the report can say the assumption was made.
"""

from __future__ import annotations

import math


class Location:
    """
    A geographic site.

    Parameters
    ----------
    latitude : degrees north, negative for south
    longitude : degrees east, negative for west
    elevation_m : height above sea level; drives air density for wind and
        the barometric pressure used in the solar model
    utc_offset_hours : standard-time offset. If omitted it is estimated from
        longitude, which is right for most of the world and wrong for large
        countries on a single zone (China, India, Spain).
    albedo : ground reflectance for the PV ground-reflected term
    terrain : one of the wind roughness classes, sets the shear exponent
    """

    def __init__(
        self,
        name="Site",
        latitude=0.0,
        longitude=0.0,
        elevation_m=0.0,
        utc_offset_hours=None,
        albedo=0.20,
        terrain="open_farmland",
        country=None,
        timezone_name=None,
    ):
        self.name = name
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.elevation_m = float(elevation_m or 0.0)
        self.albedo = float(albedo)
        self.terrain = terrain
        self.country = country
        self.timezone_name = timezone_name

        if not (-90.0 <= self.latitude <= 90.0):
            raise ValueError(f"latitude {self.latitude} outside [-90, 90]")
        if not (-180.0 <= self.longitude <= 180.0):
            raise ValueError(f"longitude {self.longitude} outside [-180, 180]")

        if utc_offset_hours is None:
            self.utc_offset_hours = round(self.longitude / 15.0)
            self.utc_offset_estimated = True
        else:
            self.utc_offset_hours = float(utc_offset_hours)
            self.utc_offset_estimated = False

    def __repr__(self):
        return (
            f"Location({self.name!r}, {self.latitude:.4f}, "
            f"{self.longitude:.4f}, {self.elevation_m:.0f} m)"
        )

    @property
    def hemisphere(self):
        return "north" if self.latitude >= 0 else "south"

    @property
    def optimal_azimuth(self):
        """
        Azimuth a fixed array should face, in the 0=north/180=south
        convention. Equator-facing: south in the northern hemisphere.
        """
        return 180.0 if self.latitude >= 0 else 0.0

    @property
    def shear_exponent(self):
        from ..models.wind import SHEAR_EXPONENTS

        return SHEAR_EXPONENTS.get(self.terrain, 0.14)

    @property
    def roughness_length_m(self):
        from ..models.wind import ROUGHNESS_LENGTHS

        return ROUGHNESS_LENGTHS.get(self.terrain, 0.10)

    def air_density(self, temperature_c=None):
        from ..models.wind import air_density_at

        return air_density_at(self.elevation_m, temperature_c)

    def distance_km(self, other):
        """Great-circle distance to another location, for cache matching."""
        r = 6371.0
        p1 = math.radians(self.latitude)
        p2 = math.radians(other.latitude)
        dp = math.radians(other.latitude - self.latitude)
        dl = math.radians(other.longitude - self.longitude)
        a = (
            math.sin(dp / 2) ** 2
            + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        )
        return 2 * r * math.asin(math.sqrt(a))

    def to_dict(self):
        return {
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "elevation_m": self.elevation_m,
            "utc_offset_hours": self.utc_offset_hours,
            "utc_offset_estimated": self.utc_offset_estimated,
            "albedo": self.albedo,
            "terrain": self.terrain,
            "country": self.country,
            "timezone_name": self.timezone_name,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            name=d.get("name", "Site"),
            latitude=d.get("latitude", 0.0),
            longitude=d.get("longitude", 0.0),
            elevation_m=d.get("elevation_m", 0.0),
            utc_offset_hours=d.get("utc_offset_hours"),
            albedo=d.get("albedo", 0.20),
            terrain=d.get("terrain", "open_farmland"),
            country=d.get("country"),
            timezone_name=d.get("timezone_name"),
        )


# A few reference sites, including the one the existing MATLAB study used.
PRESETS = {
    "shiraz": Location(
        name="Shiraz, Iran", latitude=29.6122, longitude=52.5174,
        elevation_m=1500.0, utc_offset_hours=3.5, terrain="open_farmland",
        country="IR", timezone_name="Asia/Tehran",
    ),
    "adelaide": Location(
        name="Adelaide, Australia", latitude=-34.9285, longitude=138.6007,
        elevation_m=50.0, utc_offset_hours=9.5, terrain="suburban",
        country="AU", timezone_name="Australia/Adelaide",
    ),
    "tehran": Location(
        name="Tehran, Iran", latitude=35.6892, longitude=51.3890,
        elevation_m=1200.0, utc_offset_hours=3.5, terrain="urban",
        country="IR", timezone_name="Asia/Tehran",
    ),
}
