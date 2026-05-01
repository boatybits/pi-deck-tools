# Signal K API Notes

## Server

Local Signal K server running on the Pi at `http://localhost:3000`.

Full API explorer available at `http://localhost:3000/signalk/v1/explorer` when on the same network.

## REST API Pattern

```
GET http://localhost:3000/signalk/v1/api/vessels/self/<path>/value
```

Returns a bare JSON value (not wrapped in a dict for the `/value` endpoint).

## Paths Used in pi-deck-tools

| Path | Unit | Example value | Used by |
|---|---|---|---|
| `navigation/position/value` | — | `{"latitude": 51.5, "longitude": -4.2}` | maidenhead, sun_moon |
| `environment/outside/temperature/value` | Kelvin | `284.15` | sun_moon |
| `environment/outside/pressure/value` | Pascals | `101325.0` | sun_moon |

## Converting Units

- Temperature: `°C = K − 273.15`
- Pressure: `hPa = Pa / 100`

## Timeout

All requests use a short timeout (default 0.5 s). Signal K is local so should respond in < 100 ms. Tools fall back gracefully if Signal K is unavailable.

## Shared Helper

`shared/signalk.py` provides:

```python
get_sk_value(path, default=None, timeout=0.5)
```

Example:
```python
from shared.signalk import get_sk_value

pos = get_sk_value("navigation/position/value")
# Returns {"latitude": ..., "longitude": ...} or None
```
