"""
Pi-specific OpenCPN database helper.

This module is intentionally Raspberry Pi focused and assumes OpenCPN's
default database location:
    /home/pi/.opencpn/navobj.db

It provides read-only access helpers plus route and route-waypoint extraction.
Schema differences across OpenCPN versions are handled with table/column
heuristics where possible.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any

PI_OPENCPN_DB_PATH = "/home/pi/.opencpn/navobj.db"


class OpenCPNDbError(RuntimeError):
    """Raised when OpenCPN database operations fail."""


@dataclass
class RouteRecord:
    """Basic route record returned from route lookups."""

    table: str
    route_key_column: str
    route_key_value: Any
    name: str


@dataclass
class WaypointRecord:
    """A waypoint position entry extracted for a route."""

    sequence: int | None
    name: str | None
    lat: float
    lon: float


def _ensure_pi_db_path(db_path: str) -> None:
    if db_path != PI_OPENCPN_DB_PATH:
        raise OpenCPNDbError(
            f"This module is Pi-specific. Expected db_path='{PI_OPENCPN_DB_PATH}', got '{db_path}'."
        )


def _connect_readonly(db_path: str = PI_OPENCPN_DB_PATH) -> sqlite3.Connection:
    _ensure_pi_db_path(db_path)

    if not os.path.exists(db_path):
        raise OpenCPNDbError(
            f"OpenCPN DB not found at '{db_path}'. "
            "Ensure this runs on the Pi with OpenCPN installed."
        )

    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise OpenCPNDbError(f"Failed to open OpenCPN DB: {exc}") from exc


def _connect_readwrite(db_path: str = PI_OPENCPN_DB_PATH) -> sqlite3.Connection:
    _ensure_pi_db_path(db_path)

    if not os.path.exists(db_path):
        raise OpenCPNDbError(
            f"OpenCPN DB not found at '{db_path}'. "
            "Ensure this runs on the Pi with OpenCPN installed."
        )

    try:
        return sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        raise OpenCPNDbError(f"Failed to open OpenCPN DB read/write: {exc}") from exc


def list_tables(db_path: str = PI_OPENCPN_DB_PATH) -> list[str]:
    """Return all table names in the OpenCPN DB."""
    con = _connect_readonly(db_path)
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return sorted([r[0] for r in rows])
    finally:
        con.close()


def table_columns(table: str, db_path: str = PI_OPENCPN_DB_PATH) -> list[str]:
    """Return column names for a table."""
    con = _connect_readonly(db_path)
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]
    finally:
        con.close()


def _table_columns_map(con: sqlite3.Connection) -> dict[str, list[str]]:
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    output: dict[str, list[str]] = {}
    for name in names:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({name})").fetchall()]
        output[name] = cols
    return output


def _choose_route_table(columns_map: dict[str, list[str]]) -> str | None:
    preferred = ["route", "routes"]
    for t in preferred:
        if t in columns_map:
            cols = {c.lower() for c in columns_map[t]}
            if "name" in cols and ("guid" in cols or "id" in cols):
                return t

    for table, cols_raw in columns_map.items():
        t = table.lower()
        if "route" in t and "point" not in t:
            cols = {c.lower() for c in cols_raw}
            if "name" in cols and ("guid" in cols or "id" in cols):
                return table
    return None


def _choose_route_key_column(route_cols: list[str]) -> str:
    lc = {c.lower(): c for c in route_cols}
    for key in ("guid", "id"):
        if key in lc:
            return lc[key]
    raise OpenCPNDbError("Route table has no usable key column (guid/id).")


def _choose_link_tables(columns_map: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for table in columns_map:
        t = table.lower()
        if t in {"routepoint", "routepoints", "route_points", "route_point"}:
            out.append(table)
            continue
        if "route" in t and "point" in t:
            out.append(table)
    return out


def _pick_column_by_hints(columns: list[str], hints: tuple[str, ...]) -> str | None:
    lower_to_orig = {c.lower(): c for c in columns}

    for hint in hints:
        if hint in lower_to_orig:
            return lower_to_orig[hint]

    for c in columns:
        l = c.lower()
        for hint in hints:
            if hint in l:
                return c
    return None


def _choose_waypoint_table(columns_map: dict[str, list[str]]) -> str | None:
    preferred = ["waypoint", "waypoints"]
    for t in preferred:
        if t in columns_map:
            cols = {c.lower() for c in columns_map[t]}
            if "lat" in cols and "lon" in cols:
                return t

    for table, cols_raw in columns_map.items():
        t = table.lower()
        if "waypoint" in t:
            cols = {c.lower() for c in cols_raw}
            if "lat" in cols and "lon" in cols:
                return table

    for table, cols_raw in columns_map.items():
        cols = {c.lower() for c in cols_raw}
        if "lat" in cols and "lon" in cols and ("name" in cols or "guid" in cols):
            return table

    return None


def list_routes(db_path: str = PI_OPENCPN_DB_PATH) -> list[RouteRecord]:
    """
    Return available routes with route key and name.

    Uses heuristic route table detection to tolerate schema differences.
    """
    con = _connect_readonly(db_path)
    try:
        columns_map = _table_columns_map(con)
        route_table = _choose_route_table(columns_map)
        if not route_table:
            return []

        route_cols = columns_map[route_table]
        key_col = _choose_route_key_column(route_cols)

        rows = con.execute(
            f"SELECT {key_col}, name FROM {route_table} ORDER BY name COLLATE NOCASE"
        ).fetchall()

        out: list[RouteRecord] = []
        for r in rows:
            out.append(
                RouteRecord(
                    table=route_table,
                    route_key_column=key_col,
                    route_key_value=r[0],
                    name=r[1] if r[1] is not None else "",
                )
            )
        return out
    except sqlite3.Error as exc:
        raise OpenCPNDbError(f"Failed listing routes: {exc}") from exc
    finally:
        con.close()


def _load_route_by_name(con: sqlite3.Connection, columns_map: dict[str, list[str]], route_name: str) -> RouteRecord:
    route_table = _choose_route_table(columns_map)
    if not route_table:
        raise OpenCPNDbError("Could not detect a route table in navobj.db.")

    route_cols = columns_map[route_table]
    key_col = _choose_route_key_column(route_cols)

    try:
        row = con.execute(
            f"SELECT {key_col}, name FROM {route_table} WHERE UPPER(name)=UPPER(?) LIMIT 1",
            (route_name,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise OpenCPNDbError(f"Failed querying route '{route_name}': {exc}") from exc

    if not row:
        raise OpenCPNDbError(f"Route '{route_name}' not found.")

    return RouteRecord(
        table=route_table,
        route_key_column=key_col,
        route_key_value=row[0],
        name=row[1] if row[1] is not None else route_name,
    )


def route_waypoints(route_name: str, db_path: str = PI_OPENCPN_DB_PATH) -> list[WaypointRecord]:
    """
    Extract waypoints for a named route.

    Strategy:
    1) Resolve the route in the route table.
    2) Find route-point link tables.
    3) Use direct lat/lon from link table when available, or join to waypoint table.

    Returns waypoints sorted by sequence when sequence is available.
    """
    con = _connect_readonly(db_path)
    try:
        columns_map = _table_columns_map(con)
        route = _load_route_by_name(con, columns_map, route_name)

        link_tables = _choose_link_tables(columns_map)
        if not link_tables:
            raise OpenCPNDbError("Could not detect route-point link table.")

        waypoint_table = _choose_waypoint_table(columns_map)

        for link_table in link_tables:
            cols = columns_map[link_table]

            route_ref_col = _pick_column_by_hints(
                cols,
                (
                    "route_guid",
                    "route_id",
                    "routeid",
                    "route_uuid",
                    "rte_id",
                    "route",
                ),
            )
            if not route_ref_col:
                continue

            seq_col = _pick_column_by_hints(cols, ("sequence", "seq", "order", "position", "idx", "leg"))
            name_col = _pick_column_by_hints(cols, ("name", "wp_name", "waypoint_name"))
            lat_col = _pick_column_by_hints(cols, ("lat", "latitude"))
            lon_col = _pick_column_by_hints(cols, ("lon", "lng", "longitude"))

            order_sql = f"ORDER BY {seq_col}" if seq_col else ""

            # Case A: link table stores waypoint positions directly.
            if lat_col and lon_col:
                select_fields = []
                select_fields.append(seq_col if seq_col else "NULL")
                select_fields.append(name_col if name_col else "NULL")
                select_fields.append(lat_col)
                select_fields.append(lon_col)
                sql = (
                    f"SELECT {', '.join(select_fields)} FROM {link_table} "
                    f"WHERE {route_ref_col} = ? {order_sql}"
                )
                rows = con.execute(sql, (route.route_key_value,)).fetchall()
                if rows:
                    return [
                        WaypointRecord(
                            sequence=int(r[0]) if r[0] is not None else None,
                            name=r[1],
                            lat=float(r[2]),
                            lon=float(r[3]),
                        )
                        for r in rows
                    ]

            # Case B: link table references waypoint table.
            if waypoint_table:
                wp_cols = columns_map[waypoint_table]
                wp_key_col = _pick_column_by_hints(wp_cols, ("guid", "id", "wp_guid", "waypoint_guid"))
                wp_name_col = _pick_column_by_hints(wp_cols, ("name", "wp_name", "waypoint_name"))
                wp_lat_col = _pick_column_by_hints(wp_cols, ("lat", "latitude"))
                wp_lon_col = _pick_column_by_hints(wp_cols, ("lon", "lng", "longitude"))

                link_wp_ref_col = _pick_column_by_hints(
                    cols,
                    (
                        "waypoint_guid",
                        "waypoint_id",
                        "wp_guid",
                        "wp_id",
                        "point_guid",
                        "point_id",
                    ),
                )

                if not (wp_key_col and wp_lat_col and wp_lon_col and link_wp_ref_col):
                    continue

                select_seq = f"l.{seq_col}" if seq_col else "NULL"
                select_name = f"w.{wp_name_col}" if wp_name_col else "NULL"

                sql = (
                    f"SELECT {select_seq}, {select_name}, w.{wp_lat_col}, w.{wp_lon_col} "
                    f"FROM {link_table} l "
                    f"JOIN {waypoint_table} w ON l.{link_wp_ref_col} = w.{wp_key_col} "
                    f"WHERE l.{route_ref_col} = ? "
                    f"{order_sql.replace(seq_col, f'l.{seq_col}') if seq_col else ''}"
                )

                rows = con.execute(sql, (route.route_key_value,)).fetchall()
                if rows:
                    return [
                        WaypointRecord(
                            sequence=int(r[0]) if r[0] is not None else None,
                            name=r[1],
                            lat=float(r[2]),
                            lon=float(r[3]),
                        )
                        for r in rows
                    ]

        raise OpenCPNDbError(
            "Could not extract route waypoints with current schema heuristics. "
            "Inspect navobj.db table names/columns and extend hints in shared/opencpn_db.py."
        )

    except sqlite3.Error as exc:
        raise OpenCPNDbError(f"Failed extracting waypoints for route '{route_name}': {exc}") from exc
    finally:
        con.close()


def route_with_waypoints(route_name: str, db_path: str = PI_OPENCPN_DB_PATH) -> dict[str, Any]:
    """
    Return route metadata + waypoint list in one structure.

    Example output:
    {
      "route_name": "Weekend Run",
      "waypoint_count": 12,
      "waypoints": [
        {"sequence": 1, "name": "A", "lat": 50.1, "lon": -4.2},
        ...
      ]
    }
    """
    wps = route_waypoints(route_name, db_path)
    return {
        "route_name": route_name,
        "waypoint_count": len(wps),
        "waypoints": [
            {"sequence": w.sequence, "name": w.name, "lat": w.lat, "lon": w.lon}
            for w in wps
        ],
    }


def _table_info_rows(con: sqlite3.Connection, table: str) -> list[sqlite3.Row | tuple]:
    return con.execute(f"PRAGMA table_info({table})").fetchall()


def _table_required_columns(con: sqlite3.Connection, table: str) -> set[str]:
    required: set[str] = set()
    for row in _table_info_rows(con, table):
        # PRAGMA table_info columns:
        # 0 cid, 1 name, 2 type, 3 notnull, 4 dflt_value, 5 pk
        name = row[1]
        notnull = int(row[3])
        default = row[4]
        pk = int(row[5])
        if pk:
            continue
        if notnull and default is None:
            required.add(name)
    return required


def _table_columns_set(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in _table_info_rows(con, table)}


def _insert_row_flexible(con: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
    cols = _table_columns_set(con, table)
    payload = {k: v for k, v in values.items() if k in cols}
    required = _table_required_columns(con, table)
    missing_required = sorted(c for c in required if c not in payload)
    if missing_required:
        raise OpenCPNDbError(
            f"Cannot insert into '{table}'. Missing required columns: {', '.join(missing_required)}"
        )

    if not payload:
        raise OpenCPNDbError(f"Cannot insert into '{table}'. No matching columns for payload.")

    names = list(payload.keys())
    placeholders = ", ".join("?" for _ in names)
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
    cur = con.execute(sql, [payload[n] for n in names])
    return int(cur.lastrowid)


def _maybe_set_guid(values: dict[str, Any], columns: set[str]) -> None:
    guid_col = _pick_column_by_hints(list(columns), ("guid", "uuid"))
    if guid_col and guid_col not in values:
        values[guid_col] = str(uuid.uuid4())


def create_planner_route(
    source_route_name: str,
    timeline_points: list[dict[str, Any]],
    db_path: str = PI_OPENCPN_DB_PATH,
) -> dict[str, Any]:
    """
    Create (or replace) '<source_route_name>_planner' route in OpenCPN navobj.db.

    timeline_points items must include:
      - name (str)
      - lat (float)
      - lon (float)
    """
    raise OpenCPNDbError(
        "Direct navobj.db writes are temporarily disabled for safety. "
        "Use OpenCPN route import (GPX) workflow until a schema-safe writer is completed."
    )
