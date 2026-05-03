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
import re
import sys
import tkinter as tk
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tksheet import Sheet

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.grib_reader import GribReader
from shared.opencpn_db import OpenCPNDbError, create_planner_route, list_routes, route_with_waypoints
from shared.vnc_window import VNCToolWindow


EARTH_RADIUS_NM = 3440.065
TIMELINE_STEP_HOURS = 3

# Field labels for the transposed (Windy-style) horizontal layout.
# Index order must match the tuple returned by row_for_distance.
ROW_LABELS = [
    "UTC", "Leg", "Run NM", "Remain NM", "Course T",
    "TWD°", "TWA°", "AWA°", "TWS kt", "AWS kt",
    "WvDir°", "WvAng°", "WvHt m", "WvPer s",
]


def compass_arrow16(bearing_deg: float) -> str:
    """Map an absolute compass bearing (0=N, 90=E) to a 16-point arrow pointing in that direction."""
    icons = [
        "↑", "↑·", "↗", "↗·",
        "→", "→·", "↘", "↘·",
        "↓", "↓·", "↙", "↙·",
        "←", "←·", "↖", "↖·",
    ]
    a = bearing_deg % 360.0
    index = int((a + 11.25) // 22.5) % 16
    return icons[index]


def _ordinal(n: int) -> str:
    """Return n with English ordinal suffix, e.g. 1st, 2nd, 3rd, 5th."""
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _friendly_departure(dt: "datetime") -> str:
    """Format a datetime as 'Leave Monday 5th May at 12:00'."""
    return f"Leave {dt.strftime('%A')} {_ordinal(dt.day)} {dt.strftime('%B at %H:%M')}"


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


def relative_angle_arrow16(angle_deg: float) -> str:
    """Map signed relative angle (-180..+180) to custom 16-point arrow-only icons.

    Arrow shows wind as felt by crew facing forward (direction wind is travelling).
    TWA=  0° → ↓  (dead ahead, pushes you back)
    TWA=-45° → ↘  (port bow, pushes toward lower-right)
    TWA=-90° → →  (port beam, pushes to starboard)
    TWA=±180° → ↑ (astern, pushes you forward)
    TWA=+90° → ←  (starboard beam, pushes to port)
    TWA=+45° → ↙  (starboard bow, pushes toward lower-left)
    """
    icons = [
        "↑", "↑·", "↗", "↗·",
        "→", "→·", "↘", "↘·",
        "↓", "↓·", "↙", "↙·",
        "←", "←·", "↖", "↖·",
    ]
    # Arrow shows wind direction as felt by crew facing forward (where wind is going).
    # +180 flips from "wind source" to "wind travel direction":
    # -45° (from port bow)   → ↘  (wind pushes toward lower-right)
    #   0° (from dead ahead) → ↓  (wind pushes straight back)
    # +90° (from starboard)  → ←  (wind pushes to port)
    # ±180° (from astern)    → ↑  (wind pushes forward)
    a = (angle_deg + 180.0 + 360.0) % 360.0
    index = int((a + 11.25) // 22.5) % 16
    return icons[index]


def _wind_speed_colors(knots_str: str) -> tuple[str, str]:
    """Return (bg, fg) for a wind-speed cell using Beaufort-inspired bands."""
    try:
        kt = float(re.search(r"\d+(?:\.\d+)?", knots_str).group(0))  # type: ignore[union-attr]
    except (AttributeError, ValueError, TypeError):
        return "#0d2b20", "#90e8c0"
    if kt < 4:
        return "#0d3326", "#7dd8b0"   # calm — dark teal
    if kt < 8:
        return "#0e5c38", "#6eeaaa"   # light — teal-green
    if kt < 12:
        return "#0a5c7a", "#60d8f8"   # moderate — teal-blue
    if kt < 17:
        return "#155488", "#80c8ff"   # fresh — blue
    if kt < 22:
        return "#7d6008", "#ffe060"   # strong — amber
    if kt < 28:
        return "#7d3208", "#ffb060"   # near gale — orange
    return "#7b241c", "#ff9090"       # gale+ — red


def _wave_height_colors(ht_str: str) -> tuple[str, str]:
    """Return (bg, fg) for a wave-height cell using sea-state bands."""
    try:
        h = float(re.search(r"\d+(?:\.\d+)?", ht_str).group(0))  # type: ignore[union-attr]
    except (AttributeError, ValueError, TypeError):
        return "#12103a", "#b0a0e8"
    if h < 0.5:
        return "#0e1a3a", "#8ab0e8"   # ripple — deep navy
    if h < 1.0:
        return "#1a2a5e", "#90b8f0"   # slight — navy-blue
    if h < 2.0:
        return "#2e1a6e", "#c0a0f0"   # moderate — violet-blue
    if h < 3.0:
        return "#4a1a6e", "#d880f8"   # rough — purple
    return "#6b1a7e", "#f060ff"       # very rough — deep purple


class PassagePlanningTool(VNCToolWindow):
    """Route-based passage planning scaffold for later GRIB integration."""

    def __init__(self):
        super().__init__(title="Passage Planning", width=1140, height=760)
        self.route_data: dict | None = None
        self.route_names: list[str] = []
        self.grib_reader: GribReader | None = None
        self._last_grib_path_file = Path(__file__).parent.parent / ".last_grib_path"
        self.twa_row_index = 6          # TWA° is field row 6 in transposed layout
        self._latest_plan_points: list[dict] = []
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
        tk.Label(plan_row, text="TWA alert ±°", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG).pack(side=tk.LEFT)
        self.twa_alert_var = tk.StringVar(value="60")
        tk.Entry(plan_row, textvariable=self.twa_alert_var, width=5, font=self.font_small).pack(side=tk.LEFT, padx=(6, 12))
        tk.Label(plan_row, text="AWA alert ±°", font=self.font_normal, bg=self.COLOR_BG, fg=self.COLOR_FG).pack(side=tk.LEFT)
        self.awa_alert_var = tk.StringVar(value="50")
        tk.Entry(plan_row, textvariable=self.awa_alert_var, width=5, font=self.font_small).pack(side=tk.LEFT, padx=(6, 14))
        tk.Button(plan_row, text="Build 3h Table", command=self.generate_plan, bg="#27ae60", fg="white", padx=14).pack(side=tk.LEFT)
        tk.Button(
            plan_row,
            text="Create OpenCPN Planner Route",
            command=self.create_planner_route_in_opencpn,
            bg="#1f618d",
            fg="white",
            padx=12,
        ).pack(side=tk.LEFT)

        self.summary_var = tk.StringVar(value="Load an OpenCPN route to begin.")
        tk.Label(self.content_frame, textvariable=self.summary_var, font=self.font_small, bg=self.COLOR_BG, fg="#a8d8ff", anchor="w", justify=tk.LEFT).pack(fill=tk.X, pady=(0, 4))

        table_frame = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        table_frame.pack(fill=tk.BOTH, expand=True)

        self.plan_sheet = Sheet(
            table_frame,
            headers=[],
            data=[],
            show_row_index=True,
            row_index_align="w",
            show_x_scrollbar=True,
            show_y_scrollbar=True,
            align="center",
            header_align="center",
            theme="dark blue",
        )
        self.plan_sheet.set_index_width(110)
        self.plan_sheet.enable_bindings()
        self.plan_sheet.pack(fill=tk.BOTH, expand=True)
        # Column widths are set dynamically after data is loaded.

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

        self._build_legend()

    def _build_legend(self) -> None:
        """Compact single-line color legend: wind speed (Beaufort) + wave height."""
        legend_frame = tk.Frame(self.content_frame, bg=self.COLOR_BG)
        legend_frame.pack(fill=tk.X, pady=(3, 0))

        row = tk.Frame(legend_frame, bg=self.COLOR_BG)
        row.pack(fill=tk.X)
        tk.Label(row, text="Wind kt: ", font=("Arial", 8), bg=self.COLOR_BG, fg="#7aaec8").pack(side=tk.LEFT)
        for label, bg, fg in [
            ("<4",    "#0d3326", "#7dd8b0"),
            ("4-7",   "#0e5c38", "#6eeaaa"),
            ("8-11",  "#0a5c7a", "#60d8f8"),
            ("12-16", "#155488", "#80c8ff"),
            ("17-21", "#7d6008", "#ffe060"),
            ("22-27", "#7d3208", "#ffb060"),
            ("28+",   "#7b241c", "#ff9090"),
        ]:
            tk.Label(row, text=f" {label} ", font=("Arial", 8), bg=bg, fg=fg, padx=1).pack(side=tk.LEFT, padx=1)
        tk.Label(row, text="  Wave m: ", font=("Arial", 8), bg=self.COLOR_BG, fg="#b090e8").pack(side=tk.LEFT)
        for label, bg, fg in [
            ("<0.5",  "#0e1a3a", "#8ab0e8"),
            ("0.5",   "#1a2a5e", "#90b8f0"),
            ("1",     "#2e1a6e", "#c0a0f0"),
            ("2",     "#4a1a6e", "#d880f8"),
            ("3+",    "#6b1a7e", "#f060ff"),
        ]:
            tk.Label(row, text=f" {label} ", font=("Arial", 8), bg=bg, fg=fg, padx=1).pack(side=tk.LEFT, padx=1)

    def refresh_routes(self) -> None:
        try:
            routes = list_routes()
        except OpenCPNDbError as exc:
            self.show_error("OpenCPN Route Error", str(exc))
            self.summary_var.set("Could not read OpenCPN route list.")
            return

        self.route_names = [route.name for route in routes if route.name]
        self.route_combo["values"] = self.route_names
        if self.route_names and not self.route_var.get():
            self.route_var.set(self.route_names[0])
        self.summary_var.set(f"Loaded {len(self.route_names)} routes from OpenCPN.")

    def _preseed_grib_path(self) -> None:
        """Pre-populate GRIB path from last-used file, else first local data file."""
        if self.grib_var.get():
            return

        last_path = self._load_last_grib_path()
        if last_path is not None:
            self.grib_var.set(str(last_path))
            return

        data_dir = Path(__file__).parent.parent / "data"
        if data_dir.is_dir():
            candidates = sorted(data_dir.glob("*.grb2")) + sorted(data_dir.glob("*.grb"))
            if candidates:
                self.grib_var.set(str(candidates[0]))

    def browse_grib(self) -> None:
        initial_dir = Path(__file__).parent.parent / "data"
        current = Path(self.grib_var.get().strip()) if self.grib_var.get().strip() else None
        if current and current.exists():
            initial_dir = current.parent
        else:
            last_path = self._load_last_grib_path()
            if last_path is not None:
                initial_dir = last_path.parent

        file_path = filedialog.askopenfilename(
            title="Select GRIB File",
            initialdir=str(initial_dir),
            filetypes=[
                ("GRIB files", "*.grb *.grib *.grb2 *.grib2"),
                ("All files", "*.*"),
            ],
        )
        if file_path:
            self.grib_var.set(file_path)
            self._save_last_grib_path(file_path)
            self.grib_reader = None
            self.summary_var.set("GRIB file selected — click Load GRIB to read coverage.")

    def load_grib(self) -> None:
        path = self.grib_var.get().strip()
        if not path:
            self.show_error("No GRIB File", "Select a GRIB file first.")
            return
        self.summary_var.set("Loading GRIB file…")
        self.update_idletasks()
        try:
            self.grib_reader = GribReader(path)
            self._save_last_grib_path(path)
            self.summary_var.set(f"GRIB loaded – {self.grib_reader.coverage_summary()}")
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
        self.summary_var.set("Route loaded. Build the 3-hour timeline now; GRIB columns are placeholders for the next step.")
        self.populate_route_preview()

    def populate_route_preview(self) -> None:
        self.clear_table()
        if not self.route_data:
            return

        stub_rows = []
        for waypoint in self.route_data["waypoints"]:
            sequence = waypoint.get("sequence")
            name = waypoint.get("name") or f"WP {sequence if sequence is not None else '?'}"
            stub_rows.append((
                f"WP {sequence}" if sequence is not None else "WP",
                name,
                "--", "--", "--",
                "--", "--", "--", "--", "--",
                "--", "--", "--", "--",
            ))

        time_headers, display_rows = self._transpose_for_display(stub_rows)
        self._update_sheet_headers(time_headers)
        self._sheet_set_data(display_rows, redraw=True)
        self._update_row_index(len(display_rows))
        self._sheet_set_column_widths([100] * len(stub_rows))

    def _get_plan_inputs(self) -> tuple[datetime, float] | None:
        """Validate departure/speed and apply GRIB coverage adjustment flow."""
        if not self.route_data:
            self.show_error("No Route", "Load a route before building the passage table.")
            return None

        try:
            departure_utc = datetime.strptime(self.departure_var.get().strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            self.show_error("Departure Format", "Use departure format YYYY-MM-DD HH:MM in UTC.")
            return None

        try:
            speed_kn = float(self.speed_var.get())
        except ValueError:
            self.show_error("Boat Speed", "Boat speed must be a number in knots.")
            return None

        if speed_kn <= 0:
            self.show_error("Boat Speed", "Boat speed must be greater than zero.")
            return None

        if self.grib_reader and self.grib_reader.valid_times:
            total_nm = self.route_total_nm(self.route_data["waypoints"])
            passage_hours = total_nm / speed_kn
            eta = departure_utc + timedelta(hours=passage_hours)
            grib_end = self.grib_reader.valid_times[-1]
            if eta > grib_end:
                latest_departure = grib_end - timedelta(hours=passage_hours)
                answer = self._ask_departure_adjustment(eta, grib_end, latest_departure)
                if answer == "adjust":
                    departure_utc = latest_departure.replace(minute=0, second=0, microsecond=0)
                    self.departure_var.set(departure_utc.strftime("%Y-%m-%d %H:%M"))
                elif answer == "cancel":
                    return None

        return departure_utc, speed_kn

    def generate_plan(self) -> None:
        plan_inputs = self._get_plan_inputs()
        if plan_inputs is None or not self.route_data:
            return
        departure_utc, speed_kn = plan_inputs

        points = self._build_timeline_points(self.route_data["waypoints"], departure_utc, speed_kn)
        self._latest_plan_points = points
        rows = [self._display_row_from_point(point, points[-1]["run_nm"], speed_kn) for point in points]
        self.clear_table()
        time_headers, display_rows = self._transpose_for_display(rows)
        self._update_sheet_headers(time_headers)
        self._sheet_set_data(display_rows, redraw=False)
        self._update_row_index(len(display_rows))
        self._apply_table_highlights(display_rows)
        self._sheet_set_column_widths([68] * len(rows))
        self._sheet_redraw()

        total_nm = self.route_total_nm(self.route_data["waypoints"])
        total_hours = total_nm / speed_kn if speed_kn > 0 else 0.0
        eta = departure_utc + timedelta(hours=total_hours)
        if self.grib_reader and self.grib_reader.valid_times:
            grib_end = self.grib_reader.valid_times[-1].strftime("%Y-%m-%d %H:%MZ")
            grib_note = f"Wind data from GRIB (coverage ends {grib_end})."
        else:
            grib_note = "No GRIB loaded — wind columns are placeholders."
        self.summary_var.set(
            f"Built {len(rows)} timeline rows at {TIMELINE_STEP_HOURS}-hour spacing. "
            f"ETA approx {eta.strftime('%Y-%m-%d %H:%M UTC')}. {grib_note}"
        )

    def create_planner_route_in_opencpn(self) -> None:
        """Create or replace '<route>_planner' in OpenCPN using timeline points."""
        if not self.route_data:
            self.show_error("No Route", "Load a route before creating a planner route.")
            return

        route_name = self.route_data.get("route_name", self.route_var.get().strip())
        if not route_name:
            self.show_error("Route Required", "No source route is selected.")
            return

        plan_inputs = self._get_plan_inputs()
        if plan_inputs is None:
            return
        departure_utc, speed_kn = plan_inputs

        points = self._build_timeline_points(self.route_data["waypoints"], departure_utc, speed_kn)
        if not points:
            self.show_error("Planner Route", "Could not build timeline points for planner route.")
            return

        planner_route_name = f"{route_name}_planner"
        if not messagebox.askyesno(
            "Create Planner Route",
            (
                f"Create or replace OpenCPN route '{planner_route_name}' with "
                f"{len(points)} waypoints from the current timeline?"
            ),
            parent=self,
        ):
            return

        upload_points = [
            {
                "name": self._planner_waypoint_name(point["time_utc"]),
                "lat": point["lat"],
                "lon": point["lon"],
            }
            for point in points
        ]

        try:
            result = create_planner_route(route_name, upload_points)
        except OpenCPNDbError as exc:
            self.show_error("Planner Route Error", str(exc))
            return

        self.summary_var.set(
            f"OpenCPN route '{result['route_name']}' updated with {result['waypoint_count']} planner waypoints. "
            f"Backup: {result.get('backup_path', 'n/a')}"
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

    def _angle_alert_threshold(self, var: tk.StringVar, default_value: float) -> float:
        """Return a validated positive alert threshold in degrees from a Tk variable."""
        try:
            threshold = float(var.get().strip())
        except (ValueError, AttributeError):
            return default_value
        return threshold if threshold > 0 else default_value

    def _is_alert_angle_value(self, angle_text: str, threshold_deg: float) -> bool:
        """Return True when a signed angle string is inside ±threshold degrees."""
        if not angle_text or angle_text == "--":
            return False
        match = re.search(r"[-+]?\d+(?:\.\d+)?", angle_text)
        if not match:
            return False
        angle_val = float(match.group(0))
        return -threshold_deg <= angle_val <= threshold_deg

    def _apply_table_highlights(self, display_rows: list[list]) -> None:
        """Windy-inspired bands in transposed layout: each row is a field, cols are timesteps."""
        self._sheet_clear_highlights()
        if not display_rows:
            return
        n_cols = len(display_rows[0])
        twa_alert_deg = self._angle_alert_threshold(self.twa_alert_var, 60.0)
        awa_alert_deg = self._angle_alert_threshold(self.awa_alert_var, 50.0)

        # Row index labels: frozen panel, consistent dark style
        for row_idx in range(len(display_rows)):
            self._sheet_highlight_index_cell(row_idx, bg="#0d1b2a", fg="#7aaec8")

        # Route group rows 0-4: dark steel-blue
        for row_idx in range(min(5, len(display_rows))):
            for col_idx in range(n_cols):
                self._sheet_highlight_cell(row_idx, col_idx, bg="#162436", fg="#c0d4e8")

        # TWD row (5): teal
        if len(display_rows) > 5:
            for col_idx in range(n_cols):
                self._sheet_highlight_cell(5, col_idx, bg="#0d2b20", fg="#90e8c0")

        # TWA row (6): teal-green base, amber override for upwind cells
        if len(display_rows) > 6:
            for col_idx in range(n_cols):
                val = display_rows[6][col_idx] if col_idx < len(display_rows[6]) else "--"
                if self._is_alert_angle_value(val, twa_alert_deg):
                    self._sheet_highlight_cell(6, col_idx, bg="#c48000", fg="#0a0a0a")
                else:
                    self._sheet_highlight_cell(6, col_idx, bg="#0a1a10", fg="#30a060")

        # AWA row (7): cyan-blue base, amber override for narrow apparent angles
        if len(display_rows) > 7:
            for col_idx in range(n_cols):
                val = display_rows[7][col_idx] if col_idx < len(display_rows[7]) else "--"
                if self._is_alert_angle_value(val, awa_alert_deg):
                    self._sheet_highlight_cell(7, col_idx, bg="#c48000", fg="#0a0a0a")
                else:
                    self._sheet_highlight_cell(7, col_idx, bg="#06131e", fg="#2a8090")

        # TWS row (8): value-driven Beaufort ramp
        if len(display_rows) > 8:
            for col_idx in range(n_cols):
                val = display_rows[8][col_idx] if col_idx < len(display_rows[8]) else "--"
                bg, fg = _wind_speed_colors(val)
                self._sheet_highlight_cell(8, col_idx, bg=bg, fg=fg)

        # AWS row (9): value-driven Beaufort ramp
        if len(display_rows) > 9:
            for col_idx in range(n_cols):
                val = display_rows[9][col_idx] if col_idx < len(display_rows[9]) else "--"
                bg, fg = _wind_speed_colors(val)
                self._sheet_highlight_cell(9, col_idx, bg=bg, fg=fg)

        # WvDir, WvAng rows (10, 11): violet
        for row_idx in (10, 11):
            if len(display_rows) > row_idx:
                for col_idx in range(n_cols):
                    self._sheet_highlight_cell(row_idx, col_idx, bg="#160d38", fg="#b090e8")

        # WvHt row (12): value-driven wave ramp
        if len(display_rows) > 12:
            for col_idx in range(n_cols):
                val = display_rows[12][col_idx] if col_idx < len(display_rows[12]) else "--"
                bg, fg = _wave_height_colors(val)
                self._sheet_highlight_cell(12, col_idx, bg=bg, fg=fg)

        # WvPer row (13): violet
        if len(display_rows) > 13:
            for col_idx in range(n_cols):
                self._sheet_highlight_cell(13, col_idx, bg="#160d38", fg="#b090e8")

    def _transpose_for_display(self, rows: list[tuple]) -> tuple[list[str], list[list]]:
        """Transpose N×14 passage rows into 14×N display rows (labels go in the row index).

        Returns:
            time_headers  — HH:MM strings used as sheet column headers
            display_rows  — display_rows[i] = [val_t0, val_t1, ...]  (no label in col 0)
        """
        if not rows:
            return [], [[] for _ in ROW_LABELS]
        time_headers = [str(row[0])[-5:] for row in rows]
        display_rows = []
        for field_idx in range(len(ROW_LABELS)):
            values = [
                str(rows[step_idx][field_idx]) if field_idx < len(rows[step_idx]) else "--"
                for step_idx in range(len(rows))
            ]
            display_rows.append(values)
        return time_headers, display_rows

    def _update_sheet_headers(self, time_headers: list[str]) -> None:
        """Set sheet column headers: one per timestep (labels are in the row index)."""
        headers = time_headers
        try:
            self.plan_sheet.headers(headers)
        except Exception:
            try:
                self.plan_sheet.set_headers(headers)
            except Exception:
                pass

    def _update_row_index(self, n_rows: int) -> None:
        """Populate the frozen row index panel with field labels."""
        labels = ROW_LABELS[:n_rows]
        try:
            self.plan_sheet.row_index(labels)
        except Exception:
            try:
                self.plan_sheet.set_index_data(labels)
            except Exception:
                pass

    def _planner_waypoint_name(self, dt_utc: datetime) -> str:
        """Format planner waypoint names like 'Monday 3rd 12:00'."""
        return f"{dt_utc.strftime('%A')} {_ordinal(dt_utc.day)} {dt_utc.strftime('%H:%M')}"

    def _build_route_segments(self, waypoints: list[dict]) -> tuple[list[dict], float]:
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
        return segments, cumulative_nm

    def _point_for_distance(
        self,
        segments: list[dict],
        run_nm: float,
        total_nm: float,
        time_utc: datetime,
    ) -> dict:
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
                return {
                    "time_utc": time_utc,
                    "lat": lat,
                    "lon": lon,
                    "run_nm": run_nm,
                    "remain_nm": max(0.0, total_nm - run_nm),
                    "bearing_deg": segment["bearing_deg"],
                    "leg": f"{start_name}->{end_name}",
                }

        final = segments[-1]
        end_name = final["end"].get("name") or f"WP {final['end'].get('sequence', '?')}"
        return {
            "time_utc": time_utc,
            "lat": final["end"]["lat"],
            "lon": final["end"]["lon"],
            "run_nm": total_nm,
            "remain_nm": 0.0,
            "bearing_deg": final["bearing_deg"],
            "leg": end_name,
        }

    def _build_timeline_points(self, waypoints: list[dict], departure_utc: datetime, speed_kn: float) -> list[dict]:
        if len(waypoints) < 2:
            return []

        segments, total_nm = self._build_route_segments(waypoints)
        points: list[dict] = []
        step_index = 0
        elapsed_hours = 0.0

        while True:
            run_nm = min(total_nm, speed_kn * elapsed_hours)
            point = self._point_for_distance(
                segments,
                run_nm,
                total_nm,
                departure_utc + timedelta(hours=elapsed_hours),
            )
            points.append(point)
            if run_nm >= total_nm:
                break
            step_index += 1
            elapsed_hours = step_index * TIMELINE_STEP_HOURS

        if points:
            final_eta = departure_utc + timedelta(hours=(total_nm / speed_kn))
            if points[-1]["time_utc"].strftime("%Y-%m-%d %H:%M") != final_eta.strftime("%Y-%m-%d %H:%M"):
                points.append(self._point_for_distance(segments, total_nm, total_nm, final_eta))

        return points

    def _display_row_from_point(self, point: dict, total_nm: float, speed_kn: float) -> tuple:
        twd, tws, twa, aws, awa = self._wind_columns(
            point["lat"], point["lon"], point["time_utc"], point["bearing_deg"], speed_kn
        )
        wvdir, wvang, wvht, wvper = self._wave_columns(
            point["lat"], point["lon"], point["time_utc"], point["bearing_deg"]
        )
        return (
            point["time_utc"].strftime("%Y-%m-%d %H:%M"),
            point["leg"],
            f"{point['run_nm']:.1f}",
            f"{max(0.0, total_nm - point['run_nm']):.1f}",
            f"{point['bearing_deg']:.0f}° {compass_arrow16(point['bearing_deg'])}",
            twd,
            twa,
            awa,
            tws,
            aws,
            wvdir,
            wvang,
            wvht,
            wvper,
        )

    def build_passage_rows(self, waypoints: list[dict], departure_utc: datetime, speed_kn: float) -> list[tuple]:
        if len(waypoints) < 2:
            return []

        points = self._build_timeline_points(waypoints, departure_utc, speed_kn)
        if not points:
            return []
        total_nm = points[-1]["run_nm"]
        return [self._display_row_from_point(point, total_nm, speed_kn) for point in points]

    def _wind_columns(self, lat: float, lon: float, time_utc: datetime, course_deg: float, speed_kn: float) -> tuple[str, str, str, str, str]:
        """Return (twd, tws, twa, aws, awa) strings; '--' if GRIB not loaded or outside coverage."""
        if self.grib_reader is None:
            return "--", "--", "--", "--", "--"
        try:
            w = self.grib_reader.wind_at(lat, lon, time_utc, course_deg, speed_kn)
            twd_dir = relative_angle_arrow16(w.twd_deg)   # absolute bearing, +180 gives travel dir
            twa_dir = relative_angle_arrow16(w.twa_deg)
            awa_dir = relative_angle_arrow16(w.awa_deg)
            twa_str = f"{'+' if w.twa_deg >= 0 else ''}{w.twa_deg:.0f}° {twa_dir}"
            awa_str = f"{'+' if w.awa_deg >= 0 else ''}{w.awa_deg:.0f}° {awa_dir}"
            return (
                f"{w.twd_deg:.0f}° {twd_dir}",
                f"{w.tws_kn:.1f}",
                twa_str,
                f"{w.aws_kn:.1f}",
                awa_str,
            )
        except Exception:
            return "--", "--", "--", "--", "--"

    def _load_last_grib_path(self) -> Path | None:
        """Return saved GRIB path if it exists and still points to a file."""
        try:
            saved = self._last_grib_path_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None

        if not saved:
            return None

        path = Path(saved)
        if path.is_file():
            return path
        return None

    def _save_last_grib_path(self, path: str) -> None:
        """Persist most recently selected/loaded GRIB path for next app launch."""
        try:
            self._last_grib_path_file.write_text(path.strip(), encoding="utf-8")
        except OSError:
            # Path persistence is non-critical; ignore write failures.
            pass

    def set_fullscreen(self) -> None:
        """Maximize/fullscreen for launcher use across different Tk backends."""
        try:
            self.attributes("-fullscreen", True)
            return
        except tk.TclError:
            pass

        try:
            self.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

    def _wave_columns(self, lat: float, lon: float, time_utc: datetime, course_deg: float) -> tuple[str, str, str, str]:
        """Return (wv_dir, wv_ang, wv_ht, wv_per) strings, allowing partial wave GRIBs."""
        if self.grib_reader is None:
            return "--", "--", "--", "--"

        wv_dir_text = "--"
        wv_ang_text = "--"
        wv_ht_text = "--"
        wv_per_text = "--"

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

        try:
            if self.grib_reader.has_wave_period:
                p = self.grib_reader.wave_period_at(lat, lon, time_utc)
                if math.isfinite(p):
                    wv_per_text = f"{p:.0f}"
        except Exception:
            pass

        return wv_dir_text, wv_ang_text, wv_ht_text, wv_per_text

    def row_for_distance(self, segments: list[dict], run_nm: float, total_nm: float, time_utc: datetime, speed_kn: float = 5.0) -> tuple:
        point = self._point_for_distance(segments, run_nm, total_nm, time_utc)
        return self._display_row_from_point(point, total_nm, speed_kn)

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

        self._slider_label_left.config(text=_friendly_departure(grib_start))
        self._slider_label_right.config(text=f"GRIB ends {grib_end.strftime('%a %d %b %H:%M')}")

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
            try:
                dep_dt = datetime.strptime(self.departure_var.get().strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                dep_str = _friendly_departure(dep_dt)
            except ValueError:
                pass
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

    def _sheet_highlight_index_cell(self, row: int, bg: str, fg: str) -> None:
        """Highlight a cell in the frozen row index panel."""
        if hasattr(self.plan_sheet, "highlight_cells"):
            try:
                self.plan_sheet.highlight_cells(
                    row=row,
                    canvas="row_index",
                    bg=bg,
                    fg=fg,
                    redraw=False,
                )
                return
            except TypeError:
                try:
                    self.plan_sheet.highlight_cells(
                        row=row,
                        canvas="row_index",
                        bg=bg,
                        fg=fg,
                    )
                    return
                except TypeError:
                    pass

    def _sheet_redraw(self) -> None:
        """Request a redraw across tksheet versions."""
        if hasattr(self.plan_sheet, "redraw"):
            self.plan_sheet.redraw()
        elif hasattr(self.plan_sheet, "refresh"):
            self.plan_sheet.refresh()


if __name__ == "__main__":
    full_screen = "--fullscreen" in sys.argv
    app = PassagePlanningTool()
    if full_screen:
        app.set_fullscreen()
    app.mainloop()
