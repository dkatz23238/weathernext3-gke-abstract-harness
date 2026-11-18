"""Tools over WeatherNext 3 (15-day ensemble) and the Weather API nowcast.

Big results land in the thread workspace (data/, reports/) and the tools return
small summaries, so the sandbox never carries a whole forecast.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from pydantic_ai.toolsets import FunctionToolset

from . import hazards, map_render, nowcast_client
from .wn3_client import Run

MARKER = re.compile(r"<!--\s*MAP:([a-z_]+)\s*-->")


def register(ctx) -> FunctionToolset:
    ts = FunctionToolset(max_retries=1)
    workspace = Path(ctx.workspace) if ctx.workspace else Path.cwd()
    cache = Path(ctx.data_dir) / "wn3_cache"

    def tool(func):
        ts.add_function(func=func, takes_ctx=False)
        return func

    @tool
    def get_latest_forecast_run() -> dict:
        """The newest complete WeatherNext 3 run: its id, initialization time
        (UTC), the first and last valid hour, and the hazard model's
        thresholds and sector weights. Call this first and cite the run in
        every report."""
        run = Run(cache)
        return {
            "run": run.name,
            "init_time_utc": str(run.init_utc),
            "valid_from_utc": str(run.valid_utc[0]),
            "valid_to_utc": str(run.valid_utc[-1]),
            "lead_hours": len(run.valid_utc),
            "severity_thresholds": {k: {"value": v[0], "starts": v[1], "severe": v[2]} for k, v in hazards.SEVERITY.items()},
            "sector_weights": hazards.SECTOR_WEIGHTS,
        }

    @tool
    def list_departamentos(province: str | None = None, name_contains: str | None = None) -> list[dict]:
        """Argentine departamentos/partidos (id, name, province, centroid
        lat/lon). Filter by exact province name (e.g. 'Buenos Aires',
        'Neuquén', 'Córdoba') and/or a case-insensitive name fragment."""
        rows = [f["properties"] for f in hazards.load_departamentos()["features"]]
        if province:
            rows = [r for r in rows if r["province"] == province]
        if name_contains:
            rows = [r for r in rows if name_contains.lower() in r["name"].lower()]
        return rows

    @tool
    def compute_departamento_hazards(days: int = 7, provinces: list[str] | None = None, top: int = 10) -> dict:
        """Frost, heat, heavy-rain and wind hazards for every Argentine
        departamento over the next `days` local days (1-15) from the latest
        WeatherNext 3 ensemble, plus an agro and an oil & gas score (0-1,
        classes none/low/moderate/high). Uses the ensemble percentiles: cold
        scenario p10 pre-dawn minimum (frost), hot scenario p90 afternoon
        maximum (heat), ensemble-mean daily rain (heaviest cell), p90 10 m
        wind. The first call on a new run downloads data and can take a few
        minutes; later calls reuse the cache.

        Writes data/hazards_daily_<run>.csv (one row per departamento and
        day), data/hazards_summary_<run>.csv (one row per departamento, worst
        day per hazard) and data/hazards_<run>.geojson (summary + polygons,
        limited to `provinces` when given — the input for embed_hazard_maps).
        Returns the file paths, class counts and the `top` departamentos per
        sector score and per hazard within `provinces`."""
        days = max(1, min(int(days), 15))
        run = Run(cache)
        depts = hazards.load_departamentos()
        daily = hazards.compute(run, days)
        summary = hazards.summarize(daily, depts)
        scope = summary if not provinces else summary[summary["province"].isin(provinces)]
        if provinces and scope.empty:
            known = sorted(summary["province"].unique())
            raise ValueError(f"no departamentos in {provinces}; provinces are {known}")

        data = workspace / "data"
        data.mkdir(parents=True, exist_ok=True)
        files = {
            "daily": f"data/hazards_daily_{run.name}.csv",
            "summary": f"data/hazards_summary_{run.name}.csv",
            "geojson": f"data/hazards_{run.name}.geojson",
        }
        daily.merge(summary[["name", "province"]], left_on="dept_id", right_index=True).to_csv(workspace / files["daily"], index=False)
        summary.to_csv(workspace / files["summary"])
        (workspace / files["geojson"]).write_text(json.dumps(hazards.to_geojson(summary, depts, provinces)))

        def ranked(metric: str, extra: list[str]) -> list[dict]:
            cols = ["name", "province", metric, f"{metric}_day", *extra]
            return scope.sort_values(metric, ascending=False).head(top)[cols].reset_index().to_dict("records")

        raw = ["tmin_p10_c", "tmax_p90_c", "rain_mean_mm_max", "wind_p90_kmh"]
        return {
            "run": run.name,
            "init_time_utc": str(run.init_utc),
            "local_days": sorted(daily["local_day"].unique().tolist()),
            "departamentos_in_scope": len(scope),
            "files": files,
            "class_counts": {s: scope[f"{s}_class"].value_counts().to_dict() for s in hazards.SECTOR_WEIGHTS},
            "top_agro": ranked("agro_score", ["frost", "heat", "rain", "wind"]),
            "top_oil_gas": ranked("oil_gas_score", ["frost", "heat", "rain", "wind"]),
            "top_by_hazard": {h: ranked(h, raw) for h in hazards.HAZARDS},
        }

    @tool
    def get_nowcast(lat: float, lon: float) -> dict:
        """Minute-by-minute precipitation nowcast for the next 6 hours at one
        point (Google Weather API). Returns the timeframe, time zone and the
        segments compacted to start time, type, probability %, mm and
        intensity, plus totals. Use it for 'what happens in the next hours'
        at the departamentos a report flags."""
        raw = nowcast_client.minutes(lat, lon)
        segs = [
            {
                "start": s["timeFrame"]["startTime"],
                "end": s["timeFrame"]["endTime"],
                "type": s.get("type"),
                "probability_pct": s.get("probability"),
                "mm": (s.get("qpf") or {}).get("quantity"),
                "intensity": s.get("intensity"),
            }
            for s in raw.get("segments", [])
        ]
        wet = [s for s in segs if (s["mm"] or 0) > 0]
        return {
            "timeframe": raw.get("overallPredictionTimeframe"),
            "time_zone": (raw.get("timeZone") or {}).get("id"),
            "total_mm": round(sum(s["mm"] or 0 for s in segs), 2),
            "max_probability_pct": max((s["probability_pct"] or 0 for s in segs), default=0),
            "first_wet_segment": wet[0] if wet else None,
            "segments": segs,
        }

    @tool
    def get_hourly_forecast(lat: float, lon: float, hours: int = 48) -> list[dict]:
        """Hourly point forecast (Google Weather API, up to 240 h): local
        time, condition, temperature °C, precipitation probability % and mm,
        wind speed and gust km/h. A point check next to the WeatherNext 3
        grid view."""
        out = []
        for h in nowcast_client.hours(lat, lon, max(1, min(int(hours), 240))):
            precip = h.get("precipitation") or {}
            wind = h.get("wind") or {}
            out.append(
                {
                    "time": (h.get("interval") or {}).get("startTime"),
                    "condition": ((h.get("weatherCondition") or {}).get("description") or {}).get("text"),
                    "temp_c": (h.get("temperature") or {}).get("degrees"),
                    "precip_probability_pct": (precip.get("probability") or {}).get("percent"),
                    "precip_mm": (precip.get("qpf") or {}).get("quantity"),
                    "wind_kmh": (wind.get("speed") or {}).get("value"),
                    "gust_kmh": (wind.get("gust") or {}).get("value"),
                }
            )
        return out

    @tool
    def embed_hazard_maps(report_path: str, geojson_path: str) -> dict:
        """Put interactive maps into an HTML report you already wrote. In the
        report, place a marker comment where each map belongs:
        <!-- MAP:agro_score -->, <!-- MAP:oil_gas_score -->, <!-- MAP:frost -->,
        <!-- MAP:heat -->, <!-- MAP:rain --> or <!-- MAP:wind -->. This
        replaces every marker with a Leaflet choropleth of that metric from
        `geojson_path` (the data/hazards_<run>.geojson that
        compute_departamento_hazards wrote), with popups per departamento.
        Paths are relative to the workspace (e.g. reports/x.html). Run it
        after the last edit_file on the report: the markers are gone
        afterwards."""
        report = workspace / report_path
        html = report.read_text()
        geo = json.loads((workspace / geojson_path).read_text())
        found = MARKER.findall(html)
        bad = [m for m in found if m not in map_render.LABELS]
        if not found or bad:
            raise ValueError(f"markers found {found}; use <!-- MAP:metric --> with metric in {sorted(map_render.LABELS)}")
        counter = iter(range(len(found)))
        html = MARKER.sub(lambda m: map_render.map_block(geo, m.group(1), next(counter)), html)
        head = "</head>" if "</head>" in html else "<body>"
        html = html.replace(head, map_render.LEAFLET + head, 1) if head in html else map_render.LEAFLET + html
        report.write_text(html)
        return {"report": report_path, "maps": found, "size_kb": round(len(html) / 1024)}

    return ts
