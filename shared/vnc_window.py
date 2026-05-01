"""
VNC/Touchscreen Window Template for pi-deck-tools

Provides a base window class with consistent styling, sizing, and close button
for all tools launched via VNC on the Pi. Ensures uniform UX across tools.
"""

import tkinter as tk


class VNCToolWindow(tk.Tk):
    """
    Base window for all pi-deck-tools VNC applications.

    Features:
    - Fixed size optimized for small touchscreen (500x400 default)
    - Dark theme suitable for sea use and low-light conditions
    - Large, prominent close button for reliable touch interaction
    - Consistent font sizes and padding
    - Centered on screen

    Usage:
        class MyTool(VNCToolWindow):
            def __init__(self):
                super().__init__(title="My Tool", width=500, height=400)
                # Create your UI in setup_ui()
                self.setup_ui()

            def setup_ui(self):
                # Add your widgets here
                label = tk.Label(self.content_frame, text="Hello", 
                               font=self.font_large)
                label.pack(pady=10)

    Attributes:
        content_frame: Main frame where app content goes (between header and footer)
        font_small: 10pt Arial (labels, hints)
        font_normal: 12pt Arial (normal text)
        font_large: 14pt Arial (important data, headers)
        font_xlarge: 18pt Arial (main display value)
        color_bg: Dark background #2c3e50
        color_fg: Light foreground text #ecf0f1
        color_accent: Red accent for buttons/warnings #e74c3c
    """

    # Theme colors
    COLOR_BG = "#2c3e50"
    COLOR_FG = "#ecf0f1"
    COLOR_ACCENT = "#e74c3c"

    def __init__(self, title="Tool", width=500, height=400):
        """
        Initialize a VNC tool window.

        Args:
            title (str): Window title shown in taskbar and title bar.
            width (int): Window width in pixels (default: 500).
            height (int): Window height in pixels (default: 400).
        """
        super().__init__()
        self.title(title)
        self.geometry(f"{width}x{height}")
        self.configure(bg=self.COLOR_BG)
        self.resizable(False, False)

        # Center window on screen
        self.eval('tk::PlaceWindow . center')

        # Define standard fonts
        self.font_small = ("Arial", 10)
        self.font_normal = ("Arial", 12)
        self.font_large = ("Arial", 14, "bold")
        self.font_xlarge = ("Arial", 18, "bold")

        # Create layout: header, content frame, close button
        self._create_header()
        self.content_frame = tk.Frame(self, bg=self.COLOR_BG)
        self.content_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._create_close_button()

    def _create_header(self):
        """Create a small header with title (optional, can be overridden)."""
        header = tk.Frame(self, bg=self.COLOR_BG, height=20)
        header.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

    def _create_close_button(self):
        """Create a large, prominent close button at the bottom."""
        footer = tk.Frame(self, bg=self.COLOR_BG)
        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

        close_btn = tk.Button(
            footer,
            text="✕ Close",
            font=("Arial", 14, "bold"),
            command=self.destroy,
            bg=self.COLOR_ACCENT,
            fg="white",
            padx=20,
            pady=10,
            relief=tk.RAISED,
            bd=2
        )
        close_btn.pack(side=tk.RIGHT)

    def show_error(self, title, message):
        """
        Display an error dialog (wrapper for tkinter.messagebox).

        Args:
            title (str): Dialog title.
            message (str): Error message.
        """
        from tkinter import messagebox
        messagebox.showerror(title, message)

    def show_info(self, title, message):
        """
        Display an info dialog.

        Args:
            title (str): Dialog title.
            message (str): Info message.
        """
        from tkinter import messagebox
        messagebox.showinfo(title, message)
