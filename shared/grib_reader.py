"""
GRIB weather file reader for pi-deck-tools.

Reads 10m U/V wind components from GRIB/GRIB2 files using cfgrib + xarray.
All data is loaded into numpy arrays at init time so that wind_at() is fast
and free from xarray coordinate-selection edge cases.

Dependencies (Pi install):
    sudo apt install libeccodes-dev libeccodes-tools
    pip install cfgrib xarray scipy
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class WindAtPoint:
    twd_deg: float   # True Wind Direction (degrees, 0–360, FROM)
    tws_kn: float    # True Wind Speed (knots)
    twa_deg: float   # True Wind Angle relative to course (-180 to +180)
    aws_kn: float    # Apparent Wind Speed (knots)
    awa_deg: float   # Apparent Wind Angle (-180 to +180, +ve starboard)


@dataclass
class WaveAtPoint:
    wv_dir_deg: float   # Mean wave direction coming FROM (0–360)
    wv_ang_deg: float   # Wave angle relative to bow (-180 to +180, +ve starboard)
    wv_ht_m: float      # Significant wave height (metres)


# ---------------------------------------------------------------------------
# Internal math helpers
# ---------------------------------------------------------------------------

def _ms_to_knots(ms: float) -> float:
    return ms * 1.94384


def _uv_to_dir_speed(u: float, v: float) -> tuple[float, float]:
    """u/v → (meteorological direction the wind comes FROM °, speed m/s)."""
    speed = math.sqrt(u * u + v * v)
    direction = (math.degrees(math.atan2(u, v)) + 180.0) % 360.0
    return direction, speed


def _twa(twd_deg: float, course_deg: float) -> float:
    """Signed True Wind Angle relative to course (-180 to +180, +ve starboard)."""
    return (twd_deg - course_deg + 180.0) % 360.0 - 180.0


def _apparent_wind(tws_ms: float, twa_deg: float, boat_speed_kn: float) -> tuple[float, float]:
    """Return (AWS knots, AWA degrees) from TWS m/s, TWA °, boat speed knots."""
    boat_ms = boat_speed_kn / 1.94384
    twa_rad = math.radians(twa_deg)
    aw_x = tws_ms * math.cos(twa_rad) + boat_ms
    aw_y = tws_ms * math.sin(twa_rad)
    aws_ms = math.sqrt(aw_x * aw_x + aw_y * aw_y)
    return _ms_to_knots(aws_ms), math.degrees(math.atan2(aw_y, aw_x))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

class GribReader:
    """
    Load a GRIB/GRIB2 file and interpolate wind at (lat, lon, time) points.

    All u10/v10 data is loaded into numpy arrays on construction so that
    wind_at() is a simple array lookup with no xarray overhead.

    Usage:
        reader = GribReader("path/to/file.grb2")
        wind = reader.wind_at(lat=37.5, lon=-8.3, time_utc=dt,
                              course_deg=245.0, boat_speed_kn=5.0)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lats: "np.ndarray | None" = None
        self._lons: "np.ndarray | None" = None
        self._u10: "np.ndarray | None" = None   # shape (N_times, N_lat, N_lon)
        self._v10: "np.ndarray | None" = None
        # Wave arrays — None if not present in GRIB file
        self._wave_lats: "np.ndarray | None" = None
        self._wave_lons: "np.ndarray | None" = None
        self._swh: "np.ndarray | None" = None   # significant wave height (m)
        self._mwd: "np.ndarray | None" = None   # mean wave direction (°, meteorological FROM)
        self._wave_times: list[datetime] = []
        self.valid_times: list[datetime] = []
        self.lat_min = self.lat_max = self.lon_min = self.lon_max = 0.0
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            import xarray as xr
        except ImportError as exc:
            raise ImportError(
                "cfgrib and xarray are required for GRIB reading.\n"
                "On the Pi: sudo apt install libeccodes-dev libeccodes-tools\n"
                "           pip install cfgrib xarray scipy"
            ) from exc

        import numpy as np
        import pandas as pd

        if not self.path.exists():
            raise FileNotFoundError(f"GRIB file not found: {self.path}")

        ds_u = xr.open_dataset(
            str(self.path), engine="cfgrib",
            filter_by_keys={"shortName": "10u"}, indexpath=None,
        )
        ds_v = xr.open_dataset(
            str(self.path), engine="cfgrib",
            filter_by_keys={"shortName": "10v"}, indexpath=None,
        )

        # --- latitude / longitude arrays ---------------------------------
        self._lats = ds_u.coords["latitude"].values.flatten()
        self._lons = ds_u.coords["longitude"].values.flatten()

        # --- valid times -------------------------------------------------
        # GFS files: valid_time is a 1-D coord indexed by "step".
        # Fall back to "time" for analysis-only files.
        if "valid_time" in ds_u.coords:
            raw_times = ds_u.coords["valid_time"].values
        elif "time" in ds_u.coords:
            raw_times = ds_u.coords["time"].values
        else:
            raise ValueError("No time coordinate found in GRIB dataset.")

        raw_times = np.atleast_1d(raw_times).flatten()
        parsed = [
            pd.Timestamp(t).to_pydatetime().replace(tzinfo=timezone.utc)
            for t in raw_times
        ]
        # Sort chronologically, keeping the mapping to original step indices.
        order = sorted(range(len(parsed)), key=lambda i: parsed[i])
        self.valid_times = [parsed[i] for i in order]

        # --- wind arrays -------------------------------------------------
        u_raw = ds_u["u10"].values
        v_raw = ds_v["v10"].values
        ds_u.close()
        ds_v.close()

        # Guarantee shape (N_times, N_lat, N_lon)
        if u_raw.ndim == 2:
            u_raw = u_raw[np.newaxis]
            v_raw = v_raw[np.newaxis]

        self._u10 = u_raw[order]
        self._v10 = v_raw[order]

        # --- wave arrays (optional — WW3 fields not present in all GRIBs) ----
        self._load_waves(xr, np, pd)

        # --- spatial coverage summary ------------------------------------
        self.lat_min = float(self._lats.min())
        self.lat_max = float(self._lats.max())
        self.lon_min = float(self._lons.min())
        self.lon_max = float(self._lons.max())

    def _load_waves(self, xr, np, pd) -> None:
        """Attempt to load significant wave height and mean wave direction.

        XyGrib WW3 / GFS GRIB2 files may use various shortNames. We try each
        in turn and pick the first that opens.  We intentionally read the
        *first data variable* from the returned dataset rather than indexing
        by shortName, because cfgrib internally uses the CF variable name
        (which often differs from the GRIB shortName).
        """
        SWH_NAMES = ["swh", "shww", "htsgw", "HTSGW"]
        MWD_NAMES = ["mwd", "mdww", "mwavd", "wvdir", "WVDIR", "dirpw", "dirsw"]

        def _try_load(short_names):
            for name in short_names:
                try:
                    ds = xr.open_dataset(
                        str(self.path), engine="cfgrib",
                        filter_by_keys={"shortName": name}, indexpath=None,
                    )
                    # Verify the dataset actually contains data variables.
                    if len(ds.data_vars) == 0:
                        ds.close()
                        continue
                    return ds
                except Exception:
                    continue
            return None

        ds_swh = _try_load(SWH_NAMES)
        ds_mwd = _try_load(MWD_NAMES)

        if ds_swh is None:
            return  # No usable wave-height field in this GRIB

        if ds_mwd is None:
            # Fallback: scan all datasets and pick a wave-direction-like field.
            try:
                for ds in xr.open_datasets(str(self.path), engine="cfgrib", indexpath=None):
                    for var_name in ds.data_vars:
                        attrs = ds[var_name].attrs
                        short_name = str(attrs.get("GRIB_shortName", "")).lower()
                        grib_name = str(attrs.get("GRIB_name", "")).lower()
                        long_name = str(attrs.get("long_name", "")).lower()
                        std_name = str(attrs.get("standard_name", "")).lower()
                        units = str(attrs.get("units", "")).lower()

                        text = " ".join([short_name, grib_name, long_name, std_name])
                        looks_like_direction = "direction" in text and "wave" in text
                        known_short = short_name in {"wvdir", "dirpw", "dirsw", "mwd", "mdww", "mwavd"}
                        if (looks_like_direction or known_short) and ("degree" in units or units in {"deg", "degrees"}):
                            ds_mwd = ds
                            break
                    if ds_mwd is not None:
                        break
            except Exception:
                ds_mwd = None

        try:
            self._wave_lats = ds_swh.coords["latitude"].values.flatten()
            self._wave_lons = ds_swh.coords["longitude"].values.flatten()

            if "valid_time" in ds_swh.coords:
                raw_times = ds_swh.coords["valid_time"].values
            else:
                raw_times = ds_swh.coords["time"].values
            raw_times = np.atleast_1d(raw_times).flatten()
            parsed = [
                pd.Timestamp(t).to_pydatetime().replace(tzinfo=timezone.utc)
                for t in raw_times
            ]
            order = sorted(range(len(parsed)), key=lambda i: parsed[i])
            self._wave_times = [parsed[i] for i in order]

            # Use the first data variable in each dataset — cfgrib names
            # variables by CF standard name, not by GRIB shortName.
            swh_var = next(iter(ds_swh.data_vars))
            swh_raw = ds_swh[swh_var].values
            ds_swh.close()

            if swh_raw.ndim == 2:
                swh_raw = swh_raw[np.newaxis]

            self._swh = swh_raw[order]

            if ds_mwd is not None:
                mwd_var = next(iter(ds_mwd.data_vars))
                mwd_raw = ds_mwd[mwd_var].values
                ds_mwd.close()
                if mwd_raw.ndim == 2:
                    mwd_raw = mwd_raw[np.newaxis]
                self._mwd = mwd_raw[order]
            else:
                self._mwd = None
        except Exception:
            self._swh = None
            self._mwd = None
            self._wave_times = []

    @property
    def has_wave_height(self) -> bool:
        return self._swh is not None and len(self._wave_times) > 0

    @property
    def has_wave_direction(self) -> bool:
        return self._mwd is not None and len(self._wave_times) > 0

    @property
    def has_waves(self) -> bool:
        return self.has_wave_height and self.has_wave_direction

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def coverage_summary(self) -> str:
        if not self.valid_times:
            return "No time steps found."
        start = self.valid_times[0].strftime("%Y-%m-%d %H:%MZ")
        end = self.valid_times[-1].strftime("%Y-%m-%d %H:%MZ")
        return (
            f"{len(self.valid_times)} steps  {start} → {end}  "
            f"Lat {self.lat_min:.1f}–{self.lat_max:.1f}  "
            f"Lon {self.lon_min:.1f}–{self.lon_max:.1f}"
        )

    def wind_at(
        self,
        lat: float,
        lon: float,
        time_utc: datetime,
        course_deg: float,
        boat_speed_kn: float,
    ) -> WindAtPoint:
        """
        Nearest-grid-point lookup + linear time interpolation.

        Args:
            lat / lon: decimal degrees (lon negative = West is fine).
            time_utc: UTC datetime (naive or aware).
            course_deg: True course for TWA calculation.
            boat_speed_kn: Boat speed for apparent wind calculation.
        """
        if time_utc.tzinfo is None:
            time_utc = time_utc.replace(tzinfo=timezone.utc)

        n = len(self.valid_times)
        if n == 1 or time_utc <= self.valid_times[0]:
            u, v = self._uv_at(0, lat, lon)
        elif time_utc >= self.valid_times[-1]:
            u, v = self._uv_at(n - 1, lat, lon)
        else:
            i_before = max(i for i, t in enumerate(self.valid_times) if t <= time_utc)
            i_after = i_before + 1
            span = (self.valid_times[i_after] - self.valid_times[i_before]).total_seconds()
            frac = (time_utc - self.valid_times[i_before]).total_seconds() / span
            u0, v0 = self._uv_at(i_before, lat, lon)
            u1, v1 = self._uv_at(i_after, lat, lon)
            u = u0 + (u1 - u0) * frac
            v = v0 + (v1 - v0) * frac

        twd_deg, tws_ms = _uv_to_dir_speed(u, v)
        twa_deg = _twa(twd_deg, course_deg)
        aws_kn, awa_deg = _apparent_wind(tws_ms, twa_deg, boat_speed_kn)

        return WindAtPoint(
            twd_deg=round(twd_deg, 1),
            tws_kn=round(_ms_to_knots(tws_ms), 1),
            twa_deg=round(twa_deg, 1),
            aws_kn=round(aws_kn, 1),
            awa_deg=round(awa_deg, 1),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _uv_at(self, time_idx: int, lat: float, lon: float) -> tuple[float, float]:
        """Nearest-grid-point u10, v10 at a given time index."""
        import numpy as np

        # Wrap lon to match dataset convention (0–360 vs −180–180).
        lon_q = lon + 360.0 if (self._lons.min() >= 0.0 and lon < 0.0) else lon

        lat_i = int(np.argmin(np.abs(self._lats - lat)))
        lon_i = int(np.argmin(np.abs(self._lons - lon_q)))

        return float(self._u10[time_idx, lat_i, lon_i]), float(self._v10[time_idx, lat_i, lon_i])

    def _wave_height_at_index(self, time_idx: int, lat: float, lon: float) -> float:
        """Nearest-grid-point significant wave height (m) at a given wave time index."""
        import numpy as np

        lon_q = lon + 360.0 if (self._wave_lons.min() >= 0.0 and lon < 0.0) else lon
        lat_i = int(np.argmin(np.abs(self._wave_lats - lat)))
        lon_i = int(np.argmin(np.abs(self._wave_lons - lon_q)))

        return float(self._swh[time_idx, lat_i, lon_i])

    def _wave_dir_at_index(self, time_idx: int, lat: float, lon: float) -> float:
        """Nearest-grid-point wave direction (° FROM) at a given wave time index."""
        import numpy as np

        lon_q = lon + 360.0 if (self._wave_lons.min() >= 0.0 and lon < 0.0) else lon
        lat_i = int(np.argmin(np.abs(self._wave_lats - lat)))
        lon_i = int(np.argmin(np.abs(self._wave_lons - lon_q)))

        return float(self._mwd[time_idx, lat_i, lon_i])

    def _time_interp_scalar(self, times: list[datetime], time_utc: datetime, at_index) -> float:
        """Linear interpolation helper for scalar fields over time."""
        n = len(times)
        if n == 1 or time_utc <= times[0]:
            return at_index(0)
        if time_utc >= times[-1]:
            return at_index(n - 1)

        i_before = max(i for i, t in enumerate(times) if t <= time_utc)
        i_after = i_before + 1
        span = (times[i_after] - times[i_before]).total_seconds()
        frac = (time_utc - times[i_before]).total_seconds() / span
        v0 = at_index(i_before)
        v1 = at_index(i_after)
        return v0 + (v1 - v0) * frac

    def _time_interp_angle(self, times: list[datetime], time_utc: datetime, at_index) -> float:
        """Linear interpolation helper for angles, wrapping through shorter arc."""
        n = len(times)
        if n == 1 or time_utc <= times[0]:
            return at_index(0) % 360.0
        if time_utc >= times[-1]:
            return at_index(n - 1) % 360.0

        i_before = max(i for i, t in enumerate(times) if t <= time_utc)
        i_after = i_before + 1
        span = (times[i_after] - times[i_before]).total_seconds()
        frac = (time_utc - times[i_before]).total_seconds() / span
        a0 = at_index(i_before) % 360.0
        a1 = at_index(i_after) % 360.0
        diff = ((a1 - a0 + 180.0) % 360.0) - 180.0
        return (a0 + diff * frac) % 360.0

    def wave_height_at(self, lat: float, lon: float, time_utc: datetime) -> float:
        """Return significant wave height (m)."""
        if not self.has_wave_height:
            raise ValueError("No wave height data in this GRIB file.")
        if time_utc.tzinfo is None:
            time_utc = time_utc.replace(tzinfo=timezone.utc)

        return self._time_interp_scalar(
            self._wave_times,
            time_utc,
            lambda idx: self._wave_height_at_index(idx, lat, lon),
        )

    def wave_direction_at(self, lat: float, lon: float, time_utc: datetime) -> float:
        """Return mean wave direction (° FROM)."""
        if not self.has_wave_direction:
            raise ValueError("No wave direction data in this GRIB file.")
        if time_utc.tzinfo is None:
            time_utc = time_utc.replace(tzinfo=timezone.utc)

        return self._time_interp_angle(
            self._wave_times,
            time_utc,
            lambda idx: self._wave_dir_at_index(idx, lat, lon),
        )

    def wave_at(
        self,
        lat: float,
        lon: float,
        time_utc: datetime,
        course_deg: float,
    ) -> WaveAtPoint:
        """Return wave conditions at (lat, lon, time), with angle relative to bow."""
        if not self.has_waves:
            raise ValueError("No wave data in this GRIB file.")

        swh = self.wave_height_at(lat, lon, time_utc)
        mwd = self.wave_direction_at(lat, lon, time_utc)

        wv_ang = _twa(mwd, course_deg)  # same signed-angle formula as TWA
        return WaveAtPoint(
            wv_dir_deg=round(mwd % 360.0, 1),
            wv_ang_deg=round(wv_ang, 1),
            wv_ht_m=round(max(0.0, swh), 2),
        )
