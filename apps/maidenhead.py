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
from pathlib import Path

# Import shared modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.signalk import get_sk_value
from shared.vnc_window import VNCToolWindow


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


class MaidenheadTool(VNCToolWindow):
    """Maidenhead grid square calculator."""

    def __init__(self):
        super().__init__(title="Grid Square", width=350, height=200)
        self.setup_ui()

    def setup_ui(self):
        """Build the UI."""
        lat, lon = get_current_position()

        # If no fix, show error and exit
        if lat is None or lon is None:
            self.show_error("No Position", "Could not retrieve position from Signal K.")
            self.destroy()
            return

        grid = to_maidenhead(lat, lon)

        # Display the locator in large font
        label = tk.Label(
            self.content_frame,
            text=grid,
            font=self.font_xlarge,
            fg=self.COLOR_FG,
            bg=self.COLOR_BG
        )
        label.pack(pady=20)

        # Button to copy and exit
        copy_btn = tk.Button(
            self.content_frame,
            text="Copy & Exit",
            font=self.font_large,
            command=lambda: copy_and_exit(grid, self),
            bg="#27ae60",
            fg="white",
            padx=20,
            pady=10
        )
        copy_btn.pack(pady=10)


if __name__ == "__main__":
    import tkinter as tk
    app = MaidenheadTool()
    app.mainloop()
