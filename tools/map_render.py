"""Inline Leaflet choropleths for model-authored HTML reports.

Report iframes are fetched with ?token=, which a relative iframe src would
drop, so each map is inlined (GeoJSON included) instead of linked.
"""

from __future__ import annotations

import json

LEAFLET = (
    '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">\n'
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n'
    "<style>.hazmap{height:520px;border:1px solid #ccc;border-radius:6px;margin:12px 0}"
    ".hazlegend{background:#fff;padding:6px 8px;font:12px sans-serif;line-height:18px}"
    ".hazlegend i{display:inline-block;width:14px;height:14px;margin-right:6px;vertical-align:middle}</style>\n"
)

SCRIPT = """<div id="{div}" class="hazmap"></div>
<script>(function(){{
var data={data};var metric="{metric}";
function color(v){{return v>=0.66?"#b2182b":v>=0.33?"#ef8a62":v>0?"#fddbc7":"#f7f7f7";}}
function fmt(v){{return v===null||v===undefined?"–":(typeof v==="number"?v.toFixed(2):v);}}
var map=L.map("{div}",{{scrollWheelZoom:false}});
L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",{{maxZoom:12,attribution:"© OpenStreetMap · WeatherNext 3 (Google DeepMind)"}}).addTo(map);
var layer=L.geoJSON(data,{{style:function(f){{return {{fillColor:color(f.properties[metric]),weight:0.5,color:"#555",fillOpacity:0.75}};}},
onEachFeature:function(f,l){{var p=f.properties;l.bindPopup("<b>"+p.name+"</b> ("+p.province+")<br>"+
"{label}: <b>"+fmt(p[metric])+"</b> on "+fmt(p[metric+"_day"])+"<br>"+
"agro "+fmt(p.agro_score)+" · oil&amp;gas "+fmt(p.oil_gas_score)+"<br>"+
"min p10 temp "+fmt(p.tmin_p10_c)+" °C · max p90 temp "+fmt(p.tmax_p90_c)+" °C<br>"+
"max daily rain "+fmt(p.rain_mean_mm_max)+" mm · p90 wind "+fmt(p.wind_p90_kmh)+" km/h");}}}}).addTo(map);
map.fitBounds(layer.getBounds());
var lg=L.control({{position:"bottomright"}});lg.onAdd=function(){{var d=L.DomUtil.create("div","hazlegend");
d.innerHTML="<b>{label}</b><br><i style='background:#b2182b'></i>high ≥0.66<br><i style='background:#ef8a62'></i>moderate ≥0.33<br><i style='background:#fddbc7'></i>low<br><i style='background:#f7f7f7'></i>none";return d;}};lg.addTo(map);
}})();</script>
"""

LABELS = {
    "agro_score": "Agro hazard score",
    "oil_gas_score": "Oil & gas hazard score",
    "frost": "Frost severity",
    "heat": "Heat severity",
    "rain": "Heavy rain severity",
    "wind": "Wind severity",
}


def map_block(geojson: dict, metric: str, index: int) -> str:
    return SCRIPT.format(
        div=f"hazmap-{index}", data=json.dumps(geojson, separators=(",", ":")), metric=metric, label=LABELS[metric]
    )
