---
name: hazard-reports
description: The hazard model (thresholds, severities, sector scores) and the structure of a Metero HTML hazard report. Applies to every report.
---

# Hazard model

All values come from the WeatherNext 3 ensemble statistics, per local day
(Argentina, UTC-3), per departamento (all 0.1° cells inside it).

| Hazard | Value (per departamento and day) | Severity 0 at | Severity 1 at |
|---|---|---|---|
| Frost | median cell, p10 of 2 m temperature, 03-08 h local | 2 °C | -4 °C |
| Heat | median cell, p90 of 2 m temperature, 12-17 h local | 32 °C | 40 °C |
| Heavy rain | wettest cell, ensemble-mean 24 h rain | 20 mm | 80 mm |
| Wind | windiest cell, p90 10 m wind (3-hourly) | 40 km/h | 90 km/h |

Severity is linear in between. Sector score = max of weighted severities:
agro = frost 1.0, heat 1.0, rain 1.0, wind 0.6; oil & gas = wind 1.0,
rain 1.0, heat 0.5, frost 0.3. Classes: high ≥ 0.66, moderate ≥ 0.33,
low > 0, none.

How to read it:
- A p10 frost means "at least ~10% of the ensemble freezes there"; say
  "cold scenario" and give the p10 °C. frost_area_pct is the share of the
  departamento's cells whose cold scenario goes below 0 °C.
- A p90 heat or wind value is the "bad scenario", not the expected value.
- Rain uses the ensemble mean: an expected amount, so it understates local
  convective peaks. Say so when rain drives a high class.
- Skill drops with lead time. Days 1-3: act on it. Days 4-7: plan on it.
  Days 8-15: watch list only.
- Andean departamentos (west Mendoza, San Juan, La Rioja, Catamarca, Jujuy,
  Salta, Neuquén) are mostly mountain cells that freeze most nights; their
  frost is real but not crop frost. Rank the agricultural belt separately
  and mention the Andean ones as a group, unless the user asks about them
  or about valley crops (vineyards, orchards) there.
- The first local day can be partial (hours_covered < 24); its frost or
  heat value may be missing.

# Report structure

One file, `reports/<topic>_<yyyy-mm-dd>.html`, inline CSS, readable on a
laptop. Leaflet and map tiles are added by embed_hazard_maps; add no other
scripts or charts.

1. **Header**: title, sector(s), area, forecast run (init time UTC),
   days covered, generated date.
2. **Executive summary**: 5-8 bullets, each a place + day + value + action.
3. **KPI cards**: departamentos at high / moderate per sector, worst value
   per hazard (with place and day).
4. **Map**: `<!-- MAP:agro_score -->` and/or `<!-- MAP:oil_gas_score -->`,
   then a map of the dominant hazard (`<!-- MAP:frost -->`, `heat`, `rain`
   or `wind`). One short caption under each map.
5. **Ranked tables**: top departamentos per sector (name, province, score,
   class, driving hazard, peak day, raw value with unit), ≤ 15 rows.
6. **Timeline**: worst departamentos × days table of the driving value,
   cells colored by class.
7. **Next hours (nowcast)**: per flagged place, the 6 h minute nowcast
   total, max probability and first wet minute; the hourly forecast for
   the peak day when available.
8. **Recommended actions**: per sector, concrete and dated.
9. **Methodology and caveats**: WeatherNext 3 run, percentiles used,
   thresholds, lead-time skill, rain caveat, sources (WeatherNext 3 by
   Google DeepMind, Google Weather API, geoBoundaries ARG ADM2 CC BY).

Colors: high #b2182b, moderate #ef8a62, low #fddbc7, none #f7f7f7 (the
maps use the same scale). Every number has a unit; days as "Thu 19 Nov".
