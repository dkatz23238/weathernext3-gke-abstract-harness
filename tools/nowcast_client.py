"""Google Maps Platform Weather API: minute nowcast and hourly forecast for a point."""

from __future__ import annotations

import os

import requests

BASE = "https://weather.googleapis.com/v1"


def _get(path: str, lat: float, lon: float, **params) -> dict:
    key = os.environ.get("GOOGLE_WEATHER_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_WEATHER_API_KEY is not set; the nowcast is unavailable")
    params.update({"key": key, "location.latitude": lat, "location.longitude": lon})
    r = requests.get(f"{BASE}/{path}", params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Weather API {r.status_code}: {r.text[:300]}")
    return r.json()


def minutes(lat: float, lon: float) -> dict:
    segments, token = [], None
    while True:
        extra = {"pageToken": token} if token else {}
        page = _get("forecast/minutes:lookup", lat, lon, pageSize=360, **extra)
        segments += page.get("segments", [])
        token = page.get("nextPageToken")
        if not token:
            return {**page, "segments": segments}


def hours(lat: float, lon: float, n: int) -> list[dict]:
    out, token = [], None
    while len(out) < n:
        extra = {"pageToken": token} if token else {}
        page = _get("forecast/hours:lookup", lat, lon, hours=n, pageSize=min(n, 24), **extra)
        out += page.get("forecastHours", [])
        token = page.get("nextPageToken")
        if not token:
            break
    return out[:n]
