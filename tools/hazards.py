"""Daily hazard severities per departamento from WeatherNext 3 ensemble percentiles.

Argentina has no DST: local time is UTC-3 all year. A day's numbers use only
the lead hours that fall in it, so the first (partial) day may miss its
pre-dawn or afternoon window; those values are NaN, not zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .wn3_client import Run

ASSETS = Path(__file__).resolve().parent.parent / "assets"
LOCAL = pd.Timedelta(hours=-3)
FROST_HOURS = range(3, 9)  # local pre-dawn, coldest of the night
HEAT_HOURS = range(12, 18)  # local afternoon
WIND_STEP = 3  # p90 wind sampled every 3 h; a daily max does not need every hour

# variable -> (local hours used, unit conversion)
VARS = {
    "temperature_2m_p10": (FROST_HOURS, lambda a: a - 273.15),
    "temperature_2m_p90": (HEAT_HOURS, lambda a: a - 273.15),
    "total_precipitation_1hr_mean": (range(24), lambda a: a * 1000.0),
    "wind_speed_10m_p90": (range(0, 24, WIND_STEP), lambda a: a * 3.6),
}

# Severity 0..1: linear between "starts to matter" and "severe".
SEVERITY = {
    "frost": ("tmin_p10_c", 2.0, -4.0),
    "heat": ("tmax_p90_c", 32.0, 40.0),
    "rain": ("rain_mean_mm_max", 20.0, 80.0),
    "wind": ("wind_p90_kmh", 40.0, 90.0),
}
SECTOR_WEIGHTS = {
    "agro": {"frost": 1.0, "heat": 1.0, "rain": 1.0, "wind": 0.6},
    "oil_gas": {"frost": 0.3, "heat": 0.5, "rain": 1.0, "wind": 1.0},
}
HAZARDS = list(SEVERITY)
METRICS = HAZARDS + [f"{s}_score" for s in SECTOR_WEIGHTS]


def risk_class(score: float) -> str:
    if score >= 0.66:
        return "high"
    if score >= 0.33:
        return "moderate"
    return "low" if score > 0 else "none"


def load_departamentos() -> dict:
    return json.loads((ASSETS / "departamentos.geojson").read_text())


def _cells() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lk = pd.read_csv(ASSETS / "cell_lookup.csv")
    li = np.rint((lk["lat"].to_numpy() + 56.0) * 10).astype(int)
    lo = np.rint((lk["lon"].to_numpy() - 285.0) * 10).astype(int)
    return li, lo, lk["dept_id"].to_numpy()


def compute(run: Run, days: int) -> pd.DataFrame:
    """One row per (departamento, local day) with raw values and severities."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("h")
    first = int((run.valid_utc < now).sum())
    leads = np.arange(first, min(first + days * 24, len(run.valid_utc)))
    local = run.valid_utc[leads] + LOCAL
    wanted = [
        (var, int(lead))
        for var, (hours, _) in VARS.items()
        for lead, t in zip(leads, local)
        if t.hour in hours
    ]
    fields = run.fields(wanted)

    li, lo, dept = _cells()
    rows = []
    for day in sorted(set(local.normalize())):
        in_day = [(int(l), t) for l, t in zip(leads, local) if t.normalize() == day]

        def stack(var: str) -> np.ndarray | None:
            hours, conv = VARS[var]
            got = [conv(fields[(var, l)][li, lo]) for l, t in in_day if t.hour in hours]
            return np.stack(got) if got else None

        cell = pd.DataFrame({"dept_id": dept})
        t10, t90, rain, wind = (stack(v) for v in VARS)
        cell["tmin_p10_c"] = np.nanmin(t10, 0) if t10 is not None else np.nan
        cell["tmax_p90_c"] = np.nanmax(t90, 0) if t90 is not None else np.nan
        cell["rain_mean_mm"] = np.nansum(rain, 0) if rain is not None else np.nan
        cell["wind_p90_kmh"] = np.nanmax(wind, 0) if wind is not None else np.nan
        cell["frost_cell"] = cell["tmin_p10_c"] < 0
        g = cell.groupby("dept_id").agg(
            # Median cell, not the extreme one: Andean peaks would otherwise
            # put frost on every mountain departamento every night.
            tmin_p10_c=("tmin_p10_c", "median"),
            tmax_p90_c=("tmax_p90_c", "median"),
            rain_mean_mm_avg=("rain_mean_mm", "mean"),
            rain_mean_mm_max=("rain_mean_mm", "max"),
            wind_p90_kmh=("wind_p90_kmh", "max"),
            frost_area_pct=("frost_cell", "mean"),
        )
        g["frost_area_pct"] *= 100
        g["local_day"] = day.date().isoformat()
        g["hours_covered"] = len(in_day)
        rows.append(g.reset_index())

    df = pd.concat(rows, ignore_index=True)
    for hazard, (col, start, severe) in SEVERITY.items():
        df[hazard] = ((df[col] - start) / (severe - start)).clip(0, 1).fillna(0)
    for sector, w in SECTOR_WEIGHTS.items():
        df[f"{sector}_score"] = np.max([df[h] * k for h, k in w.items()], axis=0)
    return df.round(3)


def summarize(daily: pd.DataFrame, depts: dict) -> pd.DataFrame:
    """One row per departamento: the worst day of each hazard and sector score."""
    meta = pd.DataFrame([f["properties"] for f in depts["features"]]).set_index("id")
    out = meta[["name", "province", "lat", "lon"]].copy()
    for m in METRICS:
        idx = daily.groupby("dept_id")[m].idxmax()
        peak = daily.loc[idx].set_index("dept_id")
        out[m] = peak[m]
        out[f"{m}_day"] = peak["local_day"]
    raw = daily.groupby("dept_id").agg(
        tmin_p10_c=("tmin_p10_c", "min"),
        tmax_p90_c=("tmax_p90_c", "max"),
        rain_mean_mm_max=("rain_mean_mm_max", "max"),
        rain_week_mm_avg=("rain_mean_mm_avg", "sum"),
        wind_p90_kmh=("wind_p90_kmh", "max"),
    )
    out = out.join(raw)
    for sector in SECTOR_WEIGHTS:
        out[f"{sector}_class"] = out[f"{sector}_score"].map(risk_class)
    out.index.name = "dept_id"
    return out.round(3)


def to_geojson(summary: pd.DataFrame, depts: dict, provinces: list[str] | None) -> dict:
    rows = summary.replace({np.nan: None}).to_dict("index")
    feats = []
    for f in depts["features"]:
        p = f["properties"]
        if provinces and p["province"] not in provinces:
            continue
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {"id": p["id"], **rows[p["id"]]}})
    return {"type": "FeatureCollection", "features": feats}
