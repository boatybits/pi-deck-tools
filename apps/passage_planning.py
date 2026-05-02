#!/usr/bin/env python3
"""
Passage Planning Tool

Route-based passage planning with GRIB wind overlay.

Capabilities:
- Load a route from OpenCPN.
- Select and load a GRIB2 weather file (e.g. from XyGrib).
- Generate a 3-hour timeline from a departure time and assumed boat speed.
- Populate TWD°, TWS kt, TWA°, AWS kt, AWA° columns from GRIB data
  via spatial + temporal interpolation.

Pi dependencies (install once):
    sudo apt install libeccodes-dev libeccodes-tools
    pip install cfgrib xarray scipy
"""

from __future__ import annotations

import math
import sys
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, ttk

from tksheet import Sheet

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.grib_reader import GribReader
from shared.opencpn_db import OpenCPNDbError, list_routes, route_with_waypoints
from shared.vnc_window import VNCToolWindow


EARTH_RADIUS_NM = 3440.065
TIMELINE_STEP_HOURS = 3


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in nautical miles."""
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_NM * c


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return initial course from point A to point B in degrees true."""
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlon_rad = math.radians(lon2 - lon1)

    x = math.sin(dlon_rad) * math.cos(lat2_rad)
    y = (
        math.cos(lat1_rad) * math.sin(lat2_rad)
        - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def interpolate_lat_lon(start: dict, end: dict, fraction: float) -> tuple[float, float]:
    """Linear interpolation between two route points."""
    lat = start["lat"] + ((end["lat"] - start["lat"]) * fraction)
    lon = start["lon"] + ((end["lon"] - start["lon"]) * fraction)
    return lat, lon


def next_three_hour_utc() -> datetime:
    """Return the next rounded 3-hour UTC departure suggestion."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    next_hour_block = ((now.hour // TIMELINE_STEP_HOURS) + 1) * TIMELINE_STEP_HOURS
    if next_hour_block >= 24:
        now = now + timedelta(days=1)
        next_hour_block = 0
    return now.replace(hour=next_hour_block)


class PassagePlanningTool(VNCToolWindow):
    """Route-based passage planning scaffold for later GRIB integration."""

    def __init__(self):
        super().__init__(title="Passage Planning", width=1140, height=760)
        self.route_data: dict | None = None
        self.route_names: list[str] = []
        self.grib_reader: GribReader | None = None
        self.table_headers = [
            "UTC",
            "Leg",
            "Lat",
            "Lon",
            "Course T",
            "Run NM",
            "Remain NM",
            "TWD°",
            "TWS kt",
            "TWA°",
            "AWS kt",
            "AWA°",
            "WvDir°",
            "WvAng°",
            "WvHt m",
        ]
        self.twa_column_index = 9
        # Timeline slider state
        self._slider_dragging = False
        self._slider_drag_start_x = 0
        self._slider_drag_start_frac = 0.0
        self.setup_ui()
        self.resizable(True, True)
        self.minsize(900, 600)
        self.refresh_routes()
        self._preseed_grib_path()

    def setup_ui(self) -> None:
        controls = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        controls.pack(fill=tk.X, pady=(0, 10))

        route_row = tk.Frame(controls, bg=self.COLOR_BG)
        route_row.pack(fill=tk.X, pady=3)
        tk.Label(route_row, text="Route", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG, width=12, anchor="w").pack(side=tk.LEFT)
        self.route_var = tk.StringVar()
        self.route_combo = ttk.Combobox(route_row, textvariable=self.route_var, state="readonly", width=42)
        self.route_combo.pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(route_row, text="Refresh", command=self.refresh_routes, bg="#34495e", fg="white", padx=12).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(route_row, text="Load Route", command=self.load_selected_route, bg="#2980b9", fg="white", padx=12).pack(side=tk.LEFT)

        grib_row = tk.Frame(controls, bg=self.COLOR_BG)
        grib_row.pack(fill=tk.X, pady=3)
        tk.Label(grib_row, text="GRIB File", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG, width=12, anchor="w").pack(side=tk.LEFT)
        self.grib_var = tk.StringVar()
        tk.Entry(grib_row, textvariable=self.grib_var, width=62, font=self.font_small).pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)
        tk.Button(grib_row, text="Browse", command=self.browse_grib, bg="#34495e", fg="white", padx=12).pack(side=tk.LEFT)
        tk.Button(grib_row, text="Load GRIB", command=self.load_grib, bg="#8e44ad", fg="white", padx=12).pack(side=tk.LEFT, padx=(6, 0))

        plan_row = tk.Frame(controls, bg=self.COLOR_BG)
        plan_row.pack(fill=tk.X, pady=3)
        tk.Label(plan_row, text="Departure UTC", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG, width=12, anchor="w").pack(side=tk.LEFT)
        self.departure_var = tk.StringVar(value=next_three_hour_utc().strftime("%Y-%m-%d %H:%M"))
        tk.Entry(plan_row, textvariable=self.departure_var, width=20, font=self.font_small).pack(side=tk.LEFT, padx=(0, 14))
        tk.Label(plan_row, text="Boat Speed (kt)", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG).pack(side=tk.LEFT)
        self.speed_var = tk.StringVar(value="5.0")
        tk.Entry(plan_row, textvariable=self.speed_var, width=8, font=self.font_small).pack(side=tk.LEFT, padx=(8, 14))
        tk.Button(plan_row, text="Build 3h Table", command=self.generate_plan, bg="#27ae60", fg="white", padx=14).pack(side=tk.LEFT)

        self.summary_var = tk.StringVar(value="Load an OpenCPN route to begin.")
        tk.Label(self.content_frame, textvariable=self.summary_var, font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG, anchor="w", justify=tk.LEFT).pack(fill=tk.X, pady=(0, 10))

        self.status_var = tk.StringVar(value="Load a route, select a GRIB file, then Build 3h Table.")
        tk.Label(self.content_frame, textvariable=self.status_var, font=self.font_small, bg=self.COLOR_BG, fg="#a8d8ff", anchor="w").pack(fill=tk.X, pady=(0, 8))

        table_frame = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        table_frame.pack(fill=tk.BOTH, expand=True)

        self.plan_sheet = Sheet(
            table_frame,
            headers=self.table_headers,
            data=[],
            show_row_index=False,
            show_x_scrollbar=True,
            show_y_scrollbar=True,
            align="center",
            header_align="center",
            theme="dark blue",
        )
        self.plan_sheet.enable_bindings()
        self.plan_sheet.pack(fill=tk.BOTH, expand=True)

        self._sheet_set_column_widths([130, 115, 88, 88, 68, 70, 82, 58, 58, 58, 58, 58, 60, 60, 60])

        # --- GRIB timeline slider -------------------------------------------
        slider_outer = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        slider_outer.pack(fill=tk.X, pady=(6, 0))

        tk.Label(
            slider_outer, text="Departure window",
            font=self.font_small, bg=self.COLOR_BG, fg="#a8d8ff",
        ).pack(anchor="w")

        self._slider_frame = tk.Frame(slider_outer, bg=self.COLOR_BG)
        self._slider_frame.pack(fill=tk.X)

        self._slider_canvas = tk.Canvas(
            self._slider_frame, height=28,
            bg="#1e2a38", highlightthickness=1, highlightbackground="#4a6fa5",
        )
        self._slider_canvas.pack(fill=tk.X, padx=4)

        self._slider_label_left  = tk.Label(self._slider_frame, text="", font=("Arial", 9), bg=self.COLOR_BG, fg="#7f8c8d")
        self._slider_label_right = tk.Label(self._slider_frame, text="", font=("Arial", 9), bg=self.COLOR_BG, fg="#7f8c8d")
        self._slider_label_left.pack(side=tk.LEFT, padx=4)
        self._slider_label_right.pack(side=tk.RIGHT, padx=4)

        self._slider_canvas.bind("<ButtonPress-1>",   self._slider_press)
        self._slider_canvas.bind("<B1-Motion>",       self._slider_drag)
        self._slider_canvas.bind("<ButtonRelease-1>", self._slider_release)
        self._slider_canvas.bind("<Configure>",       lambda _e: self._redraw_slider())

    def refresh_routes(self) -> None:
        try:
            routes = list_routes()
        except OpenCPNDbError as exc:
            self.show_error("OpenCPN Route Error", str(exc))
            self.status_var.set("Could not read OpenCPN route list.")
            return

        self.route_names = [route.name for route in routes if route.name]
        self.route_combo["values"] = self.route_names
        if self.route_names and not self.route_var.get():
            self.route_var.set(self.route_names[0])
        self.status_var.set(f"Loaded {len(self.route_names)} routes from OpenCPN.")

    def _preseed_grib_path(self) -> None:
        """If the data/ directory has a GRIB file and no path is set, pre-populate it."""
        if self.grib_var.get():
            return
        data_dir = Path(__file__).parent.parent / "data"
        if data_dir.is_dir():
            candidates = sorted(data_dir.glob("*.grb2")) + sorted(data_dir.glob("*.grb"))
            if candidates:
                self.grib_var.set(str(candidates[0]))

    def browse_grib(self) -> None:
        initial_dir = str(Path(__file__).parent.parent / "data")
        file_path = filedialog.askopenfilename(
            title="Select GRIB File",
            initialdir=initial_dir,
            filetypes=[
                ("GRIB files", "*.grb *.grib *.grb2 *.grib2"),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            self.grib_var.set(file_path)
            self.grib_reader = None
            self.status_var.set("GRIB file selected — click Load GRIB to read coverage.")

    def load_grib(self) -> None:
        path = self.grib_var.get().strip()
        if not path:
            self.show_error("No GRIB File", "Select a GRIB file first.")
            return
        self.status_var.set("Loading GRIB file…")
        self.update_idletasks()
        try:
            self.grib_reader = GribReader(path)
            self.status_var.set(f"GRIB loaded – {self.grib_reader.coverage_summary()}")
            self._redraw_slider()
        except ImportError as exc:
            self.grib_reader = None
            self.show_error(
                "GRIB Library Missing",
                str(exc),
            )
        except Exception as exc:
            self.grib_reader = None
            self.show_error("GRIB Load Error", str(exc))

    def load_selected_route(self) -> None:
        route_name = self.route_var.get().strip()
        if not route_name:
            self.show_error("Route Required", "Select a route first.")
            return

        try:
            self.route_data = route_with_waypoints(route_name)
        except OpenCPNDbError as exc:
            self.show_error("Route Load Error", str(exc))
            return

        waypoint_count = self.route_data["waypoint_count"]
        total_nm = self.route_total_nm(self.route_data["waypoints"])
        self.summary_var.set(
            f"Route: {route_name}    Waypoints: {waypoint_count}    Total Distance: {total_nm:.1f} NM\n"
            f"Apparent wind will require a boat-speed assumption. Current scaffold uses a constant speed in knots."
        )
        self.status_var.set("Route loaded. Build the 3-hour timeline now; GRIB columns are placeholders for the next step.")
        self.populate_route_preview()

    def populate_route_preview(self) -> None:
        self.clear_table()
        if not self.route_data:
            return

        preview_rows = []
        for waypoint in self.route_data["waypoints"]:
            sequence = waypoint.get("sequence")
            label = waypoint.get("name") or f"WP {sequence if sequence is not None else '?'}"
            preview_rows.append(
                (
                    f"WP {sequence}" if sequence is not None else "WP",
                    label,
                    f"{waypoint['lat']:.4f}",
                    f"{waypoint['lon']:.4f}",
                    "--", "--", "--",
                    "--", "--", "--", "--", "--",
                    "--", "--", "--",
                ),
            )

        self._sheet_set_data(preview_rows, redraw=True)

    def generate_plan(self) -> None:
        if not self.route_data:
            self.show_error("No Route", "Load a route before building the passage table.")
            return

        try:
            departure_utc = datetime.strptime(self.departure_var.get().strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            self.show_error("Departure Format", "Use departure format YYYY-MM-DD HH:MM in UTC.")
            return

        try:
            speed_kn = float(self.speed_var.get())
        except ValueError:
            self.show_error("Boat Speed", "Boat speed must be a number in knots.")
            return

        if speed_kn <= 0:
            self.show_error("Boat Speed", "Boat speed must be greater than zero.")
            return

        # Warn if departure is too late for GRIB coverage.
        if self.grib_reader and self.grib_reader.valid_times:
            total_nm = self.route_total_nm(self.route_data["waypoints"])
            passage_hours = total_nm / speed_kn
            eta = departure_utc + timedelta(hours=passage_hours)
            grib_end = self.grib_reader.valid_times[-1]
            if eta > grib_end:
                latest_departure = grib_end - timedelta(hours=passage_hours)
                answer = self._ask_departure_adjustment(
                    eta, grib_end, latest_departure
                )
                if answer == "adjust":
                    departure_utc = latest_departure.replace(
                        minute=0, second=0, microsecond=0
                    )
                    self.departure_var.set(departure_utc.strftime("%Y-%m-%d %H:%M"))
                elif answer == "cancel":
                    return

        rows = self.build_passage_rows(self.route_data["waypoints"], departure_utc, speed_kn)
        self.clear_table()
        self._sheet_set_data(rows, redraw=False)
        self._apply_twa_cell_highlights(rows)
        self._sheet_redraw()

        total_nm = self.route_total_nm(self.route_data["waypoints"])
        total_hours = total_nm / speed_kn if speed_kn > 0 else 0.0
        eta = departure_utc + timedelta(hours=total_hours)
        if self.grib_reader and self.grib_reader.valid_times:
            grib_end = self.grib_reader.valid_times[-1].strftime("%Y-%m-%d %H:%MZ")
            grib_note = f"Wind data from GRIB (coverage ends {grib_end})."
        else:
            grib_note = "No GRIB loaded — wind columns are placeholders."
        self.status_var.set(
            f"Built {len(rows)} timeline rows at {TIMELINE_STEP_HOURS}-hour spacing. "
            f"ETA approx {eta.strftime('%Y-%m-%d %H:%M UTC')}. {grib_note}"
        )

    def _ask_departure_adjustment(
        self,
        eta: datetime,
        grib_end: datetime,
        latest_departure: datetime,
    ) -> str:
        """
        Show a dialog warning that ETA exceeds GRIB coverage.

        Returns 'adjust' if user wants the departure auto-adjusted,
                'proceed' to build with out-of-coverage rows anyway,
                'cancel' to abort.
        """
        result = {"choice": "cancel"}

        win = tk.Toplevel(self)
        win.title("Departure Too Late for GRIB")
        win.configure(bg=self.COLOR_BG)
        win.grab_set()
        win.resizable(False, False)

        msg = (
            f"At this speed the ETA is:\n"
            f"  {eta.strftime('%Y-%m-%d %H:%MZ')}\n\n"
            f"But GRIB coverage ends at:\n"
            f"  {grib_end.strftime('%Y-%m-%d %H:%MZ')}\n\n"
            f"Latest valid departure for full GRIB coverage:\n"
            f"  {latest_departure.strftime('%Y-%m-%d %H:%MZ')}\n\n"
            f"Rows beyond GRIB coverage will still be built\n"
            f"but wind data will be clamped to the last forecast."
        )
        tk.Label(
            win, text=msg, font=self.font_normal, bg=self.COLOR_BG,
            fg=self.COLOR_FG, justify=tk.LEFT, padx=18, pady=12,
        ).pack()

        btn_frame = tk.Frame(win, bg=self.COLOR_BG)
        btn_frame.pack(pady=(0, 12))

        def _choose(choice: str) -> None:
            result["choice"] = choice
            win.destroy()

        tk.Button(
            btn_frame, text="Use latest valid departure",
            command=lambda: _choose("adjust"),
            bg="#27ae60", fg="white", padx=10, pady=4,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_frame, text="Build anyway",
            command=lambda: _choose("proceed"),
            bg="#e67e22", fg="white", padx=10, pady=4,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_frame, text="Cancel",
            command=lambda: _choose("cancel"),
            bg="#7f8c8d", fg="white", padx=10, pady=4,
        ).pack(side=tk.LEFT, padx=6)

        self.wait_window(win)
        return result["choice"]

    def _row_is_upwind_twa(self, row: tuple) -> bool:
        """Return True when TWA is in the -50° to +50° range."""
        if len(row) <= self.twa_column_index:
            return False

        twa_text = str(row[self.twa_column_index]).strip()
        if twa_text in {"--", ""}:
            return False

        try:
            twa_value = float(twa_text.replace("°", ""))
        except ValueError:
            return False

        return -50.0 <= twa_value <= 50.0

    def _apply_twa_cell_highlights(self, rows: list[tuple]) -> None:
        """Color only the TWA cell amber when it is between -50° and +50°."""
        self._sheet_clear_highlights()
        for row_index, row in enumerate(rows):
            if self._row_is_upwind_twa(row):
                self._sheet_highlight_cell(row_index, self.twa_column_index, bg="#ffbf00", fg="#1a1a1a")

    def build_passage_rows(self, waypoints: list[dict], departure_utc: datetime, speed_kn: float) -> list[tuple]:
        if len(waypoints) < 2:
            return []

        segments = []
        cumulative_nm = 0.0
        for index in range(len(waypoints) - 1):
            start = waypoints[index]
            end = waypoints[index + 1]
            distance_nm = haversine_nm(start["lat"], start["lon"], end["lat"], end["lon"])
            bearing_deg = initial_bearing_deg(start["lat"], start["lon"], end["lat"], end["lon"])
            segments.append(
                {
                    "start": start,
                    "end": end,
                    "distance_nm": distance_nm,
                    "bearing_deg": bearing_deg,
                    "start_cumulative_nm": cumulative_nm,
                }
            )
            cumulative_nm += distance_nm

        total_nm = cumulative_nm
        rows = []
        step_index = 0
        elapsed_hours = 0.0

        while True:
            run_nm = min(total_nm, speed_kn * elapsed_hours)
            row = self.row_for_distance(
                segments, run_nm, total_nm,
                departure_utc + timedelta(hours=elapsed_hours),
                speed_kn,
            )
            rows.append(row)
            if run_nm >= total_nm:
                break
            step_index += 1
            elapsed_hours = step_index * TIMELINE_STEP_HOURS

        if rows:
            last_time = rows[-1][0]
            final_eta = departure_utc + timedelta(hours=(total_nm / speed_kn))
            if last_time != final_eta.strftime("%Y-%m-%d %H:%M"):
                final_row = self.row_for_distance(segments, total_nm, total_nm, final_eta, speed_kn)
                rows.append(final_row)

        return rows

    def _wind_columns(self, lat: float, lon: float, time_utc: datetime, course_deg: float, speed_kn: float) -> tuple[str, str, str, str, str]:
        """Return (twd, tws, twa, aws, awa) strings; '--' if GRIB not loaded or outside coverage."""
        if self.grib_reader is None:
            return "--", "--", "--", "--", "--"
        try:
            w = self.grib_reader.wind_at(lat, lon, time_utc, course_deg, speed_kn)
            twa_str = f"{'+' if w.twa_deg >= 0 else ''}{w.twa_deg:.0f}°"
            awa_str = f"{'+' if w.awa_deg >= 0 else ''}{w.awa_deg:.0f}°"
            return (
                f"{w.twd_deg:.0f}°",
                f"{w.tws_kn:.1f}",
                twa_str,
                f"{w.aws_kn:.1f}",
                awa_str,
            )
        except Exception:
            return "--", "--", "--", "--", "--"

    def _wave_columns(self, lat: float, lon: float, time_utc: datetime, course_deg: float) -> tuple[str, str, str]:
        """Return (wv_dir, wv_ang, wv_ht) strings, allowing height-only GRIBs."""
        if self.grib_reader is None:
            return "--", "--", "--"

        wv_dir_text = "--"
        wv_ang_text = "--"
        wv_ht_text = "--"

        try:
            if self.grib_reader.has_wave_height:
                h = self.grib_reader.wave_height_at(lat, lon, time_utc)
                if math.isfinite(h):
                    wv_ht_text = f"{max(0.0, h):.1f}"
        except Exception:
            pass

        try:
            if self.grib_reader.has_wave_direction:
                d = self.grib_reader.wave_direction_at(lat, lon, time_utc)
                if math.isfinite(d):
                    a = (d - course_deg + 180.0) % 360.0 - 180.0
                    if math.isfinite(a):
                        wv_dir_text = f"{d:.0f}°"
                        wv_ang_text = f"{'+' if a >= 0 else ''}{a:.0f}°"
        except Exception:
            pass

        return wv_dir_text, wv_ang_text, wv_ht_text

    def row_for_distance(self, segments: list[dict], run_nm: float, total_nm: float, time_utc: datetime, speed_kn: float = 5.0) -> tuple:
        for segment in segments:
            seg_start = segment["start_cumulative_nm"]
            seg_end = seg_start + segment["distance_nm"]
            if run_nm <= seg_end or math.isclose(run_nm, seg_end):
                if segment["distance_nm"] <= 0:
                    fraction = 0.0
                else:
                    fraction = max(0.0, min(1.0, (run_nm - seg_start) / segment["distance_nm"]))
                lat, lon = interpolate_lat_lon(segment["start"], segment["end"], fraction)
                start_name = segment["start"].get("name") or f"WP {segment['start'].get('sequence', '?')}"
                end_name = segment["end"].get("name") or f"WP {segment['end'].get('sequence', '?')}"
                twd, tws, twa, aws, awa = self._wind_columns(lat, lon, time_utc, segment["bearing_deg"], speed_kn)
                wvdir, wvang, wvht = self._wave_columns(lat, lon, time_utc, segment["bearing_deg"])
                return (
                    time_utc.strftime("%Y-%m-%d %H:%M"),
                    f"{start_name}->{end_name}",
                    f"{lat:.4f}",
                    f"{lon:.4f}",
                    f"{segment['bearing_deg']:.0f}°",
                    f"{run_nm:.1f}",
                    f"{max(0.0, total_nm - run_nm):.1f}",
                    twd, tws, twa, aws, awa,
                    wvdir, wvang, wvht,
                )

        final = segments[-1]
        lat = final["end"]["lat"]
        lon = final["end"]["lon"]
        end_name = final["end"].get("name") or f"WP {final['end'].get('sequence', '?')}"
        twd, tws, twa, aws, awa = self._wind_columns(lat, lon, time_utc, final["bearing_deg"], speed_kn)
        wvdir, wvang, wvht = self._wave_columns(lat, lon, time_utc, final["bearing_deg"])
        return (
            time_utc.strftime("%Y-%m-%d %H:%M"),
            end_name,
            f"{lat:.4f}",
            f"{lon:.4f}",
            f"{final['bearing_deg']:.0f}°",
            f"{total_nm:.1f}",
            "0.0",
            twd, tws, twa, aws, awa,
            wvdir, wvang, wvht,
        )

    def route_total_nm(self, waypoints: list[dict]) -> float:
        total_nm = 0.0
        for index in range(len(waypoints) - 1):
            total_nm += haversine_nm(
                waypoints[index]["lat"],
                waypoints[index]["lon"],
                waypoints[index + 1]["lat"],
                waypoints[index + 1]["lon"],
            )
        return total_nm

    def clear_table(self) -> None:
        self._sheet_set_data([], redraw=True)

    # ------------------------------------------------------------------
    # GRIB timeline slider
    # ------------------------------------------------------------------

    def _slider_passage_hours(self) -> float:
        """Return passage duration in hours from current route + speed, or 0."""
        if not self.route_data:
            return 0.0
        try:
            speed_kn = float(self.speed_var.get())
        except ValueError:
            return 0.0
        if speed_kn <= 0:
            return 0.0
        total_nm = self.route_total_nm(self.route_data["waypoints"])
        return total_nm / speed_kn

    def _slider_departure_fraction(self) -> float:
        """Current departure as a fraction [0,1] within the GRIB window."""
        if not self.grib_reader or not self.grib_reader.valid_times:
            return 0.0
        grib_start = self.grib_reader.valid_times[0]
        grib_end   = self.grib_reader.valid_times[-1]
        grib_span  = (grib_end - grib_start).total_seconds()
        if grib_span <= 0:
            return 0.0
        try:
            dep = datetime.strptime(
                self.departure_var.get().strip(), "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            dep = grib_start
        offset = (dep - grib_start).total_seconds()
        return max(0.0, min(1.0, offset / grib_span))

    def _snap_to_grib_time(self, dt_utc: datetime) -> datetime:
        """Snap a UTC datetime to the nearest GRIB valid_time step."""
        if not self.grib_reader or not self.grib_reader.valid_times:
            return dt_utc
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

        return min(
            self.grib_reader.valid_times,
            key=lambda t: abs((t - dt_utc).total_seconds()),
        )

    def _redraw_slider(self) -> None:
        """Repaint the GRIB timeline canvas."""
        c = self._slider_canvas
        c.delete("all")

        if not self.grib_reader or not self.grib_reader.valid_times:
            c.create_text(
                10, 14, anchor="w", text="Load a GRIB file to enable the departure slider.",
                fill="#4a6fa5", font=("Arial", 9),
            )
            self._slider_label_left.config(text="")
            self._slider_label_right.config(text="")
            return

        grib_start  = self.grib_reader.valid_times[0]
        grib_end    = self.grib_reader.valid_times[-1]
        grib_span_h = (grib_end - grib_start).total_seconds() / 3600.0

        self._slider_label_left.config(text=grib_start.strftime("%Y-%m-%d %H:%MZ"))
        self._slider_label_right.config(text=grib_end.strftime("%Y-%m-%d %H:%MZ"))

        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10:
            return

        pad = 4
        track_w = w - 2 * pad

        # Background track
        c.create_rectangle(pad, 6, pad + track_w, h - 6, fill="#2c3e50", outline="#4a6fa5")

        passage_hours = self._slider_passage_hours()
        if grib_span_h > 0 and passage_hours > 0:
            thumb_frac  = min(1.0, passage_hours / grib_span_h)
            thumb_w     = max(8, int(track_w * thumb_frac))
            dep_frac    = self._slider_departure_fraction()
            max_dep_frac = max(0.0, 1.0 - thumb_frac)
            dep_frac    = min(dep_frac, max_dep_frac)
            thumb_x     = pad + int(dep_frac * track_w)

            # Shade the out-of-passage region in red if ETA > GRIB end
            eta_frac = dep_frac + thumb_frac
            if eta_frac > 1.0:
                overflow_x = pad + track_w
                c.create_rectangle(
                    pad + int(track_w), 6, pad + track_w, h - 6,
                    fill="#7f1c1c", outline="",
                )

            # Thumb
            c.create_rectangle(
                thumb_x, 4, thumb_x + thumb_w, h - 4,
                fill="#2980b9", outline="#5dade2", width=1,
            )
            # Departure label inside thumb
            dep_str = self.departure_var.get().strip()[-5:]  # HH:MM
            c.create_text(
                thumb_x + thumb_w // 2, h // 2,
                text=dep_str, fill="white", font=("Arial", 9, "bold"),
                anchor="center",
            )
        else:
            c.create_text(
                w // 2, h // 2,
                text="Load a route and set boat speed to size the thumb.",
                fill="#4a6fa5", font=("Arial", 9), anchor="center",
            )

    def _slider_press(self, event: tk.Event) -> None:
        self._slider_dragging = True
        self._slider_drag_start_x = event.x
        self._slider_drag_start_frac = self._slider_departure_fraction()

    def _slider_drag(self, event: tk.Event) -> None:
        if not self._slider_dragging or not self.grib_reader:
            return
        c = self._slider_canvas
        w = c.winfo_width()
        pad = 4
        track_w = w - 2 * pad
        if track_w <= 0:
            return

        delta_px   = event.x - self._slider_drag_start_x
        delta_frac = delta_px / track_w

        grib_start  = self.grib_reader.valid_times[0]
        grib_end    = self.grib_reader.valid_times[-1]
        grib_span_h = (grib_end - grib_start).total_seconds() / 3600.0

        passage_hours = self._slider_passage_hours()
        thumb_frac = passage_hours / grib_span_h if grib_span_h > 0 else 0.0
        max_frac   = max(0.0, 1.0 - thumb_frac)

        new_frac = max(0.0, min(max_frac, self._slider_drag_start_frac + delta_frac))
        new_dep  = grib_start + timedelta(seconds=new_frac * grib_span_h * 3600)
        # Snap to nearest GRIB timestamp so generated rows align with available forecast steps.
        new_dep  = self._snap_to_grib_time(new_dep)
        self.departure_var.set(new_dep.strftime("%Y-%m-%d %H:%M"))
        self._redraw_slider()

    def _slider_release(self, _event: tk.Event) -> None:
        self._slider_dragging = False
        # Auto-rebuild if a route is loaded
        if self.route_data:
            self.generate_plan()

    # ------------------------------------------------------------------
    # tksheet compatibility wrappers
    # ------------------------------------------------------------------

    def _sheet_set_column_widths(self, widths: list[int]) -> None:
        """Set column widths with compatibility for different tksheet versions."""
        try:
            self.plan_sheet.set_column_widths(widths, redraw=False)
            return
        except TypeError:
            pass

        try:
            self.plan_sheet.set_column_widths(widths)
        except TypeError:
            for idx, width in enumerate(widths):
                try:
                    self.plan_sheet.column_width(column=idx, width=width, redraw=False)
                except TypeError:
                    self.plan_sheet.column_width(column=idx, width=width)

    def _sheet_set_data(self, rows: list[tuple], redraw: bool) -> None:
        """Set sheet data with compatibility for different tksheet versions."""
        try:
            self.plan_sheet.set_sheet_data(rows, reset_col_positions=False, redraw=redraw)
            return
        except TypeError:
            pass

        try:
            self.plan_sheet.set_sheet_data(rows, reset_col_positions=False)
        except TypeError:
            self.plan_sheet.set_sheet_data(rows)

        if redraw:
            self._sheet_redraw()

    def _sheet_clear_highlights(self) -> None:
        """Clear any prior cell highlights across tksheet versions."""
        if hasattr(self.plan_sheet, "dehighlight_all"):
            self.plan_sheet.dehighlight_all()
            return

        if hasattr(self.plan_sheet, "dehighlight_cells"):
            try:
                self.plan_sheet.dehighlight_cells(all_=True)
            except TypeError:
                self.plan_sheet.dehighlight_cells()

    def _sheet_highlight_cell(self, row: int, column: int, bg: str, fg: str) -> None:
        """Highlight a single cell with compatibility for different tksheet versions."""
        if hasattr(self.plan_sheet, "highlight_cells"):
            try:
                self.plan_sheet.highlight_cells(
                    row=row,
                    column=column,
                    bg=bg,
                    fg=fg,
                    redraw=False,
                )
                return
            except TypeError:
                try:
                    self.plan_sheet.highlight_cells(
                        row=row,
                        column=column,
                        bg=bg,
                        fg=fg,
                    )
                    return
                except TypeError:
                    pass

        if hasattr(self.plan_sheet, "highlight_cells_at"):
            self.plan_sheet.highlight_cells_at(row=row, column=column, bg=bg, fg=fg)

    def _sheet_redraw(self) -> None:
        """Request a redraw across tksheet versions."""
        if hasattr(self.plan_sheet, "redraw"):
            self.plan_sheet.redraw()
        elif hasattr(self.plan_sheet, "refresh"):
            self.plan_sheet.refresh()


if __name__ == "__main__":
    app = PassagePlanningTool()
    app.mainloop()