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

    def plot_constellation_lines(self, ax, observer, time, data_json):
        """Draw constellation lines on the chart."""
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
                        
                        ax.plot([az1, az2], [alt1, alt2], 
                               color='cyan', alpha=0.4, linewidth=1, 
                               linestyle='--', zorder=2)
            
            # Add constellation labels at centroid
            constellation_labels = {
                'Orion': 'ORION',
                'Ursa Major': 'URSA MAJOR',
                'Cassiopeia': 'CASSIOPEIA',
                'Leo': 'LEO',
                'Gemini': 'GEMINI',
                'Scorpius': 'SCORPIUS',
                'Crux': 'CRUX',
                'Cygnus': 'CYGNUS',
            }
            
            for const_name in constellations.keys():
                # Find centroid of visible stars in constellation
                const_stars = [hip_positions[hip] for line in constellations[const_name] 
                              for hip in line if hip in hip_positions]
                if len(const_stars) >= 2:
                    avg_az = sum(pos[0] for pos in const_stars) / len(const_stars)
                    avg_alt = sum(pos[1] for pos in const_stars) / len(const_stars)
                    
                    ax.text(avg_az, avg_alt, constellation_labels[const_name], 
                           fontsize=8, color='cyan', alpha=0.6, 
                           ha='center', va='center', style='italic',
                           bbox=dict(boxstyle='round,pad=0.3', 
                                    facecolor='#001133', 
                                    edgecolor='cyan', 
                                    alpha=0.3))
            
        except Exception as e:
            print(f"Constellation lines error: {e}")

    def calculate_stars(self):
        """Calculate brightest stars visible during nautical twilight."""
        lat, lon = self.parse_position()
        if lat is None:
            messagebox.showerror("Format Error", "Use format: 32° 27.84' N  040° 53.99' W")
            return
        
        try:
            # Load Hipparcos star catalog
            with self.load.open(hipparcos.URL) as f:
                df = hipparcos.load_dataframe(f)
            
            # Filter brightest stars (Vmag <= 2.4 for chart)
            df_chart = df[df['magnitude'] <= 3.0].copy()
            df_chart = df_chart[df_chart['ra_degrees'].notnull()]
            
            # Filter brightest stars (Vmag <= 1.5 for table)
            df_table = df[df['magnitude'] <= 1.5].copy()
            df_table = df_table[df_table['ra_degrees'].notnull()]
            
            topos = wgs84.latlon(lat, lon)
            observer = self.earth + topos
            t = self.ts.now()
            
            # Calculate zone offset
            self.zone_hours = round(lon / 15)
            
            # Find nautical twilight times (both dawn and dusk)
            t0 = self.ts.utc(t.utc_datetime().year, t.utc_datetime().month, t.utc_datetime().day, 0, 0, 0)
            t1 = self.ts.utc(t.utc_datetime().year, t.utc_datetime().month, t.utc_datetime().day, 23, 59, 59)
            
            f = almanac.dark_twilight_day(self.eph, topos)
            times, events = almanac.find_discrete(t0, t1, f)
            
            # Find dawn and dusk nautical twilight times
            dawn_time = None
            dusk_time = None
            
            for i, (ti, event) in enumerate(zip(times, events)):
                if event == 2 and ti.utc_datetime().hour < 12 and dawn_time is None:
                    dawn_time = ti
                elif event == 2 and ti.utc_datetime().hour >= 12 and dusk_time is None:
                    dusk_time = ti
            
            if dusk_time is None:
                for ti, event in zip(times, events):
                    if event == 2 and ti.utc_datetime().hour >= 18:
                        dusk_time = ti
                        break
            
            # Build output with both dawn and dusk
            res = f"═══════════════════════════════════════════════════════════════════\n"
            res += f"         BRIGHTEST STARS - NAUTICAL TWILIGHT\n"
            res += f"═══════════════════════════════════════════════════════════════════\n"
            res += f"Date (UTC):  {t.utc_strftime('%Y %B %d, %A')}\n"
            res += f"Position:    {self.format_lat_lon_almanac(lat, lon)}\n"
            res += f"───────────────────────────────────────────────────────────────────\n"
            
            zone_desc = f"(Zone {self.zone_hours:+d})" if self.zone_hours != 0 else "(Zone 0)"
            
            chart_data = []
            dawn_data = []
            dusk_data = []
            
            # DAWN SECTION
            if dawn_time is not None:
                local_dawn = dawn_time.utc_datetime() + timedelta(hours=self.zone_hours)
                res += f"\n### DAWN NAUTICAL TWILIGHT ###\n"
                res += f"Time (UTC):  {dawn_time.utc_strftime('%H:%M:%S')}\n"
                res += f"Time (LMT):  {local_dawn.strftime('%H:%M:%S')} {zone_desc}\n\n"
                
                # Calculate moon position at dawn
                moon_p = observer.at(dawn_time).observe(self.eph['moon']).apparent()
                m_alt, m_az, _ = moon_p.altaz()
                res += f"Moon:        Alt {m_alt.degrees:5.1f}°   Az {m_az.degrees:6.1f}°"
                if m_alt.degrees > 0:
                    res += f"   (Visible)\n"
                else:
                    res += f"   (Below horizon)\n"
                
                # Calculate stars at dawn for table
                stars_table = Star.from_dataframe(df_table)
                astrometric_table = observer.at(dawn_time).observe(stars_table)
                apparent_table = astrometric_table.apparent()
                
                alt_table, az_table, _ = apparent_table.altaz()
                ra_table, dec_table, _ = apparent_table.radec()
                
                mask_table = (alt_table.degrees > 15) & (alt_table.degrees < 75)
                visible_indices_table = df_table.index[mask_table].tolist()
                
                visible_df_table = df_table.loc[visible_indices_table].copy()
                visible_df_table['altitude'] = alt_table.degrees[mask_table]
                visible_df_table['azimuth'] = az_table.degrees[mask_table]
                visible_df_table['declination'] = dec_table.degrees[mask_table]
                visible_df_table = visible_df_table.sort_values('magnitude').head(5)
                
                res += f"\nSTAR NAME          Vmag    Alt      Az       Dec\n"
                res += f"───────────────────────────────────────────────────────────────────\n"
                
                for idx, row in visible_df_table.iterrows():
                    star_name = self.get_star_name(idx)
                    res += f"{star_name:15s}   {row['magnitude']:4.2f}   {row['altitude']:5.1f}°   {row['azimuth']:6.1f}°   {self.format_dec_dms(row['declination'])}\n"
                
                # Calculate all stars for chart (Vmag <= 2.4)
                stars_chart = Star.from_dataframe(df_chart)
                astrometric_chart = observer.at(dawn_time).observe(stars_chart)
                apparent_chart = astrometric_chart.apparent()
                
                alt_chart, az_chart, _ = apparent_chart.altaz()
                ra_chart, dec_chart, _ = apparent_chart.radec()
                
                mask_chart = alt_chart.degrees > 0
                visible_chart_indices = df_chart.index[mask_chart]
                
                for i, idx in enumerate(visible_chart_indices):
                    a = alt_chart.degrees[mask_chart][i]
                    z = az_chart.degrees[mask_chart][i]
                    mag = df_chart.loc[idx, 'magnitude']
                    
                    dawn_data.append({
                        'name': self.get_star_name(idx),
                        'az': float(z),
                        'alt': float(a),
                        'mag': float(mag),
                        'type': 'bold' if mag <= 0.1 else ('bright' if mag <= 1.5 else 'medium'),
                        'twilight': 'dawn'
                    })
                
                # Add Moon to dawn chart
                if m_alt.degrees > 0:
                    dawn_data.append({
                        'name': 'Moon',
                        'az': float(m_az.degrees),
                        'alt': float(m_alt.degrees),
                        'phase': 0.65,
                        'type': 'moon',
                        'twilight': 'dawn'
                    })
                
                # Add Sun at nautical twilight position for dawn
                sun_p = observer.at(dawn_time).observe(self.eph['sun']).apparent()
                s_alt, s_az, _ = sun_p.altaz()
                dawn_data.append({
                    'name': 'Sun',
                    'az': float(s_az.degrees),
                    'alt': float(s_alt.degrees),
                    'type': 'sun',
                    'twilight': 'dawn'
                })
            
            # DUSK SECTION
            if dusk_time is not None:
                local_dusk = dusk_time.utc_datetime() + timedelta(hours=self.zone_hours)
                res += f"\n### DUSK NAUTICAL TWILIGHT ###\n"
                res += f"Time (UTC):  {dusk_time.utc_strftime('%H:%M:%S')}\n"
                res += f"Time (LMT):  {local_dusk.strftime('%H:%M:%S')} {zone_desc}\n\n"
                
                # Calculate moon position at dusk
                moon_p = observer.at(dusk_time).observe(self.eph['moon']).apparent()
                m_alt, m_az, _ = moon_p.altaz()
                res += f"Moon:        Alt {m_alt.degrees:5.1f}°   Az {m_az.degrees:6.1f}°"
                if m_alt.degrees > 0:
                    res += f"   (Visible)\n"
                else:
                    res += f"   (Below horizon)\n"
                
                # Calculate stars at dusk for table
                stars_table = Star.from_dataframe(df_table)
                astrometric_table = observer.at(dusk_time).observe(stars_table)
                apparent_table = astrometric_table.apparent()
                
                alt_table, az_table, _ = apparent_table.altaz()
                ra_table, dec_table, _ = apparent_table.radec()
                
                mask_table = (alt_table.degrees > 15) & (alt_table.degrees < 75)
                visible_indices_table = df_table.index[mask_table].tolist()
                
                visible_df_table = df_table.loc[visible_indices_table].copy()
                visible_df_table['altitude'] = alt_table.degrees[mask_table]
                visible_df_table['azimuth'] = az_table.degrees[mask_table]
                visible_df_table['declination'] = dec_table.degrees[mask_table]
                visible_df_table = visible_df_table.sort_values('magnitude').head(5)
                
                res += f"\nSTAR NAME          Vmag    Alt      Az       Dec\n"
                res += f"───────────────────────────────────────────────────────────────────\n"
                
                for idx, row in visible_df_table.iterrows():
                    star_name = self.get_star_name(idx)
                    res += f"{star_name:15s}   {row['magnitude']:4.2f}   {row['altitude']:5.1f}°   {row['azimuth']:6.1f}°   {self.format_dec_dms(row['declination'])}\n"
                
                # Calculate all stars for chart (Vmag <= 2.4)
                stars_chart = Star.from_dataframe(df_chart)
                astrometric_chart = observer.at(dusk_time).observe(stars_chart)
                apparent_chart = astrometric_chart.apparent()
                
                alt_chart, az_chart, _ = apparent_chart.altaz()
                ra_chart, dec_chart, _ = apparent_chart.radec()
                
                mask_chart = alt_chart.degrees > 0
                visible_chart_indices = df_chart.index[mask_chart]
                
                for i, idx in enumerate(visible_chart_indices):
                    a = alt_chart.degrees[mask_chart][i]
                    z = az_chart.degrees[mask_chart][i]
                    mag = df_chart.loc[idx, 'magnitude']
                    
                    dusk_data.append({
                        'name': self.get_star_name(idx),
                        'az': float(z),
                        'alt': float(a),
                        'mag': float(mag),
                        'type': 'bold' if mag <= 0.1 else ('bright' if mag <= 1.5 else 'medium'),
                        'twilight': 'dusk'
                    })
                
                # Add Moon to dusk chart
                if m_alt.degrees > 0:
                    dusk_data.append({
                        'name': 'Moon',
                        'az': float(m_az.degrees),
                        'alt': float(m_alt.degrees),
                        'phase': 0.65,
                        'type': 'moon',
                        'twilight': 'dusk'
                    })
                
                # Add Sun at nautical twilight position for dusk
                sun_p = observer.at(dusk_time).observe(self.eph['sun']).apparent()
                s_alt, s_az, _ = sun_p.altaz()
                dusk_data.append({
                    'name': 'Sun',
                    'az': float(s_az.degrees),
                    'alt': float(s_alt.degrees),
                    'type': 'sun',
                    'twilight': 'dusk'
                })
            
            res += f"\n───────────────────────────────────────────────────────────────────\n"
            res += f"Note: Stars listed at nautical twilight (Sun altitude -12°)\n"
            res += f"      Altitude range: 15° - 75° (optimal for sextant sights)\n"
            res += f"      See charts below for visual star field reference"
            
            # Show text results first
            self.withdraw()
            self.show_results(res)
            
            # Generate charts for dawn and dusk
            dawn_json = json.dumps(dawn_data) if dawn_data else None
            dusk_json = json.dumps(dusk_data) if dusk_data else None
            
            if dawn_data and dawn_time is not None:
                self.create_twilight_chart(dawn_json, "dawn", 
                                          dawn_time.utc_strftime('%H:%M UTC'),
                                          observer, dawn_time)
            
            if dusk_data and dusk_time is not None:
                self.create_twilight_chart(dusk_json, "dusk", 
                                          dusk_time.utc_strftime('%H:%M UTC'),
                                          observer, dusk_time)
            
            # Export to PDF
            pdf_filename = self.export_to_pdf(
                res,
                dawn_json if dawn_data else None,
                dusk_json if dusk_data else None,
                dawn_time if dawn_data else None,
                dusk_time if dusk_data else None,
                observer
            )
            
            if pdf_filename:
                print(f"\n✓ PDF saved to: {os.path.abspath(pdf_filename)}")
            
        except Exception as e:
            import traceback
            messagebox.showerror("Star Calculation Error", f"Error calculating stars:\n{e}\n\n{traceback.format_exc()}")

    def create_twilight_chart(self, data_json, period, time_str, observer, time):
        """Create azimuth-altitude chart for dawn or dusk using matplotlib."""
        try:
            import matplotlib
            matplotlib.use('TkAgg')  # Interactive backend for display
            
            data = json.loads(data_json)
            
            # Create figure
            fig, ax = plt.subplots(figsize=(12, 8))
            fig.patch.set_facecolor('#001133')
            ax.set_facecolor('#001133')
            
            # Plot stars by type
            bold_added = False
            bright_added = False
            medium_added = False
            moon_added = False
            sun_added = False
            
            for item in data:
                az = item['az']
                alt = item['alt']
                name = item['name']
                star_type = item['type']
                
                if star_type == 'bold':
                    if not bold_added:
                        ax.scatter(az, alt, s=350, marker='*', color='gold', edgecolors='orange', linewidths=2, zorder=10, label='Top 5 Stars')
                        bold_added = True
                    else:
                        ax.scatter(az, alt, s=350, marker='*', color='gold', edgecolors='orange', linewidths=2, zorder=10)
                    ax.text(az, alt+3, name, fontsize=10, color='yellow', ha='center', weight='bold')
                elif star_type == 'bright':
                    if not bright_added:
                        ax.scatter(az, alt, s=120, marker='o', color='white', edgecolors='gray', linewidths=1, zorder=5, label='Bright Reference')
                        bright_added = True
                    else:
                        ax.scatter(az, alt, s=120, marker='o', color='white', edgecolors='gray', linewidths=1, zorder=5)
                    ax.text(az, alt+2, name, fontsize=8, color='white', ha='center')
                elif star_type == 'medium':
                    if not medium_added:
                        ax.scatter(az, alt, s=60, marker='o', color='lightgray', edgecolors='darkgray', linewidths=0.5, zorder=3, label='Medium Reference')
                        medium_added = True
                    else:
                        ax.scatter(az, alt, s=60, marker='o', color='lightgray', edgecolors='darkgray', linewidths=0.5, zorder=3)
                elif star_type == 'moon':
                    if not moon_added:
                        ax.scatter(az, alt, s=200, marker='D', color='orange', edgecolors='gold', linewidths=2, zorder=8, label='Moon')
                        moon_added = True
                    else:
                        ax.scatter(az, alt, s=200, marker='D', color='orange', edgecolors='gold', linewidths=2, zorder=8)
                    ax.text(az, alt+3, name, fontsize=10, color='orange', ha='center', weight='bold')
                elif star_type == 'sun':
                    if not sun_added:
                        ax.scatter(az, alt, s=150, marker='v', color='red', edgecolors='darkred', linewidths=2, zorder=8, label='Sun (-12°)')
                        sun_added = True
                    else:
                        ax.scatter(az, alt, s=150, marker='v', color='red', edgecolors='darkred', linewidths=2, zorder=8)
                    ax.text(az, alt-4, name, fontsize=10, color='red', ha='center', weight='bold')
            
            # Draw constellation lines
            self.plot_constellation_lines(ax, observer, time, data_json)
            
            # Horizon line
            ax.axhline(y=0, color='black', linewidth=3, zorder=1)
            
            # Grid
            ax.grid(True, alpha=0.3, color='lightgray', linestyle='--')
            
            # Axis setup
            ax.set_xlim(0, 360)
            ax.set_ylim(-5, 90)
            ax.set_xticks([0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360])
            ax.set_xticklabels(['N\n0°', '30°', '60°', 'E\n90°', '120°', '150°', 'S\n180°', '210°', '240°', 'W\n270°', '300°', '330°', '360°'])
            ax.set_yticks([0, 15, 30, 45, 60, 75, 90])
            
            # Labels
            ax.set_xlabel('Azimuth (0°=N, 90°=E, 180°=S, 270°=W)', fontsize=12, color='white')
            ax.set_ylabel('Altitude°', fontsize=12, color='white')
            ax.set_title(f'{period.upper()} Nautical Twilight Star Field ({time_str})', 
                         fontsize=14, color='white', weight='bold', pad=20)
            
            # Tick colors
            ax.tick_params(colors='white', which='both')
            for spine in ax.spines.values():
                spine.set_color('white')
            
            # Legend
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                # Remove duplicates
                by_label = dict(zip(labels, handles))
                ax.legend(by_label.values(), by_label.keys(), 
                         loc='upper right', fontsize=10, 
                         facecolor='#002255', edgecolor='white', 
                         labelcolor='white', framealpha=0.8)
            
            # Annotation
            ax.text(0.98, 0.02, '★ Top 5 | ● Bright | ○ Medium | ◆ Moon | ▼ Sun',
                   transform=ax.transAxes, fontsize=9, color='white',
                   ha='right', va='bottom', bbox=dict(boxstyle='round', 
                   facecolor='#002255', alpha=0.8, edgecolor='white'))
            
            plt.tight_layout()
            plt.show()
            
        except Exception as e:
            import traceback
            print(f"Chart creation error: {e}")
            print(traceback.format_exc())

    def generate_chart_image(self, data_json, period, time_str, observer, time):
        """Generate chart as PIL Image for PDF embedding."""
        try:
            import matplotlib
            matplotlib.use('Agg')  # Non-interactive backend
            
            data = json.loads(data_json)
            
            # Create figure
            fig, ax = plt.subplots(figsize=(10, 7))
            fig.patch.set_facecolor('#001133')
            ax.set_facecolor('#001133')
            
            # Plot stars by type
            bold_added = False
            bright_added = False
            medium_added = False
            moon_added = False
            sun_added = False
            
            for item in data:
                az = item['az']
                alt = item['alt']
                name = item['name']
                star_type = item['type']
                
                if star_type == 'bold':
                    if not bold_added:
                        ax.scatter(az, alt, s=350, marker='*', color='gold', edgecolors='orange', linewidths=2, zorder=10, label='Top 5 Stars')
                        bold_added = True
                    else:
                        ax.scatter(az, alt, s=350, marker='*', color='gold', edgecolors='orange', linewidths=2, zorder=10)
                    ax.text(az, alt+3, name, fontsize=9, color='yellow', ha='center', weight='bold')
                elif star_type == 'bright':
                    if not bright_added:
                        ax.scatter(az, alt, s=120, marker='o', color='white', edgecolors='gray', linewidths=1, zorder=5, label='Bright Reference')
                        bright_added = True
                    else:
                        ax.scatter(az, alt, s=120, marker='o', color='white', edgecolors='gray', linewidths=1, zorder=5)
                    ax.text(az, alt+2, name, fontsize=7, color='white', ha='center')
                elif star_type == 'medium':
                    if not medium_added:
                        ax.scatter(az, alt, s=60, marker='o', color='lightgray', edgecolors='darkgray', linewidths=0.5, zorder=3, label='Medium Reference')
                        medium_added = True
                    else:
                        ax.scatter(az, alt, s=60, marker='o', color='lightgray', edgecolors='darkgray', linewidths=0.5, zorder=3)
                elif star_type == 'moon':
                    if not moon_added:
                        ax.scatter(az, alt, s=200, marker='D', color='orange', edgecolors='gold', linewidths=2, zorder=8, label='Moon')
                        moon_added = True
                    else:
                        ax.scatter(az, alt, s=200, marker='D', color='orange', edgecolors='gold', linewidths=2, zorder=8)
                    ax.text(az, alt+3, name, fontsize=9, color='orange', ha='center', weight='bold')
                elif star_type == 'sun':
                    if not sun_added:
                        ax.scatter(az, alt, s=150, marker='v', color='red', edgecolors='darkred', linewidths=2, zorder=8, label='Sun (-12°)')
                        sun_added = True
                    else:
                        ax.scatter(az, alt, s=150, marker='v', color='red', edgecolors='darkred', linewidths=2, zorder=8)
                    ax.text(az, alt-4, name, fontsize=9, color='red', ha='center', weight='bold')
            
            # Draw constellation lines
            self.plot_constellation_lines(ax, observer, time, data_json)
            
            # Horizon line
            ax.axhline(y=0, color='black', linewidth=3, zorder=1)
            
            # Grid
            ax.grid(True, alpha=0.3, color='lightgray', linestyle='--')
            
            # Axis setup
            ax.set_xlim(0, 360)
            ax.set_ylim(-5, 90)
            ax.set_xticks([0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360])
            ax.set_xticklabels(['N\n0°', '30°', '60°', 'E\n90°', '120°', '150°', 'S\n180°', '210°', '240°', 'W\n270°', '300°', '330°', '360°'], fontsize=8)
            ax.set_yticks([0, 15, 30, 45, 60, 75, 90])
            
            # Labels
            ax.set_xlabel('Azimuth (0°=N, 90°=E, 180°=S, 270°=W)', fontsize=11, color='white')
            ax.set_ylabel('Altitude°', fontsize=11, color='white')
            ax.set_title(f'{period.upper()} Nautical Twilight Sky ({time_str})', 
                         fontsize=12, color='white', weight='bold', pad=15)
            
            # Tick colors
            ax.tick_params(colors='white', which='both')
            for spine in ax.spines.values():
                spine.set_color('white')
            
            # Legend
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                by_label = dict(zip(labels, handles))
                ax.legend(by_label.values(), by_label.keys(), 
                         loc='upper right', fontsize=9, 
                         facecolor='#002255', edgecolor='white', 
                         labelcolor='white', framealpha=0.8)
            
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
