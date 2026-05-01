# Roadmap — pi-deck-tools

## Phase 1 — Foundation *(current)*

- [x] Define folder structure
- [x] Create README, STATE_OF_PLAY, ROADMAP
- [ ] Initialise GitHub repository
- [ ] Set up `.venv` on Pi
- [ ] Copy and clean up 3 existing draft tools into `apps/`
  - Fix hardcoded paths
  - Use `shared/signalk.py` for Signal K calls
- [ ] Create `shared/signalk.py`
- [ ] Document OpenCPN Launcher Plugin setup (`docs/opencpn_launcher_setup.md`)
- [ ] Test all 3 tools launching from OpenCPN on Pi

## Phase 2 — Polish & Standards

- [ ] Define a standard window template for VNC/touchscreen use
  - Consistent sizing, font size readable at arm's length, large close/quit button
- [ ] Add per-tool `--test` flag or fallback mode for when Signal K is not available (dev on desktop)
- [ ] Complete `docs/signalk_api_notes.md`
- [ ] Add `docs/opencpn_launcher_setup.md` screenshots

## Phase 3 — New Tools (ideas)

These are candidates — priority TBD:

| Tool | Description |
|---|---|
| `anchor_watch.py` | Monitor distance from drop point, alert if dragging |
| `tide_times.py` | Fetch tide predictions for current position (Admiralty / open API) |
| `ais_summary.py` | Show nearby AIS targets from Signal K in a simple list |
| `barograph.py` | Plot pressure history from Signal K over last N hours |
| `nmea_monitor.py` | Raw NMEA sentence display for diagnostics |
| `waypoint_list.py` | Read and display active route waypoints from OpenCPN navobj.db |
| `vhf_channels.py` | Quick reference for VHF marine channels with search |

## Phase 4 — Launcher Menu (stretch goal)

- A single "hub" app (`apps/launcher_menu.py`) that presents all tools in a grid of large buttons — user launches just this one script from OpenCPN, then picks the tool. Avoids cluttering the OpenCPN launcher with many entries.
