# State of Play — pi-deck-tools

> **Purpose of this document:** Onboarding context for an AI assistant (or returning developer) to get up to speed quickly. Updated as the project progresses.

*Last updated: 2026-05-01*

---

## Project Goal

Build a curated set of small, single-purpose Python/tkinter tools that run on a Raspberry Pi 5 aboard a small boat. Each tool is launched directly from the **OpenCPN Launcher Plugin**, which calls `python3 /path/to/script.py` inside a VNC session. Tools must be self-contained, open quickly, and be usable on a small touchscreen or via mouse in VNC.

---

## Physical Setup

| Item | Details |
|---|---|
| Computer | Raspberry Pi 5, 4 GB RAM |
| OS | Raspberry Pi OS (Debian Bookworm) |
| Python | 3.11.2 |
| Audio DAC | HiFiBerry (ALSA — controlled via `amixer`) |
| Display | Touchscreen + remote VNC |
| Chart plotter | OpenCPN (with Launcher Plugin) |
| Data bus | Signal K server on `localhost:3000` |

---

## Key Software & APIs

### Signal K REST API
Base URL: `http://localhost:3000/signalk/v1/api/vessels/self/`

Paths used so far:
- `navigation/position/value` → `{ latitude, longitude }`
- `environment/outside/temperature/value` → float (Kelvin)
- `environment/outside/pressure/value` → float (Pascals)

### OpenCPN
- Waypoints/routes stored in SQLite: `/home/pi/.opencpn/navobj.db`
- Launcher Plugin fires a shell command per button — typically `python3 /path/to/app.py`
- Must set `DISPLAY=:0` or equivalent if launching outside the primary VNC session

---

## Current Tools (in `apps/`)

| Tool | Status | Notes |
|---|---|---|
| `maidenhead.py` | Draft in `tests/` — needs clean-up | Gets pos from SK, calculates 6-char Maidenhead grid, copies to clipboard |
| `hifiberry_volume.py` | Draft in `tests/` — needs clean-up | tkinter sliders → `amixer sset` calls, L/R sync toggle |
| `sun_moon.py` | Draft in `tests/` — needs clean-up | Celestial calculator using Skyfield + de421.bsp, optional PDF report via ReportLab |

All drafts hardcode `/data/reinstallbackups/pythonScripts/tests/` — this must be fixed to use paths relative to the script or a central config.

---

## Shared Module Plan (`shared/`)

- `signalk.py` — single `get_sk_value(path, default=None, timeout=0.5)` helper; all apps import this rather than duplicating `requests.get` logic

---

## Repository

- GitHub: *(not yet created — to be set up)*
- Remote will be used for: version history, sync between Pi and dev machine (Windows), and AI context

---

## Known Issues / TODOs

- [ ] Hardcoded paths in all three draft scripts need fixing
- [ ] No standard window size / style guide for VNC touchscreen use yet
- [ ] `sun_moon.py` imports `de421.bsp` — ephemeris download step needs to be documented
- [ ] GitHub repo not yet initialised
- [ ] `.venv` not yet set up (deferred)

---

## Useful Links for AI Context

| Topic | Link |
|---|---|
| OpenCPN Launcher Plugin | https://opencpn-manuals.github.io/main/opencpn-plugins/launcher/docs/ |
| Signal K specification | https://signalk.org/specification/1.7.0/doc/ |
| Signal K REST API | https://demo.signalk.org/signalk/v1/api/ |
| Skyfield (celestial) | https://rhodesmill.org/skyfield/ |
| Maidenhead locator | https://en.wikipedia.org/wiki/Maidenhead_Locator_System |
| HiFiBerry ALSA config | https://www.hifiberry.com/docs/software/configuring-linux-3-18-x/ |
| ReportLab user guide | https://docs.reportlab.com/reportlab/userguide/ch1_intro/ |
| OpenCPN navobj.db schema | https://github.com/OpenCPN/OpenCPN/blob/master/model/src/navobj_db.cpp |

---

## Roadmap Summary

See [ROADMAP.md](ROADMAP.md) for full detail.
