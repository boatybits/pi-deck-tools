# OpenCPN Launcher Plugin Setup

## Overview

The [OpenCPN Launcher Plugin](https://opencpn-manuals.github.io/main/opencpn-plugins/launcher/docs/) lets you add custom buttons to OpenCPN's toolbar that run shell commands. We use it to launch Python/tkinter tools inside the VNC session.

## Prerequisites

- OpenCPN installed with the Launcher Plugin enabled
- Python 3 with `python3-tk` installed (`sudo apt install python3-tk`)
- VNC server running (tools open windows in the VNC display)

## Adding a Tool

1. In OpenCPN, go to **Options → Plugins → Launcher → Preferences**
2. Add a new entry:
   - **Label:** Short name shown on the toolbar button (e.g. `Grid Sq`)
   - **Command:** The shell command to run (see below)
   - **Icon:** Optional — point at a 32×32 PNG

### Command Template

```bash
DISPLAY=:0 python3 /home/pi/pi-deck-tools/apps/maidenhead.py
```

> `DISPLAY=:0` ensures the tkinter window opens in the VNC X session rather than trying to open on a non-existent display. Adjust `:0` to match your VNC display number (check with `echo $DISPLAY` inside VNC terminal).

## Tool Commands

| Tool | Command |
|---|---|
| Maidenhead Grid | `DISPLAY=:0 python3 /home/pi/pi-deck-tools/apps/maidenhead.py` |
| HiFiBerry Volume | `DISPLAY=:0 python3 /home/pi/pi-deck-tools/apps/hifiberry_volume.py` |
| Sun/Moon | `DISPLAY=:0 python3 /home/pi/pi-deck-tools/apps/sun_moon.py` |

## Notes

- Tools exit cleanly when their window is closed — they do not run in the background
- If a tool fails silently, run the command manually in a VNC terminal to see error output
- The Launcher Plugin supports an optional working directory setting — leave blank; tools resolve paths relative to their own location using `__file__`
