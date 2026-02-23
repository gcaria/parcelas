import io
import os
import zipfile

import geopandas as gpd
import requests

URL_WRS2_GRID = "https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/atoms/files/WRS2_descending_0.zip"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


def get_wrs2_grid():
    return gpd.read_file(URL_WRS2_GRID)


def download_wrs2_grid(output_file: str = "wrs2_descending.geojson") -> None:
    """Downloads the WRS-2 grid from the USGS and saves it as a GeoJSON file.
    If the file already exists, it will skip the download.

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

    print(f"Downloading WRS-2 from USGS...")
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
    """Downloads the boundary of Chile from Natural Earth and saves it as a
    GeoJSON file.

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
    """Returns a GeoDataFrame containing the WRS-2 tiles that intersect with
    the boundary of Chile.

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
    """Returns a GeoDataFrame containing the WRS-2 tile that corresponds to the
    given path and row.

    Args:
        path: The WRS-2 path number.
        row: The WRS-2 row number.

    Returns:
        A GeoDataFrame containing the WRS-2 tile that corresponds to the given path and row.
    """

    wrs2_tiles = get_wrs2_grid()
    return wrs2_tiles[(wrs2_tiles["PATH"] == path) & (wrs2_tiles["ROW"] == row)]
