"""A module for downloading and processing shapefiles related to the WRS-2 grid, the
Sentinel-2 MGRS tiling grid, and the boundary of Chile.
"""

import io
import os
import zipfile

import geopandas as gpd
import requests
import shapely
from shapely.ops import unary_union

URL_WRS2_GRID = "https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/atoms/files/WRS2_descending_0.zip"

# Official ESA Sentinel-2 MGRS tiling grid (KML format, ~10 MB), hosted by NASA HLS.
# Reading requires fiona with the KML/libkml driver compiled in.
URL_MGRS_GRID = (
    "https://hls.gsfc.nasa.gov/wp-content/uploads/2016/03/"
    "S2A_OPER_GIP_TILPAR_MPC__20151209T095117_V20150622T000000_21000101T000000_B00.kml"
)
# Column in the ESA KML that carries the bare MGRS tile ID (e.g. "19HCD").
MGRS_TILE_ID_COLUMN = "Name"
# KML layer that contains the 100 km × 100 km Sentinel-2 tiles.
MGRS_KML_LAYER = "Features"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}


def get_wrs2_grid():
    """Returns a GeoDataFrame containing the WRS-2 grid."""
    return gpd.read_file(URL_WRS2_GRID)


def download_wrs2_grid(output_file: str = "wrs2_descending.geojson") -> None:
    """
    Downloads the WRS-2 grid from the USGS and saves it as a GeoJSON file. If the file
    already exists, it will skip the download.

    Args:
        output_file: The name of the output GeoJSON file. Default is "wrs2_descending.geojson".

    Returns:
        None

    Raises:
        ValueError: If the downloaded file is not a valid zip or if a .shp file cannot be found in the zip.
    """
    if os.path.exists(output_file):
        print(f"WRS-2 grid already exists at {output_file}.")
        return

    print("Downloading WRS-2 from USGS...")
    response = requests.get(URL_WRS2_GRID, headers=HEADERS, stream=True)

    if response.status_code != 200:
        print(f"Failed to download. Status code: {response.status_code}")

    # Check if the content is actually a zip file
    try:
        z = zipfile.ZipFile(io.BytesIO(response.content))
        temp_dir = "wrs2_temp"
        z.extractall(temp_dir)
        print("Unzipped successfully.")
    except zipfile.BadZipFile:
        raise ValueError(
            "Error: The downloaded file is not a valid zip. The URL may have moved."
        )

    # Find the shapefile (it might be in a subfolder or named differently)
    shp_file = next((f for f in os.listdir(temp_dir) if f.endswith(".shp")), None)

    if shp_file:
        print(f"Converting {shp_file} to GeoJSON...")
        gdf = gpd.read_file(os.path.join(temp_dir, shp_file))
        gdf = gdf.to_crs(epsg=4326)
        gdf.to_file(output_file, driver="GeoJSON")
        print(f"Success! Saved to {output_file}")
        return
    else:
        raise ValueError("Could not find a .shp file in the zip.")


def get_chile_boundary(output_file: str = "chile.geojson") -> gpd.GeoDataFrame:
    """
    Downloads the boundary of Chile from Natural Earth and saves it as a GeoJSON file.

    Args:
        output_file: The name of the output GeoJSON file. Default is "chile.geojson".

    Returns:
        A GeoDataFrame containing the boundary of Chile.
    """
    world = gpd.read_file(
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
    )

    chile = world[world["ADMIN"] == "Chile"]
    chile.to_file(output_file, driver="GeoJSON")
    return chile


def get_chile_wrs2_tiles() -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame containing the WRS-2 tiles that intersect with the boundary
    of Chile.

    Args:
        None

    Returns:
        A GeoDataFrame containing the WRS-2 tiles that intersect with the boundary of Chile.
    """
    wrs2_tiles = get_wrs2_grid()
    chile_boundary = get_chile_boundary()
    intersects = wrs2_tiles.intersects(chile_boundary.geometry.unary_union)
    return wrs2_tiles[intersects]


def get_wrs2_tile(path: int, row: int) -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame containing the WRS-2 tile that corresponds to the given path
    and row.

    Args:
        path: The WRS-2 path number.
        row: The WRS-2 row number.

    Returns:
        A GeoDataFrame containing the WRS-2 tile that corresponds to the given path and row.
    """
    wrs2_tiles = get_wrs2_grid()
    return wrs2_tiles[(wrs2_tiles["PATH"] == path) & (wrs2_tiles["ROW"] == row)]


def _extract_mgrs_geometry(geom):
    """
    Extract polygon geometry from a KML GEOMETRYCOLLECTION.

    Each tile in the KML is encoded as a ``GEOMETRYCOLLECTION Z`` that bundles
    the tile polygon with a ``Point`` label artefact. This helper discards the
    point, merges any remaining polygon parts (needed for tiles that cross the
    antimeridian), and returns a 2-D ``Polygon`` or ``MultiPolygon``.
    """
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    if not polys:
        return geom
    return unary_union(polys) if len(polys) > 1 else polys[0]


def get_mgrs_grid() -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame containing the Sentinel-2 MGRS tiling grid.

    The grid is downloaded from the NASA HLS tiling-system page (the official
    ESA Sentinel-2 KML). The file is fetched with ``requests`` to avoid GDAL
    network-parsing issues, and each tile's geometry is normalised to a plain
    2-D ``Polygon`` or ``MultiPolygon``.

    Returns:
        A GeoDataFrame with one row per 100 km × 100 km Sentinel-2 tile. The
        tile ID is stored in the ``"Name"`` column (e.g. ``"19HCD"``).
    """
    response = requests.get(URL_MGRS_GRID, headers=HEADERS)
    response.raise_for_status()
    gdf = gpd.read_file(
        io.BytesIO(response.content), driver="KML", layer=MGRS_KML_LAYER
    )
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.map(_extract_mgrs_geometry)
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)
    return gdf


def get_mgrs_tile(tile_id: str) -> gpd.GeoDataFrame:
    """
    Returns a GeoDataFrame containing the MGRS tile that corresponds to the given
    Sentinel-2 tile ID.

    The tile ID is normalised before lookup: leading/trailing whitespace is stripped,
    the string is upper-cased, and an optional leading ``"T"`` is removed so that both
    ``"T19HCD"`` and ``"19HCD"`` resolve to the same tile.

    Args:
        tile_id: The Sentinel-2 MGRS tile ID, e.g. ``"19HCD"`` or ``"T19HCD"``.

    Returns:
        A GeoDataFrame containing the matching MGRS tile, or an empty GeoDataFrame if
        the tile ID is not found in the grid.

    Raises:
        ValueError: If ``tile_id`` is empty after normalisation.
    """
    normalized = tile_id.strip().upper()
    if normalized.startswith("T"):
        normalized = normalized[1:]
    if not normalized:
        raise ValueError("tile_id must not be empty")

    mgrs_tiles = get_mgrs_grid()
    return mgrs_tiles[mgrs_tiles[MGRS_TILE_ID_COLUMN] == normalized]
