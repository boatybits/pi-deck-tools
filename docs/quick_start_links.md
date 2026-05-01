# Quick Start Notes and Links

## Short Notes

- `DISPLAY=:0` tells GUI apps (tkinter) which X display to open on.
- Use `:0` for the main Raspberry Pi desktop/VNC session in most setups.
- If launcher buttons do nothing, test in terminal with `echo $DISPLAY`.
- Always run apps through the project venv on the Pi: `.venv/bin/python`.

## Quick Commands (Pi)

```bash
# Create/update Pi-native virtual environment
bash setup_pi_venv.sh

# Launch apps through venv wrapper
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh maidenhead
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh hifiberry_volume
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh sun_moon

# Check active display in your current session
echo $DISPLAY
```

## OpenCPN Launcher Commands

```bash
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh maidenhead
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh hifiberry_volume
DISPLAY=:0 /home/pi/pi-deck-tools/launch_pi_app.sh sun_moon
```

## Key Links

- OpenCPN Launcher Plugin docs:
  - https://opencpn-manuals.github.io/main/opencpn-plugins/launcher/docs/
- Signal K specification:
  - https://signalk.org/specification/1.7.0/doc/
- Signal K API explorer example:
  - https://demo.signalk.org/signalk/v1/api/
- Skyfield docs:
  - https://rhodesmill.org/skyfield/
- HiFiBerry Linux setup:
  - https://www.hifiberry.com/docs/software/configuring-linux-3-18-x/
- ReportLab user guide:
  - https://docs.reportlab.com/reportlab/userguide/ch1_intro/

## Related Project Docs

- `docs/opencpn_launcher_setup.md`
- `docs/signalk_api_notes.md`
- `STATE_OF_PLAY.md`
