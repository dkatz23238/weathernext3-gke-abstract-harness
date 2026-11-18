"""WeatherNext 3 ensemble statistics from the GCS Zarr store, Argentina only.

Every Zarr chunk is one global field for one lead hour (~20 MB), so a regional
read still downloads whole globes. Fetch only the hours a hazard needs, in
parallel, and keep the Argentina crop of each (variable, lead) as a small .npy
under the profile's data dir; later questions on the same run cost nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gcsfs
import numpy as np
import pandas as pd
import xarray as xr

ROOT = "weathernext3_statistics_spatial/weathernext_3_0_0_statistics/zarr/2026_to_present"
LAT = slice(-56.0, -21.0)  # 351 rows, ascending
LON = slice(285.0, 307.0)  # 221 cols, dataset longitudes are 0..360
WORKERS = int(os.environ.get("WN3_WORKERS", "24"))
KEEP_RUNS = 2


def filesystem() -> gcsfs.GCSFileSystem:
    adc = Path.home() / ".config/gcloud/application_default_credentials.json"
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or adc.exists():
        return gcsfs.GCSFileSystem(token="google_default")
    # Local fallback: the gcloud CLI user (token lasts an hour).
    from google.oauth2.credentials import Credentials

    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    return gcsfs.GCSFileSystem(token=Credentials(token))


def latest_run(fs: gcsfs.GCSFileSystem, lookback: int = 8) -> str:
    """Newest complete 6-hourly init (those carry the 15-day horizon)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t = now - timedelta(hours=now.hour % 6)
    for _ in range(lookback):
        run = f"{ROOT}/{t:%Y%m%d_%H}hr_01_preds"
        if fs.exists(f"{run}/success"):
            return run
        t -= timedelta(hours=6)
    raise RuntimeError("no complete WeatherNext 3 run found in the last two days")


class Run:
    def __init__(self, cache_root: Path):
        self.fs = filesystem()
        self.path = latest_run(self.fs)
        self.name = self.path.rsplit("/", 1)[1]
        self.ds = xr.open_zarr(self.fs.get_mapper(f"{self.path}/predictions.zarr"), consolidated=False, chunks=None)
        self.valid_utc = pd.DatetimeIndex(self.ds.datetime.values)
        self.init_utc = pd.Timestamp(self.ds.init_time.values)
        self.lats = self.ds.lat_0p1.sel(lat_0p1=LAT).values
        self.lons = self.ds.lon_0p1.sel(lon_0p1=LON).values
        self.cache = cache_root / self.name
        self.cache.mkdir(parents=True, exist_ok=True)
        for old in sorted(p for p in cache_root.iterdir() if p.is_dir())[:-KEEP_RUNS]:
            shutil.rmtree(old, ignore_errors=True)

    def field(self, var: str, lead: int) -> np.ndarray:
        f = self.cache / f"{var}_{lead:03d}.npy"
        if f.exists():
            return np.load(f)
        arr = self.ds[var].isel(lead_time=lead).sel(lat_0p1=LAT, lon_0p1=LON).values.astype(np.float32)
        tmp = f.with_suffix(".tmp.npy")
        np.save(tmp, arr)
        tmp.replace(f)
        return arr

    def fields(self, wanted: list[tuple[str, int]]) -> dict[tuple[str, int], np.ndarray]:
        with ThreadPoolExecutor(WORKERS) as pool:
            arrays = pool.map(lambda vl: self.field(*vl), wanted)
            return dict(zip(wanted, arrays))
