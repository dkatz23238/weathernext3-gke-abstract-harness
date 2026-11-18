"""Build assets/departamentos.geojson and assets/cell_lookup.csv.

Run once: uv run --with geopandas python scripts/build_assets.py

Boundaries: geoBoundaries ARG ADM2/ADM1 (CC BY 3.0 IGO). The lookup maps every
WeatherNext 3 0.1 deg grid centre inside Argentina to the departamento it falls in,
using the dataset's own coordinates (lat ascending, lon 0..360).
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

BASE = "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/ARG"
OUT = Path(__file__).resolve().parent.parent / "assets"

adm2 = gpd.read_file(f"{BASE}/ADM2/geoBoundaries-ARG-ADM2_simplified.geojson")[["shapeName", "geometry"]]
adm1 = gpd.read_file(f"{BASE}/ADM1/geoBoundaries-ARG-ADM1_simplified.geojson")[["shapeName", "geometry"]]
adm1 = adm1.rename(columns={"shapeName": "province"})

points = adm2.copy()
points["geometry"] = adm2.representative_point()
prov = gpd.sjoin(points, adm1, predicate="within", how="left")["province"]
nearest = gpd.sjoin_nearest(points[prov.isna().values], adm1, how="left")["province"]
prov.loc[nearest.index] = nearest
prov = prov.replace({"La Roja": "La Rioja"})

depts = gpd.GeoDataFrame(
    {
        "id": np.arange(1, len(adm2) + 1),
        "name": adm2["shapeName"].values,
        "province": prov.values,
        "lat": points.geometry.y.round(3).values,
        "lon": points.geometry.x.round(3).values,
    },
    geometry=adm2.geometry.simplify(0.01, preserve_topology=True).values,
    crs=adm2.crs,
)
# Antarctic and South Atlantic claims are not useful hazard targets.
depts = depts[depts["lat"] > -56].reset_index(drop=True)

lats = np.round(np.arange(-56.0, -21.0, 0.1), 1)
lons = np.round(np.arange(285.0, 307.0, 0.1), 1)
glat, glon = np.meshgrid(lats, lons, indexing="ij")
cells = gpd.GeoDataFrame(
    {"lat": glat.ravel(), "lon": glon.ravel()},
    geometry=gpd.points_from_xy(glon.ravel() - 360, glat.ravel()),
    crs=depts.crs,
)
hit = gpd.sjoin(cells, depts[["id", "geometry"]], predicate="within", how="inner")
lookup = pd.DataFrame({"lat": hit["lat"], "lon": hit["lon"], "dept_id": hit["id"]}).drop_duplicates(["lat", "lon"])

# Departamentos smaller than one cell (CABA comunas, small partidos) get their nearest cell.
missing = depts[~depts["id"].isin(lookup["dept_id"])]
extra = [
    {"lat": round(round(r.lat * 10) / 10, 1), "lon": round(round((r.lon + 360) * 10) / 10, 1), "dept_id": r.id}
    for r in missing.itertuples()
]
lookup = pd.concat([lookup, pd.DataFrame(extra)], ignore_index=True)

OUT.mkdir(exist_ok=True)
depts.to_file(OUT / "departamentos.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)
lookup.to_csv(OUT / "cell_lookup.csv", index=False)
print(f"{len(depts)} departamentos, {depts['province'].nunique()} provinces, {len(lookup)} cells")
print(depts.groupby("province").size().to_string())
