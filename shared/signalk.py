"""
Shared Signal K REST API client for pi-deck-tools.

All tools use this helper to fetch data from the local Signal K server
at http://localhost:3000 instead of duplicating the request logic.
"""

import requests


def get_sk_value(path, default=None, timeout=0.5):
    """
    Fetch a value from Signal K REST API.
    
    Args:
        path (str): API path relative to /signalk/v1/api/vessels/self/
                   (e.g., "navigation/position/value")
        default: Value to return if fetch fails (default: None)
        timeout (float): Request timeout in seconds (default: 0.5)
    
    Returns:
        The JSON value, or the default if the request failed.
    
    Examples:
        >>> pos = get_sk_value("navigation/position/value")
        >>> # Returns: {"latitude": ..., "longitude": ...} or None
        >>> temp = get_sk_value("environment/outside/temperature/value")
        >>> # Returns: 284.15 (Kelvin) or None
    """
    url = f"http://localhost:3000/signalk/v1/api/vessels/self/{path}"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except Exception:
        return default
