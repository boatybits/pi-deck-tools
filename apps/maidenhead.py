#!/usr/bin/env python3
"""
Maidenhead Grid Square Tool

Fetches current position from Signal K server and calculates the 
6-character Maidenhead locator (grid square), displaying it in a tkinter
window and copying to clipboard.

Usage:
    DISPLAY=:0 python3 maidenhead.py    (in VNC, launched from OpenCPN Launcher)
    python3 maidenhead.py               (dev on any machine with Signal K server)
"""

import sys
import tkinter as tk
from pathlib import Path

# Import shared Signal K helper
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.signalk import get_sk_value


def to_maidenhead(lat, lon):
    """Calculates the 6-character Maidenhead locator from lat/lon."""
    lon += 180
    lat += 90
    field = chr(ord('A') + int(lon // 20)) + chr(ord('A') + int(lat // 10))
    square = str(int((lon % 20) // 2)) + str(int((lat % 10) // 1))
    sub_lon = chr(ord('a') + int(((lon % 2) * 60) // 5))
    sub_lat = chr(ord('a') + int(((lat % 1) * 60) // 2.5))
    return f"{field}{square}{sub_lon}{sub_lat}"


def get_current_position():
    """Fetches navigation.position from local Signal K server."""
    data = get_sk_value("navigation/position/value")
    if data and 'latitude' in data and 'longitude' in data:
        return data['latitude'], data['longitude']
    return None, None


def copy_and_exit(text, root):
    """Copies text to clipboard and closes the app."""
    root.clipboard_clear()
    root.clipboard_append(text)
    # update() ensures the OS clipboard manager receives data before exit
    root.update()
    root.destroy()


def run_app():
    lat, lon = get_current_position()

    # If no fix, exit silently or print to stderr
    if lat is None or lon is None:
        print("Error: Could not retrieve position from Signal K.", file=sys.stderr)
        sys.exit(1)

    grid = to_maidenhead(lat, lon)

    # Setup the GUI
    root = tk.Tk()
    root.title("Grid Square")
    root.geometry("250x100")
    root.eval('tk::PlaceWindow . center')

    # Display the locator in large font
    label = tk.Label(root, text=grid, font=("Arial", 20, "bold"), pady=10)
    label.pack()

    # Button to copy and exit
    btn = tk.Button(
        root, text="Copy & Exit", width=15,
        command=lambda: copy_and_exit(grid, root)
    )
    btn.pack(pady=5)

    root.mainloop()


if __name__ == "__main__":
    run_app()
