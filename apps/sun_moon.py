#!/usr/bin/env python3
"""
Sun & Moon Celestial Navigation Tool

Calculates sun/moon rise/set/altitude/azimuth/declination from Signal K position.
Generates a detailed nautical almanac-style text report and optional PDF with
star field charts for dawn and dusk nautical twilight times.

Usage:
    DISPLAY=:0 python3 sun_moon.py    (in VNC, launched from OpenCPN Launcher)
    python3 sun_moon.py               (dev on any machine with Signal K server)

Dependencies: skyfield, reportlab, matplotlib, requests
"""

import sys
import re
import os
import math
import sqlite3
import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta
from pathlib import Path
from skyfield.api import Loader, wgs84, Star
from skyfield import almanac
from skyfield.data import hipparcos
import json
import io
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
import matplotlib.pyplot as plt
from io import BytesIO

# Import shared Signal K helper and VNC window template
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.signalk import get_sk_value
from shared.vnc_window import VNCToolWindow

# Configuration Constants
DB_PATH = "/home/pi/.opencpn/navobj.db"
# DATA_DIR points to the data/ folder in this project (relative to script)
DATA_DIR = str(Path(__file__).parent.parent / "data")
EPH_FILE = "de421.bsp"
COORD_PRECISION = 4

class CelestialCalculator(VNCToolWindow):
    """Sun/Moon celestial navigation tool using VNC window template."""
    
    def __init__(self):
        super().__init__(title="Sun/Moon Navigation", width=650, height=450)
        
        eph_path = os.path.join(DATA_DIR, EPH_FILE)
        if not os.path.exists(eph_path):
            self.show_error("Missing Ephemeris", 
                f"Ephemeris file not found at:\n{eph_path}\n\nDownload de421.bsp to {DATA_DIR}")
            self.destroy()
            sys.exit(1)
        
        self.load = Loader(DATA_DIR)
        self.ts = self.load.timescale()
        self.eph = self.load(EPH_FILE)
        self.earth = self.eph['earth']
        
        self.using_waypoint = False
        
        self.setup_ui()

    def get_sk_pos(self):
        data = get_sk_value("navigation/position/value")
        if data and 'latitude' in data and 'longitude' in data:
            return self.format_coords(data['latitude'], data['longitude'])
        return ""

    def get_sk_environment(self):
        temp_data = get_sk_value("environment/outside/temperature/value")
        pressure_data = get_sk_value("environment/outside/pressure/value")

        temp_c = (temp_data - 273.15) if temp_data else 15.0
        pressure_mbar = (pressure_data / 100.0) if pressure_data else 1013.0

        return temp_c, pressure_mbar

    def get_sk_heading_degrees(self):
        """Return vessel heading in degrees from Signal K, or None if unavailable."""
        heading_paths = [
            "navigation/headingTrue/value",
            "navigation/headingMagnetic/value",
            "navigation/courseOverGroundTrue/value",
        ]

        for path in heading_paths:
            value = get_sk_value(path)
            if value is None:
                continue
            try:
                # Signal K angular values are typically radians.
                heading_deg = (math.degrees(float(value)) + 360.0) % 360.0
                return heading_deg
            except (TypeError, ValueError):
                continue

        return None

    def format_coords(self, lat, lon):
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        lat_s = f"{int(abs(lat))}° {(abs(lat)%1)*60:.{COORD_PRECISION}f}' {ns}"
        lon_s = f"{int(abs(lon))}° {(abs(lon)%1)*60:.{COORD_PRECISION}f}' {ew}"
        return f"{lat_s}\t{lon_s}"

    def format_dec_dms(self, dec_degrees):
        """Format declination in Degrees and Decimal Minutes (nautical almanac style)."""
        direction = "N" if dec_degrees >= 0 else "S"
        abs_dec = abs(dec_degrees)
        deg = int(abs_dec)
        minutes = (abs_dec % 1) * 60
        return f"{deg:02d}° {minutes:04.1f}' {direction}"

    def format_lat_lon_almanac(self, lat, lon):
        """Format position in Nautical Almanac style."""
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        return f"{abs(lat):6.2f}° {lat_dir}  {abs(lon):7.2f}° {lon_dir}"

    def get_waypoint_pos(self):
        if not os.path.exists(DB_PATH):
            messagebox.showwarning("Database", f"OpenCPN database not found at:\n{DB_PATH}")
            return
        
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            cursor = conn.cursor()
            found = False
            
            for table in ["waypoint", "routepoints", "route_points"]:
                try:
                    cursor.execute(f"SELECT lat, lon FROM {table} WHERE UPPER(name) = 'SUN'")
                    row = cursor.fetchone()
                    if row:
                        self.pos_entry.delete(0, tk.END)
                        self.pos_entry.insert(0, self.format_coords(row[0], row[1]))
                        self.using_waypoint = True
                        found = True
                        break
                except sqlite3.OperationalError:
                    continue
            
            conn.close()
            
            if not found:
                messagebox.showinfo("Not Found", "Waypoint 'sun' not found in database.\n\nCreate a waypoint named 'sun' in OpenCPN.")
        except Exception as e:
            messagebox.showerror("Database Error", f"Error reading OpenCPN database:\n{e}")

    def setup_ui(self):
        tk.Label(self.content_frame, text="Position (Signal K or Waypoint):").pack(pady=5)
        self.pos_entry = tk.Entry(self.content_frame, width=55)
        self.pos_entry.pack(pady=5, padx=10)
        
        initial_pos = self.get_sk_pos()
        self.pos_entry.insert(0, initial_pos)
        self.using_waypoint = False

        btn_frame = tk.Frame(self.content_frame)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="Use Sun Waypoint", command=self.get_waypoint_pos).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Calculate", command=self.calculate, bg="green", fg="white").pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Brightest Stars", command=self.calculate_stars, bg="blue", fg="white").pack(side=tk.LEFT, padx=5)

    def parse_position(self):
        """Parse position from entry field."""
        raw = self.pos_entry.get()
        matches = re.findall(r"(\d+)°\s*([\d\.]+)'\s*([NSEW])", raw)
        if len(matches) < 2:
            return None, None
        
        coords = []
        for d, m, direction in matches:
            val = float(d) + float(m)/60
            if direction in ['S', 'W']:
                val = -val
            coords.append(val)
        return coords[0], coords[1]

    def calculate(self):
        lat, lon = self.parse_position()
        if lat is None:
            messagebox.showerror("Format Error", "Use format: 32° 27.84' N  040° 53.99' W")
            return

        topos = wgs84.latlon(lat, lon)
        observer = self.earth + topos
        t = self.ts.now()
        
        # Calculate zone offset
        self.zone_hours = round(lon / 15)
        
        if self.using_waypoint:
            temp_c, pressure_mbar = 15.0, 1013.0
            env_note = ""
        else:
            temp_c, pressure_mbar = self.get_sk_environment()
            env_note = f"    Atmospheric Conditions: {temp_c:.1f}°C, {pressure_mbar:.1f} mbar\n"
        
        sun_p = observer.at(t).observe(self.eph['sun']).apparent()
        moon_p = observer.at(t).observe(self.eph['moon']).apparent()
        
        s_alt, s_az, _ = sun_p.altaz(temperature_C=temp_c, pressure_mbar=pressure_mbar)
        m_alt, m_az, _ = moon_p.altaz(temperature_C=temp_c, pressure_mbar=pressure_mbar)
        
        s_ra, s_dec, s_dist = sun_p.radec()
        m_ra, m_dec, m_dist = moon_p.radec()

        t0 = self.ts.utc(t.utc_datetime().year, t.utc_datetime().month, t.utc_datetime().day, 0, 0, 0)
        t1 = self.ts.utc(t.utc_datetime().year, t.utc_datetime().month, t.utc_datetime().day, 23, 59, 59)
        
        rise_set_text = self.calculate_rise_set(topos, t0, t1, temp_c, pressure_mbar)
        twilight_text = self.calculate_twilight(topos, t0, t1)
        meridian_text = self.calculate_meridian_passage(topos, t0, t1, temp_c, pressure_mbar)
        
        zone_desc = f"(Zone {self.zone_hours:+d})" if self.zone_hours != 0 else "(Zone 0)"
        local_time = t.utc_datetime() + timedelta(hours=self.zone_hours)
        
        # Build output in Nautical Almanac style
        res = f"═══════════════════════════════════════════════════════════════════\n"
        res += f"              CELESTIAL NAVIGATION DATA\n"
        res += f"═══════════════════════════════════════════════════════════════════\n"
        res += f"Date (UTC):  {t.utc_strftime('%Y %B %d, %A')}\n"
        res += f"Time (UTC):  {t.utc_strftime('%H:%M:%S')}\n"
        res += f"Time (LMT):  {local_time.strftime('%H:%M:%S')} {zone_desc}\n"
        res += f"Position:    {self.format_lat_lon_almanac(lat, lon)}\n"
        if env_note:
            res += env_note
        res += f"───────────────────────────────────────────────────────────────────\n"
        res += f"BODY           Alt        Az        Dec\n"
        res += f"───────────────────────────────────────────────────────────────────\n"
        res += f"SUN         {s_alt.degrees:6.2f}°   {s_az.degrees:6.1f}°   {self.format_dec_dms(s_dec.degrees)}\n"
        res += f"MOON        {m_alt.degrees:6.2f}°   {m_az.degrees:6.1f}°   {self.format_dec_dms(m_dec.degrees)}\n"
        res += f"\n{twilight_text}"
        res += f"\n{rise_set_text}"
        res += f"\n{meridian_text}"

        self.withdraw()
        self.show_results(res)

    def get_constellation_lines(self):
        """Define constellation line patterns using HIP catalog numbers."""
        constellations = {
            'Orion': [
                (27989, 25336),  # Betelgeuse to Bellatrix
                (25336, 25428),  # Bellatrix to Alnilam (belt)
                (25428, 26311),  # Alnilam to Alnitak
                (26311, 26727),  # Alnitak to Saiph
                (26727, 24436),  # Saiph to Rigel
                (24436, 37279),  # Rigel to path
                (25428, 27989),  # Belt to Betelgeuse
            ],
            'Ursa Major': [
                (54061, 53910),  # Alioth to Mizar
                (53910, 50583),  # Mizar to Alkaid
                (54061, 59774),  # Alioth to Megrez
                (59774, 58001),  # Megrez to Phecda
                (58001, 62956),  # Phecda to Merak
                (62956, 65477),  # Merak to Dubhe
                (65477, 59774),  # Dubhe to Megrez
            ],
            'Cassiopeia': [
                (3179, 746),
                (746, 3179),
                (3179, 4427),
                (4427, 6686),
                (6686, 8886),
            ],
            'Leo': [
                (49669, 50583),
                (50583, 54872),
                (54872, 57632),
                (57632, 54879),
                (54879, 49669),
            ],
            'Gemini': [
                (45238, 44816),  # Castor
                (44816, 42911),
                (42911, 37826),
                (45238, 37826),  # Castor to Pollux
            ],
            'Scorpius': [
                (80763, 78820),  # Antares
                (78820, 84143),
                (84143, 86228),
                (80763, 78265),
            ],
            'Crux': [
                (60718, 62434),
                (61084, 59747),
            ],
            'Cygnus': [
                (102098, 100453),  # Deneb
                (100453, 97165),
                (97165, 95947),
                (97165, 104732),
            ],
        }
        return constellations

    def az_alt_to_polar(self, az_degrees, alt_degrees):
        """Convert azimuth/altitude to polar chart coordinates."""
        theta = math.radians(az_degrees)
        radius = max(0, min(90, 90 - alt_degrees))
        return theta, radius

    def get_boat_marker_path(self, heading_degrees):
        """Build a simple boat-shaped matplotlib marker, rotated to heading."""
        from matplotlib.path import Path as MplPath

        # A compact boat silhouette in marker-local coordinates.
        # +Y is "forward" (bow) before rotation.
        verts = [
            (0.00, 1.25),   # bow
            (0.52, 0.20),
            (0.40, -0.95),
            (0.00, -0.70),
            (-0.40, -0.95),
            (-0.52, 0.20),
            (0.00, 1.25),
        ]
        codes = [
            MplPath.MOVETO,
            MplPath.LINETO,
            MplPath.LINETO,
            MplPath.LINETO,
            MplPath.LINETO,
            MplPath.LINETO,
            MplPath.CLOSEPOLY,
        ]

        # Heading is clockwise from north; screen-space rotation is opposite sign.
        angle = math.radians(-heading_degrees)
        c = math.cos(angle)
        s = math.sin(angle)
        rotated = []
        for x, y in verts:
            rx = (x * c) - (y * s)
            ry = (x * s) + (y * c)
            rotated.append((rx, ry))

        return MplPath(rotated, codes)

    def plot_constellation_lines(self, ax, observer, time, data_json):
        """Draw constellation lines on the circular all-sky chart."""
        try:
            import json
            from skyfield.api import Star
            from skyfield.data import hipparcos
            
            # Load star catalog
            with self.load.open(hipparcos.URL) as f:
                df = hipparcos.load_dataframe(f)
            
            constellations = self.get_constellation_lines()
            
            # Calculate all HIP star positions needed for constellations
            all_hip = set()
            for lines in constellations.values():
                for hip1, hip2 in lines:
                    all_hip.add(hip1)
                    all_hip.add(hip2)
            
            # Get positions for constellation stars
            hip_positions = {}
            for hip_num in all_hip:
                if hip_num in df.index:
                    star = Star.from_dataframe(df.loc[hip_num])
                    astrometric = observer.at(time).observe(star)
                    apparent = astrometric.apparent()
                    alt, az, _ = apparent.altaz()
                    
                    # Only include if above horizon
                    if alt.degrees > 0:
                        hip_positions[hip_num] = (az.degrees, alt.degrees)
            
            # Draw constellation lines
            for const_name, lines in constellations.items():
                for hip1, hip2 in lines:
                    if hip1 in hip_positions and hip2 in hip_positions:
                        az1, alt1 = hip_positions[hip1]
                        az2, alt2 = hip_positions[hip2]
                        
                        # Handle azimuth wrap-around (0/360 boundary)
                        if abs(az2 - az1) > 180:
                            continue  # Skip lines that cross the boundary

                        theta1, radius1 = self.az_alt_to_polar(az1, alt1)
                        theta2, radius2 = self.az_alt_to_polar(az2, alt2)

                        ax.plot([theta1, theta2], [radius1, radius2], 
                               color='cyan', alpha=0.22, linewidth=0.8, 
                               linestyle='--', zorder=2)
            
        except Exception as e:
            print(f"Constellation lines error: {e}")

    def setup_polar_chart_axes(self, ax, period, time_str, compact=False, heading_degrees=None):
        """Configure a circular all-sky chart with compass orientation."""
        ax.set_theta_zero_location('N')
        ax.set_theta_direction(-1)
        ax.set_ylim(0, 90)
        ax.set_facecolor('#001133')

        ax.set_thetagrids(
            [0, 45, 90, 135, 180, 225, 270, 315],
            labels=['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
        )
        ax.set_rgrids(
            [15, 30, 45, 60, 75, 90],
            labels=['75°', '60°', '45°', '30°', '15°', 'Horizon'],
            angle=22.5
        )
        ax.grid(True, alpha=0.3, color='lightgray', linestyle='--')
        ax.tick_params(colors='white', which='both')
        ax.spines['polar'].set_color('white')

        chart_title = f'{period.upper()} Nautical Twilight Sky ({time_str})'
        if period.lower() == 'current':
            chart_title = f'Current Sky ({time_str})'

        title_size = 12 if compact else 14
        ax.set_title(
            chart_title,
            fontsize=title_size,
            color='white',
            weight='bold',
            pad=20 if compact else 24
        )

        # Boat / observer marker at center, rotated to match vessel heading.
        heading = heading_degrees if heading_degrees is not None else 0.0
        boat_marker = self.get_boat_marker_path(heading)
        ax.scatter(0, 0, s=260, marker=boat_marker, color='deepskyblue',
               edgecolors='white', linewidths=1.2, zorder=12)

        # Faint heading line from center toward the heading direction.
        heading_theta = math.radians(heading)
        ax.plot([heading_theta, heading_theta], [0, 10], color='deepskyblue',
            alpha=0.55, linewidth=1.2, zorder=11)

        if heading_degrees is not None:
            ax.text(0, 6, f'Boat {heading:.0f}°', fontsize=8 if compact else 9,
                color='deepskyblue', ha='center', va='center', weight='bold')
        else:
            ax.text(0, 6, 'Boat', fontsize=8 if compact else 9,
                color='deepskyblue', ha='center', va='center', weight='bold')

    def plot_sky_objects(self, ax, data, compact=False):
        """Plot stars, sun, and moon on the circular sky chart."""
        labels_added = set()
        label_sizes = {
            'bold': 7 if compact else 8,
            'bright': 6 if compact else 7,
            'medium': 5 if compact else 6,
            'moon': 7 if compact else 8,
            'sun': 7 if compact else 8,
        }

        marker_sizes = {
            'bold': 120,
            'bright': 120,
            'medium': 28,
            'moon': 120,
            'sun': 110,
        }

        for item in data:
            az = item['az']
            alt = item['alt']
            name = item['name']
            star_type = item['type']
            theta, radius = self.az_alt_to_polar(az, alt)

            if star_type in ('bold', 'bright', 'medium'):
                label_radius = max(0, radius - 2.6)
                label_color = 'yellow' if star_type == 'bold' else ('white' if star_type == 'bright' else '#c8d0d8')
                label_weight = 'bold' if star_type == 'bold' else 'normal'
                ax.text(theta, label_radius, name,
                        fontsize=label_sizes[star_type], color=label_color,
                        ha='center', va='center', weight=label_weight,
                        alpha=0.95 if star_type != 'medium' else 0.75, zorder=11)

            if star_type == 'bold':
                ax.scatter(theta, radius, s=marker_sizes['bold'], marker='*', color='gold',
                           edgecolors='orange', linewidths=1.4, zorder=10,
                           label='Top 5 Stars' if 'bold' not in labels_added else None)
                labels_added.add('bold')
            elif star_type == 'bright':
                ax.scatter(theta, radius, s=marker_sizes['bright'], marker='o', color='white',
                           edgecolors='gray', linewidths=0.9, zorder=5,
                           label='Bright Reference' if 'bright' not in labels_added else None)
                labels_added.add('bright')
            elif star_type == 'medium':
                ax.scatter(theta, radius, s=marker_sizes['medium'], marker='o', color='lightgray',
                           edgecolors='darkgray', linewidths=0.4, zorder=3,
                           alpha=0.9, label=None)
            elif star_type == 'moon':
                ax.scatter(theta, radius, s=marker_sizes['moon'], marker='D', color='orange',
                           edgecolors='gold', linewidths=1.4, zorder=8,
                           label='Moon' if 'moon' not in labels_added else None)
                labels_added.add('moon')
                ax.text(theta, max(0, radius - 2.8), name,
                        fontsize=label_sizes['moon'], color='orange',
                        ha='center', va='center', weight='bold')
            elif star_type == 'sun':
                ax.scatter(theta, radius, s=marker_sizes['sun'], marker='v', color='red',
                           edgecolors='darkred', linewidths=1.4, zorder=8,
                           label='Sun (-12°)' if 'sun' not in labels_added else None)
                labels_added.add('sun')
                ax.text(theta, min(88, radius + 2.8), name,
                        fontsize=label_sizes['sun'], color='red',
                        ha='center', va='center', weight='bold')

    def add_chart_legend(self, ax, compact=False):
        """Add legend and explanatory annotation to the chart."""
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            by_label = dict(zip(labels, handles))
            ax.legend(
                by_label.values(), by_label.keys(),
                loc='upper right', fontsize=9 if compact else 10,
                facecolor='#002255', edgecolor='white',
                labelcolor='white', framealpha=0.8
            )

        ax.text(
            0.98, 0.02,
            'Center = Zenith / Boat   Outer ring = Horizon   Labels = all plotted stars',
            transform=ax.transAxes,
            fontsize=8 if compact else 9,
            color='white',
            ha='right', va='bottom',
            bbox=dict(boxstyle='round', facecolor='#002255', alpha=0.8, edgecolor='white')
        )

    def calculate_stars(self):
        """Calculate a simplified current-time star reference chart."""
        lat, lon = self.parse_position()
        if lat is None:
            messagebox.showerror("Format Error", "Use format: 32° 27.84' N  040° 53.99' W")
            return

        try:
            with self.load.open(hipparcos.URL) as f:
                df = hipparcos.load_dataframe(f)

            df_chart = df[df['magnitude'] <= 2.2].copy()
            df_chart = df_chart[df_chart['ra_degrees'].notnull()]

            df_table = df[df['magnitude'] <= 1.5].copy()
            df_table = df_table[df_table['ra_degrees'].notnull()]

            topos = wgs84.latlon(lat, lon)
            observer = self.earth + topos
            t = self.ts.now()

            self.zone_hours = round(lon / 15)
            zone_desc = f"(Zone {self.zone_hours:+d})" if self.zone_hours != 0 else "(Zone 0)"
            local_time = t.utc_datetime() + timedelta(hours=self.zone_hours)

            moon_p = observer.at(t).observe(self.eph['moon']).apparent()
            m_alt, m_az, _ = moon_p.altaz()

            sun_p = observer.at(t).observe(self.eph['sun']).apparent()
            s_alt, s_az, _ = sun_p.altaz()

            stars_table = Star.from_dataframe(df_table)
            apparent_table = observer.at(t).observe(stars_table).apparent()
            alt_table, az_table, _ = apparent_table.altaz()
            _, dec_table, _ = apparent_table.radec()

            mask_table = (alt_table.degrees > 15) & (alt_table.degrees < 75)
            visible_indices_table = df_table.index[mask_table].tolist()

            visible_df_table = df_table.loc[visible_indices_table].copy()
            visible_df_table['altitude'] = alt_table.degrees[mask_table]
            visible_df_table['azimuth'] = az_table.degrees[mask_table]
            visible_df_table['declination'] = dec_table.degrees[mask_table]
            visible_df_table = visible_df_table.sort_values('magnitude').head(5)
            top_star_ids = set(visible_df_table.index.tolist())

            stars_chart = Star.from_dataframe(df_chart)
            apparent_chart = observer.at(t).observe(stars_chart).apparent()
            alt_chart, az_chart, _ = apparent_chart.altaz()

            mask_chart = alt_chart.degrees > 10
            visible_chart_indices = df_chart.index[mask_chart]

            current_data = []
            for i, idx in enumerate(visible_chart_indices):
                altitude = alt_chart.degrees[mask_chart][i]
                azimuth = az_chart.degrees[mask_chart][i]
                magnitude = df_chart.loc[idx, 'magnitude']

                current_data.append({
                    'name': self.get_star_name(idx),
                    'az': float(azimuth),
                    'alt': float(altitude),
                    'mag': float(magnitude),
                    'type': 'bold' if idx in top_star_ids else ('bright' if magnitude <= 1.5 else 'medium'),
                    'twilight': 'current'
                })

            if m_alt.degrees > 0:
                current_data.append({
                    'name': 'Moon',
                    'az': float(m_az.degrees),
                    'alt': float(m_alt.degrees),
                    'phase': 0.65,
                    'type': 'moon',
                    'twilight': 'current'
                })

            if s_alt.degrees > -18:
                current_data.append({
                    'name': 'Sun',
                    'az': float(s_az.degrees),
                    'alt': float(s_alt.degrees),
                    'type': 'sun',
                    'twilight': 'current'
                })

            res = f"═══════════════════════════════════════════════════════════════════\n"
            res += f"            CURRENT SKY REFERENCE\n"
            res += f"═══════════════════════════════════════════════════════════════════\n"
            res += f"Date (UTC):  {t.utc_strftime('%Y %B %d, %A')}\n"
            res += f"Time (UTC):  {t.utc_strftime('%H:%M:%S')}\n"
            res += f"Time (LMT):  {local_time.strftime('%H:%M:%S')} {zone_desc}\n"
            res += f"Position:    {self.format_lat_lon_almanac(lat, lon)}\n"
            heading_deg = self.get_sk_heading_degrees()

            res += f"───────────────────────────────────────────────────────────────────\n"
            res += f"Sun:         Alt {s_alt.degrees:5.1f}°   Az {s_az.degrees:6.1f}°\n"
            res += f"Moon:        Alt {m_alt.degrees:5.1f}°   Az {m_az.degrees:6.1f}°"
            if m_alt.degrees > 0:
                res += f"   (Visible)\n"
            else:
                res += f"   (Below horizon)\n"
            if heading_deg is not None:
                res += f"Heading:     {heading_deg:6.1f}° (from Signal K)\n"
            else:
                res += f"Heading:     unavailable from Signal K\n"

            res += f"\nTOP REFERENCE STARS NOW\n"
            res += f"───────────────────────────────────────────────────────────────────\n"
            res += f"STAR NAME          Vmag    Alt      Az       Dec\n"
            res += f"───────────────────────────────────────────────────────────────────\n"

            for idx, row in visible_df_table.iterrows():
                star_name = self.get_star_name(idx)
                res += f"{star_name:15s}   {row['magnitude']:4.2f}   {row['altitude']:5.1f}°   {row['azimuth']:6.1f}°   {self.format_dec_dms(row['declination'])}\n"

            res += f"\n───────────────────────────────────────────────────────────────────\n"
            res += f"Note: Circular chart shows the sky right now.\n"
            res += f"      Center = zenith above the boat, outer ring = horizon.\n"
            res += f"      Only the key stars, Sun, and Moon are labelled."

            self.withdraw()
            self.show_results(res)

            current_json = json.dumps(current_data) if current_data else None
            if current_data:
                self.create_twilight_chart(
                    current_json,
                    "current",
                    t.utc_strftime('%H:%M UTC'),
                    observer,
                    t,
                    heading_degrees=heading_deg
                )

            pdf_filename = self.export_to_pdf(res)
            if pdf_filename:
                print(f"\n✓ PDF saved to: {os.path.abspath(pdf_filename)}")

        except Exception as e:
            import traceback
            messagebox.showerror("Star Calculation Error", f"Error calculating stars:\n{e}\n\n{traceback.format_exc()}")

    def create_twilight_chart(self, data_json, period, time_str, observer, time, heading_degrees=None):
        """Create circular all-sky chart for dawn or dusk using matplotlib."""
        try:
            import matplotlib
            matplotlib.use('TkAgg')  # Interactive backend for display
            
            data = json.loads(data_json)
            
            # Create figure
            fig, ax = plt.subplots(figsize=(10, 10), subplot_kw={'projection': 'polar'})
            fig.patch.set_facecolor('#001133')

            self.setup_polar_chart_axes(ax, period, time_str, compact=False, heading_degrees=heading_degrees)
            self.plot_sky_objects(ax, data, compact=False)
            self.plot_constellation_lines(ax, observer, time, data_json)
            self.add_chart_legend(ax, compact=False)
            
            plt.tight_layout()
            plt.show()
            
        except Exception as e:
            import traceback
            print(f"Chart creation error: {e}")
            print(traceback.format_exc())

    def generate_chart_image(self, data_json, period, time_str, observer, time, heading_degrees=None):
        """Generate circular all-sky chart as image for PDF embedding."""
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            
            data = json.loads(data_json)
            
            # Create figure
            fig, ax = plt.subplots(figsize=(8.5, 8.5), subplot_kw={'projection': 'polar'})
            fig.patch.set_facecolor('#001133')

            self.setup_polar_chart_axes(ax, period, time_str, compact=True, heading_degrees=heading_degrees)
            self.plot_sky_objects(ax, data, compact=True)
            self.plot_constellation_lines(ax, observer, time, data_json)
            self.add_chart_legend(ax, compact=True)
            
            # Save to BytesIO
            img_buffer = BytesIO()
            plt.tight_layout()
            plt.savefig(img_buffer, format='png', dpi=100, facecolor='#001133')
            img_buffer.seek(0)
            plt.close()
            
            return img_buffer
        except Exception as e:
            import traceback
            print(f"Chart image generation error: {e}")
            print(traceback.format_exc())
            return None

    def export_to_pdf(self, text_data, dawn_data_json=None, dusk_data_json=None, 
                      dawn_time=None, dusk_time=None, observer=None, filename=None):
        """Export celestial data and charts to professional PDF."""
        try:
            if filename is None:
                filename = f"celestial_nav_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            
            # Create PDF document
            doc = SimpleDocTemplate(filename, pagesize=letter,
                                   rightMargin=0.5*inch, leftMargin=0.5*inch,
                                   topMargin=0.5*inch, bottomMargin=0.5*inch)
            
            # Container for PDF elements
            elements = []
            
            # Styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=24,
                textColor=colors.HexColor('#1E5A8E'),
                spaceAfter=10,
                alignment=TA_CENTER,
                fontName='Helvetica-Bold'
            )
            
            heading_style = ParagraphStyle(
                'CustomHeading',
                parent=styles['Heading2'],
                fontSize=14,
                textColor=colors.HexColor('#1E5A8E'),
                spaceAfter=8,
                spaceBefore=12,
                fontName='Helvetica-Bold'
            )
            
            body_style = ParagraphStyle(
                'CustomBody',
                parent=styles['BodyText'],
                fontSize=10,
                leading=12,
                textColor=colors.black,
                alignment=TA_LEFT,
                fontName='Courier'
            )
            
            # Title
            elements.append(Paragraph("CELESTIAL NAVIGATION REPORT", title_style))
            elements.append(Spacer(1, 0.2*inch))
            
            # Add timestamp
            timestamp = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"
            elements.append(Paragraph(f"<i>{timestamp}</i>", styles['Normal']))
            elements.append(Spacer(1, 0.3*inch))
            
            # Add text data (formatted monospace)
            text_lines = text_data.split('\n')
            for line in text_lines:
                if line.startswith('═'):
                    elements.append(Spacer(1, 0.1*inch))
                elif line.startswith('###'):
                    # Section header
                    section_title = line.replace('###', '').strip()
                    elements.append(Paragraph(section_title, heading_style))
                elif line.strip() == '':
                    elements.append(Spacer(1, 0.05*inch))
                else:
                    # Use monospace for data lines
                    elements.append(Paragraph(f"<font face='Courier' size='9'>{line}</font>", 
                                            ParagraphStyle('Normal', 
                                                          parent=styles['Normal'],
                                                          fontSize=9,
                                                          leading=11)))
            
            # Add charts if available
            if dawn_data_json and dawn_time and observer:
                elements.append(PageBreak())
                elements.append(Paragraph("DAWN STAR FIELD CHART", heading_style))
                
                dawn_img = self.generate_chart_image(dawn_data_json, "dawn", 
                                                    dawn_time.utc_strftime('%H:%M UTC'),
                                                    observer, dawn_time)
                if dawn_img:
                    img = Image(dawn_img, width=6*inch, height=4.2*inch)
                    elements.append(img)
                    elements.append(Spacer(1, 0.2*inch))
            
            if dusk_data_json and dusk_time and observer:
                elements.append(PageBreak())
                elements.append(Paragraph("DUSK STAR FIELD CHART", heading_style))
                
                dusk_img = self.generate_chart_image(dusk_data_json, "dusk", 
                                                   dusk_time.utc_strftime('%H:%M UTC'),
                                                   observer, dusk_time)
                if dusk_img:
                    img = Image(dusk_img, width=6*inch, height=4.2*inch)
                    elements.append(img)
                    elements.append(Spacer(1, 0.2*inch))
            
            # Build PDF
            doc.build(elements)
            
            print(f"\n✓ PDF exported successfully: {filename}")
            return filename
        
        except Exception as e:
            import traceback
            print(f"PDF export error: {e}")
            print(traceback.format_exc())
            return None

    def get_star_name(self, hip_number):
        """Get common star name from HIP catalog number."""
        star_names = {
            32349: "Sirius",
            30438: "Canopus",
            71683: "Rigil Kent",
            69673: "Arcturus",
            91262: "Vega",
            24608: "Capella",
            37279: "Rigel",
            27989: "Betelgeuse",
            21421: "Aldebaran",
            37826: "Procyon",
            25336: "Bellatrix",
            113368: "Spica",
            80763: "Antares",
            60718: "Pollux",
            45238: "Castor",
            97649: "Altair",
            102098: "Deneb",
            113881: "Shaula",
            86032: "Dubhe",
            677: "Alpheratz",
            15863: "Polaris",
            65474: "Regulus",
            62956: "Merak",
            50583: "Alkaid",
            54061: "Alioth",
            113963: "Alnair",
            107315: "Peacock",
            3419: "Schedar",
            746: "Fomalhaut",
            76267: "Kochab"
        }
        return star_names.get(hip_number, f"HIP {hip_number}")

    def format_time_with_local(self, t):
        """Format time as UTC and Local (LMT)."""
        utc_str = t.utc_strftime('%H:%M')
        local_dt = t.utc_datetime() + timedelta(hours=self.zone_hours)
        local_str = local_dt.strftime('%H:%M')
        return f"{utc_str}   {local_str}"

    def calculate_twilight(self, topos, t0, t1):
        """Calculate civil and nautical twilight times."""
        f = almanac.dark_twilight_day(self.eph, topos)
        times, events = almanac.find_discrete(t0, t1, f)
        
        result = "\n"
        result += "TWILIGHT               UTC      LMT\n"
        result += "───────────────────────────────────────────────────────────────────\n"
        
        civil_start, civil_end = None, None
        naut_start, naut_end = None, None
        
        for ti, event in zip(times, events):
            if event == 2 and naut_start is None:
                naut_start = ti
            elif event in [1, 3] and naut_start is not None and naut_end is None:
                naut_end = ti
            if event == 3 and civil_start is None:
                civil_start = ti
            elif event in [2, 4] and civil_start is not None and civil_end is None:
                civil_end = ti
        
        # Change these lines from "if naut_start:" to "if naut_start is not None:"
        if naut_start is not None:
            result += f"Nautical begins    {self.format_time_with_local(naut_start)}\n"
        if civil_start is not None:
            result += f"Civil begins       {self.format_time_with_local(civil_start)}\n"
        if civil_end is not None:
            result += f"Civil ends         {self.format_time_with_local(civil_end)}\n"
        if naut_end is not None:
            result += f"Nautical ends      {self.format_time_with_local(naut_end)}\n"
        
        return result


    def calculate_rise_set(self, topos, t0, t1, temp_c, pressure_mbar):
        """Calculate rise and set times for sun and moon."""
        result = "═══════════════════════════════════════════════════════════════════\n"
        result += "RISE/SET               UTC     LMT\n"
        result += "───────────────────────────────────────────────────────────────────\n"
        
        # Sun rise/set
        f = almanac.sunrise_sunset(self.eph, topos)
        times, events = almanac.find_discrete(t0, t1, f)
        
        for ti, event in zip(times, events):
            if event == 1:
                result += f"Sunrise             {self.format_time_with_local(ti)}\n"
            else:
                result += f"Sunset              {self.format_time_with_local(ti)}\n"
        
        # Moon rise/set
        f = almanac.risings_and_settings(self.eph, self.eph['moon'], topos)
        times, events = almanac.find_discrete(t0, t1, f)
        
        for ti, event in zip(times, events):
            if event == 1:
                result += f"Moonrise            {self.format_time_with_local(ti)}\n"
            else:
                result += f"Moonset             {self.format_time_with_local(ti)}\n"
        
        return result

    def calculate_meridian_passage(self, topos, t0, t1, temp_c, pressure_mbar):
        """Calculate meridian passage (culmination) times."""
        result = "═══════════════════════════════════════════════════════════════════\n"
        result += "MERIDIAN PASSAGE       UTC     LMT      Alt\n"
        result += "───────────────────────────────────────────────────────────────────\n"
        
        observer = self.earth + topos
        
        # Sun meridian
        f = almanac.meridian_transits(self.eph, self.eph['sun'], topos)
        times, events = almanac.find_discrete(t0, t1, f)
        
        for ti in times:
            sun_p = observer.at(ti).observe(self.eph['sun']).apparent()
            alt, az, _ = sun_p.altaz(temperature_C=temp_c, pressure_mbar=pressure_mbar)
            result += f"Sun                 {self.format_time_with_local(ti)}   {alt.degrees:5.1f}°\n"
        
        # Moon meridian
        f = almanac.meridian_transits(self.eph, self.eph['moon'], topos)
        times, events = almanac.find_discrete(t0, t1, f)
        
        for ti in times:
            moon_p = observer.at(ti).observe(self.eph['moon']).apparent()
            alt, az, _ = moon_p.altaz(temperature_C=temp_c, pressure_mbar=pressure_mbar)
            result += f"Moon                {self.format_time_with_local(ti)}   {alt.degrees:5.1f}°\n"
        
        return result

    def show_results(self, text):
        """Display results in a scrollable text window."""
        result_win = tk.Toplevel(self)
        result_win.title("Celestial Navigation Results")
        result_win.geometry("800x600")
        
        scrollbar = tk.Scrollbar(result_win)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        text_widget = tk.Text(result_win, wrap=tk.WORD, yscrollcommand=scrollbar.set, 
                             font=("Courier", 10))
        text_widget.pack(expand=True, fill=tk.BOTH)
        scrollbar.config(command=text_widget.yview)
        
        text_widget.insert(tk.END, text)
        text_widget.config(state=tk.DISABLED)
        
        def on_close():
            result_win.destroy()
            self.deiconify()
        
        result_win.protocol("WM_DELETE_WINDOW", on_close)

    def run(self):
        self.mainloop()

if __name__ == "__main__":
    app = CelestialCalculator()
    app.run()
