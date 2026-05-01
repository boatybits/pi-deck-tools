#!/usr/bin/env python3
"""
HiFiBerry Volume Control

Control left/right channel volume on a HiFiBerry DAC via ALSA amixer.

Displays two sliders (0–207) with optional L/R sync. Changes trigger
background amixer commands.

Usage:
    DISPLAY=:0 python3 hifiberry_volume.py    (in VNC, launched from OpenCPN Launcher)
    python3 hifiberry_volume.py               (on Pi with ALSA/HiFiBerry DAC)
"""

import tkinter as tk
from tkinter import ttk
import subprocess
import threading


class VolumeControl:
    """HiFiBerry volume control UI."""

    def __init__(self, root):
        self.root = root
        self.root.title("HiFiBerry Volume Control")
        self.root.geometry("400x180")
        self.root.resizable(False, False)

        # Variables
        self.left_vol = tk.IntVar(value=150)
        self.right_vol = tk.IntVar(value=150)
        self.sync_enabled = tk.BooleanVar(value=True)
        self.updating = False  # Prevent recursive updates during sync

        # Left Channel
        ttk.Label(root, text="Left Channel:").grid(
            row=0, column=0, padx=10, pady=10, sticky='w'
        )
        self.left_slider = ttk.Scale(
            root,
            from_=0,
            to=207,
            orient='horizontal',
            length=200,
            variable=self.left_vol,
            command=self.on_left_change
        )
        self.left_slider.grid(row=0, column=1, padx=10, pady=10)
        self.left_label = ttk.Label(root, text="150")
        self.left_label.grid(row=0, column=2, padx=5)

        # Right Channel
        ttk.Label(root, text="Right Channel:").grid(
            row=1, column=0, padx=10, pady=10, sticky='w'
        )
        self.right_slider = ttk.Scale(
            root,
            from_=0,
            to=207,
            orient='horizontal',
            length=200,
            variable=self.right_vol,
            command=self.on_right_change
        )
        self.right_slider.grid(row=1, column=1, padx=10, pady=10)
        self.right_label = ttk.Label(root, text="150")
        self.right_label.grid(row=1, column=2, padx=5)

        # Sync Checkbox
        self.sync_check = ttk.Checkbutton(
            root,
            text="Sync L/R Channels",
            variable=self.sync_enabled,
            command=self.on_sync_toggle
        )
        self.sync_check.grid(row=2, column=0, columnspan=2, padx=10, pady=15, sticky='w')

        # Status Label
        self.status_label = ttk.Label(root, text="Ready", foreground="green")
        self.status_label.grid(row=3, column=0, columnspan=3, padx=10, pady=5)

    def on_left_change(self, value):
        """Handle left slider change."""
        if self.updating:
            return

        left = int(float(value))
        self.left_label.config(text=str(left))

        if self.sync_enabled.get():
            self.updating = True
            self.right_vol.set(left)
            self.right_label.config(text=str(left))
            self.updating = False

        self.update_volume()

    def on_right_change(self, value):
        """Handle right slider change."""
        if self.updating:
            return

        right = int(float(value))
        self.right_label.config(text=str(right))

        if self.sync_enabled.get():
            self.updating = True
            self.left_vol.set(right)
            self.left_label.config(text=str(right))
            self.updating = False

        self.update_volume()

    def on_sync_toggle(self):
        """Handle sync checkbox toggle."""
        if self.sync_enabled.get():
            # Sync right to left when enabling
            self.updating = True
            left = self.left_vol.get()
            self.right_vol.set(left)
            self.right_label.config(text=str(left))
            self.updating = False
            self.update_volume()

    def update_volume(self):
        """Execute amixer command in background thread to avoid blocking UI."""
        left = self.left_vol.get()
        right = self.right_vol.get()

        thread = threading.Thread(target=self.run_amixer, args=(left, right))
        thread.daemon = True
        thread.start()

    def run_amixer(self, left, right):
        """Execute the amixer command."""
        try:
            cmd = ['amixer', '-c', 'sndrpihifiberry', 'sset', 'Digital', f'{left},{right}']
            subprocess.run(cmd, check=True, capture_output=True, timeout=2)
            self.status_label.config(
                text=f"Volume: L={left} R={right}", foreground="green"
            )
        except subprocess.TimeoutExpired:
            self.status_label.config(text="Error: Command timeout", foreground="red")
        except subprocess.CalledProcessError as e:
            self.status_label.config(text=f"Error: {e.returncode}", foreground="red")
        except FileNotFoundError:
            self.status_label.config(text="Error: amixer not found", foreground="red")


if __name__ == "__main__":
    root = tk.Tk()
    app = VolumeControl(root)
    root.mainloop()
