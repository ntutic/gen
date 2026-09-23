"""Location points from explicit record boundaries; never from amenity geometry."""

import math

from shapely import get_coordinates, make_valid, union_all
from shapely.geometry import shape


def boundary_representative_point(collection):
    """Return (latitude, longitude) inside the largest connected boundary polygon.

    GeoJSON uses longitude/latitude. Preserve holes and disconnected parcels;
    repair invalid source rings with GEOS before choosing the largest component.
    This is a site location, not a building entrance or a derived address point.
    """
    if collection.get("type") != "FeatureCollection" or not collection.get("features"):
        raise ValueError("Record boundary must be a nonempty FeatureCollection")
    polygons = []
    for feature in collection["features"]:
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Record boundary must contain polygons")
        # Source editors may retain an empty polygon alongside the actual boundary.
        # It contributes no geometry; an entirely empty site still fails.
        if geometry.is_empty:
            continue
        for longitude, latitude in get_coordinates(geometry):
            if not (math.isfinite(longitude) and math.isfinite(latitude)
                    and -180 <= longitude <= 180 and -90 <= latitude <= 90):
                raise ValueError("Record boundary has invalid geographic coordinates")
        geometry = make_valid(geometry)
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise ValueError("Record boundary repair did not produce polygons")
        polygons.append(geometry)
    if not polygons:
        raise ValueError("Record boundary must contain nonempty polygons")
    merged = union_all(polygons)
    components = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
    largest = max(components, key=lambda polygon: polygon.area)
    if largest.area <= 0:
        raise ValueError("Record boundary has no area")
    point = largest.representative_point()
    return point.y, point.x
