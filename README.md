# pi-deck-tools

A collection of Python/tkinter utility tools for use aboard a small boat, launched from the **OpenCPN Launcher Plugin** on a Raspberry Pi 5 running inside VNC.

## Tools

| Script | Description |
|---|---|
| `apps/maidenhead.py` | Calculates Maidenhead grid square from current Signal K position and copies to clipboard |
| `apps/hifiberry_volume.py` | L/R channel volume control for a HiFiBerry DAC via ALSA `amixer` |
| `apps/sun_moon.py` | Sun & moon rise/set/altitude/azimuth from Signal K position; generates printable PDF almanac page |

## Hardware / Software Stack

- **Hardware:** Raspberry Pi 5, 4 GB RAM, HiFiBerry DAC
- **OS:** Raspberry Pi OS (Debian Bookworm)
- **Python:** 3.11.2
- **Navigation:** OpenCPN with Launcher Plugin, Signal K server (localhost:3000)
- **Display:** VNC — all tools are tkinter GUIs, launched into the VNC X session

## Prerequisites

### On the Pi

```bash
sudo apt install python3-tk
pip install -r requirements.txt
```

Download the ephemeris file (required by `sun_moon.py`) into the `data/` directory:

```bash
cd data/
python3 -c "from skyfield.api import Loader; Loader('.')('de421.bsp')"
```

### Signal K

Signal K server must be running on `localhost:3000`. The tools use the following paths:

- `navigation/position/value` — lat/lon
- `environment/outside/temperature/value`
- `environment/outside/pressure/value`

## OpenCPN Launcher Plugin Setup

See [docs/opencpn_launcher_setup.md](docs/opencpn_launcher_setup.md) for how to wire each script into the launcher.

## Development

See [STATE_OF_PLAY.md](STATE_OF_PLAY.md) for current status and [ROADMAP.md](ROADMAP.md) for planned work.
