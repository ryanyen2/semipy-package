"""
CTD profile ingest + quality control with semipy, on real research-cruise casts.

A physical oceanographer, Nadia, is turning a pile of CTD casts into clean,
comparable, quality-controlled profiles. The casts come off three different
instruments, each with its own header convention and units (see
``examples/data/marine/PROVENANCE.md``). Two facts make this impossible to fully
write ahead of time:

  1. Every instrument and water mass brings quirks nobody can list in advance --
     a new vendor's column order, a drifting sensor, a fill-value sentinel, a
     salinity spike from a bubble. What counts as a clean profile depends on the
     cast in front of you.
  2. The same processing has to run unattended over a cruise's hundreds of casts,
     later over millions of archived profiles, and eventually onboard autonomous
     floats with no network -- so whatever answers a request must already exist
     as compiled code, not a model call per sample.

This is what semipy is for. An **informal** request lives *inside* the **formal**
parser and is shaped by it: the surrounding code decides the request's inputs
(the declared channels), its output vocabulary (the canonical roles), and its
return type. The four things the poster claims show up here, in order:

  [1] MATCH BY MEANING    one channel-mapping function, reused across instruments
                          by wording+args+type, not by file or line.
  [2] VERIFY BEFORE REUSE a downcast detector learned on a deep Sea-Bird cast is
                          reused on a second Sea-Bird cast, then fails on the
                          shallow Castaway cast -> a new version, old one kept.
  [3] TWO WAYS TO COMMIT  the QARTOD spike test compiles to code; the free-text
                          QC note stays interpreted (a model on every call).
  [4] EXPLAIN + CONTAIN   the spike threshold is steered through the #< note; QC
                          flags reach the shared cruise DB only through a
                          blast-radius-checked effect (a station-wide sweep is
                          refused).

Pure stdlib + semipy -- no pandas, so the generated code runs in the sandbox
with nothing extra to install.

Run from the repo root (needs OPENAI_API_KEY):

  uv run python examples/ctd_profile_qc.py

Clear this example's learned state, then rerun:

  rm -f .semiformal/runtime/ctd_profile_qc.semi.py .semiformal/*ctd_profile_qc*.portal.json
"""
from __future__ import annotations

import csv
import math
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from semipy import (
    SqliteArtifactBackend,
    configure,
    register_artifact_backend,
    semi,
    semiformal,
)
from semipy.effects import EffectResult

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "examples" / "data" / "marine"
SEABIRD_STN18 = _DATA / "ctd_seabird_km1312_stn18.cnv"
SEABIRD_2014 = _DATA / "ctd_seabird_2014_shelf.cnv"
CASTAWAY_2017 = _DATA / "ctd_castaway_2017_nsw.csv"
SESSION_SOURCE = str(Path(__file__).resolve())

# The one canonical vocabulary the whole pipeline speaks. This fixed tuple is a
# *formal* constraint on the informal channel-mapping slot below: the slot may
# only map into these roles, nothing else.
CANON_ROLES = (
    "pressure_dbar",
    "temperature_c",
    "salinity_psu",
    "conductivity",
    "oxygen",
    "fluorescence",
)


# --------------------------------------------------------------------------- #
# Formal readers: deterministic, hand-written parsers for the two header shapes.
# They produce a common CTDCast, but they do NOT decide which column is which --
# that judgement is left to the informal slot, because it differs per instrument.
# --------------------------------------------------------------------------- #
@dataclass
class CTDCast:
    instrument: str
    source: str
    lat: float | None
    lon: float | None
    time: str
    channels: list[str]  # declared short names, in column order
    units: list[str]  # unit string per channel ("" if none declared)
    columns: dict[str, list[float]]  # channel name -> column values
    n: int  # number of samples
    bad_flag: float | None  # sentinel/fill value if the format declares one
    roles: dict[str, str] = field(default_factory=dict)  # channel -> canonical role

    def profile_id(self) -> str:
        return re.sub(r"\.\w+$", "", self.source)

    def col_for(self, role: str) -> str:
        for chan, r in self.roles.items():
            if r == role:
                return chan
        raise KeyError(f"{self.source}: no channel mapped to role {role!r}")


def _dm_to_deg(text: str) -> float | None:
    """'39 16.23 N' / '150 06.34 W' -> signed decimal degrees."""
    m = re.match(r"\s*(\d+)\s+([\d.]+)\s*([NSEW])", text.strip(), re.IGNORECASE)
    if not m:
        return None
    deg = float(m.group(1)) + float(m.group(2)) / 60.0
    return -deg if m.group(3).upper() in ("S", "W") else deg


def _to_float(tok: str) -> float | None:
    try:
        return float(tok)
    except ValueError:
        return None


def read_seabird_cnv(path: Path) -> CTDCast:
    lat = lon = None
    time = ""
    bad: float | None = None
    names: dict[int, str] = {}
    units: dict[int, str] = {}
    rows: list[list[float]] = []
    for raw in Path(path).read_text(errors="replace").splitlines():
        s = raw.strip()
        if s.startswith("* NMEA Latitude"):
            lat = _dm_to_deg(s.split("=", 1)[1])
        elif s.startswith("* NMEA Longitude"):
            lon = _dm_to_deg(s.split("=", 1)[1])
        elif (s.startswith("* NMEA UTC") or s.startswith("* System UpLoad")) and not time:
            time = s.split("=", 1)[1].strip()
        elif s.startswith("# name"):
            left, right = s.split("=", 1)
            idx = int(left.split()[-1])
            names[idx] = right.split(":", 1)[0].strip()
            bracketed = re.findall(r"\[([^\]]+)\]", right)
            units[idx] = bracketed[-1] if bracketed else ""
        elif s.startswith("# bad_flag"):
            bad = float(s.split("=", 1)[1])
        elif s.startswith("*") or s.startswith("#") or not s:
            continue
        else:
            parts = raw.split()
            if parts and _to_float(parts[0]) is not None:
                rows.append([float(x) for x in parts])
    ncol = max(names) + 1
    cols = [names[i] for i in range(ncol)]
    columns = {cols[i]: [r[i] for r in rows if len(r) >= ncol] for i in range(ncol)}
    return CTDCast(
        instrument="Sea-Bird",
        source=Path(path).name,
        lat=lat,
        lon=lon,
        time=time,
        channels=cols,
        units=[units[i] for i in range(ncol)],
        columns=columns,
        n=len(next(iter(columns.values()))) if columns else 0,
        bad_flag=bad,
    )


def read_castaway_csv(path: Path) -> CTDCast:
    meta: dict[str, str] = {}
    header_idx: int | None = None
    lines = Path(path).read_text(errors="replace").splitlines()
    for i, raw in enumerate(lines):
        if raw.startswith("%"):
            kv = raw[1:].split(",", 1)
            if len(kv) == 2 and kv[0].strip():
                meta[kv[0].strip()] = kv[1].strip()
        elif raw.strip():
            header_idx = i
            break
    reader = csv.reader(lines[header_idx:])
    raw_cols = next(reader)
    channels = [re.sub(r"\s*\(.*\)", "", c).strip() for c in raw_cols]
    units = [(re.findall(r"\(([^)]+)\)", c) or [""])[-1] for c in raw_cols]
    values: list[list[float]] = [[] for _ in channels]
    for row in reader:
        if not row or not row[0].strip():
            continue
        for j in range(len(channels)):
            v = _to_float(row[j]) if j < len(row) else None
            values[j].append(v if v is not None else math.nan)
    columns = {channels[j]: values[j] for j in range(len(channels))}

    def _meta_f(key: str) -> float | None:
        return _to_float(meta.get(key, ""))

    return CTDCast(
        instrument="SonTek Castaway",
        source=Path(path).name,
        lat=_meta_f("Start latitude"),
        lon=_meta_f("Start longitude"),
        time=meta.get("Cast time (UTC)", ""),
        channels=channels,
        units=units,
        columns=columns,
        n=len(values[0]) if values else 0,
        bad_flag=None,
    )


# --------------------------------------------------------------------------- #
# [1] The informal spec, living inside the formal parser.
#
# `declared`, `units`, and `instrument` are handed in by the formal readers; the
# allowed outputs are fixed by CANON_ROLES; the return type is fixed at
# dict[str, str]. The request is written the SAME way for every instrument, so
# semipy matches it by meaning and reuses one function across all three casts.
# --------------------------------------------------------------------------- #
@semiformal
def map_channels(declared: list[str], units: list[str], instrument: str) -> dict[str, str]:
    #< intent: canonicalize declared sensor channels
    #< given: declared channel names with corresponding units
    #< given: units may be missing or scalar
    #< by: matching known labels and unit synonyms; because units disambiguate pressure and conductivity
    #< unless: unrecognized channels are omitted
    #> map each declared channel to one canonical role chosen only from
    #> pressure_dbar, temperature_c, salinity_psu, conductivity, oxygen, fluorescence.
    #> disambiguate with the units: "db"/"Decibar" is pressure_dbar; S/m, mS/cm and
    #> uS/cm are all conductivity. leave a channel out entirely if it fits no role.
    roles = ...
    #< yields: object with roles mapping channel names to canonical roles
    return roles


# --------------------------------------------------------------------------- #
# [2] A slot learned on one cast, reused on the next, re-verified every time.
#
# `pressure` is a formal local computed from the mapped role -- it is in scope
# and shapes what the slot may assume. The deep Sea-Bird casts share a downcast
# shape; the shallow 8 m Castaway cast does not, so reuse there fails
# verification and semipy opens a new version instead of returning nonsense.
# --------------------------------------------------------------------------- #
@semiformal
def downcast_indices(cast: CTDCast) -> list[int]:
    pressure = cast.columns[cast.col_for("pressure_dbar")]  # noqa: F841  (shapes the slot)
    #< intent: Return contiguous downcast pressure indices
    #< given: pressure is iterable with samples in cast order
    #< by: finding the last deepest finite pressure, then scanning backward through increasing segment
    #< unless: no finite pressure values, yields empty keep
    #> return the sample indices of the downcast only: the single contiguous run
    #> where `pressure` increases from the near-surface soak down to the deepest
    #> sample. drop the initial surface soak and the entire upcast.
    keep = ...
    #< yields: keep contains original sample indices for the downcast run
    return keep


# --------------------------------------------------------------------------- #
# [2/3] Binning. The bad_flag sentinel is a formal local; how to treat a bin with
# no good samples is a genuine fork the model has to guess -- semipy surfaces it
# as a #? line you resolve in the editor (requires decisions_enabled=True).
# --------------------------------------------------------------------------- #
@semiformal
def bin_1dbar(cast: CTDCast, keep: list[int]) -> dict[str, list[float]]:
    bad_flag = cast.bad_flag  # noqa: F841  (e.g. -9.99e-29, or None)
    #> average every mapped role's channel into 1-decibar pressure bins, over the
    #> kept indices. return {role: [per-bin mean, ...]} keyed by canonical role,
    #> including pressure_dbar as the bin centres. within a bin, average only good
    #> samples: a value equal to `bad_flag` is not a good sample.
    binned = ...
    return binned


def canonicalize(cast: CTDCast) -> CTDCast:
    """Formal driver: run the informal channel-map, then attach canonical roles."""
    cast.roles = dict(map_channels(cast.channels, cast.units, cast.instrument))
    return cast


# --------------------------------------------------------------------------- #
# [3] QARTOD QC, two ways.
# --------------------------------------------------------------------------- #
def qartod_spike_flags(temps: list[float], threshold: float) -> list[bool]:
    """Mechanical: a fixed formula. Compiles to code after it verifies once.

    The strategy note semipy writes here (a #< line) is where Nadia steers the
    threshold to the region's QARTOD table -- see the poster, step 4a.
    """
    return semi(
        f"QARTOD spike test over the temperature series {temps} with threshold "
        f"{threshold}: return a boolean list where point i is True iff "
        f"abs(t[i] - (t[i-1] + t[i+1]) / 2) > threshold. First and last points are "
        f"always False.",
        expected_type=list[bool],
    )


def qc_note(cast: CTDCast, binned: dict[str, list[float]], n_spikes: int) -> str:
    """Open-ended: a one-line human summary. Never reproduces held-out output, so
    it stays interpreted (a model every call) -- which is the correct outcome."""
    press = binned["pressure_dbar"]
    has_sal = "salinity_psu" in cast.roles.values()
    return semi(
        f"In one sentence for a QC log, say what an oceanographer should double-check "
        f"about this cast: instrument={cast.instrument}, pressure {min(press):.1f}-"
        f"{max(press):.1f} dbar, {n_spikes} temperature spike(s) flagged, "
        f"practical-salinity channel present={has_sal}.",
        interpreted=True,
    )


# --------------------------------------------------------------------------- #
# [4] Contained effects: QC flags reach the shared cruise DB only as described,
# blast-radius-checked changes -- never a direct write.
# --------------------------------------------------------------------------- #
def make_cruise_db(profiles: list[str]) -> str:
    path = str(Path(tempfile.mkdtemp(prefix="cruise_")) / "cruise_qc.db")
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE qc_flags (
            profile_id TEXT PRIMARY KEY,
            station    TEXT,
            flag       TEXT,
            reason     TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO qc_flags (profile_id, station, flag, reason) VALUES (?, ?, 'unassessed', '')",
        [(p, p.split("_")[-1]) for p in profiles],
    )
    con.commit()
    con.close()
    return path


def show_flags(db: str, label: str) -> None:
    con = sqlite3.connect(db)
    rows = con.execute("SELECT profile_id, flag, reason FROM qc_flags ORDER BY profile_id").fetchall()
    con.close()
    print(f"\n--- qc_flags: {label} ---")
    for pid, flag, reason in rows:
        print(f"    {pid:32s} {flag:12s} {reason}")


def record_flag(profile_id: str, flag: str, reason: str) -> EffectResult:
    # profile_id is the primary key, so the schema proof caps the reach at one row.
    return semi(
        f"In 'db://qc_flags', update the single row whose profile_id equals "
        f"{profile_id!r}: set flag={flag!r} and reason={reason!r}. profile_id is unique."
    )


# --------------------------------------------------------------------------- #
def process(cast: CTDCast, spike_threshold: float) -> tuple[dict[str, list[float]], int, str]:
    cast = canonicalize(cast)
    print(f"    channels -> roles: {cast.roles}")
    keep = downcast_indices(cast)
    print(f"    downcast: kept {len(keep)}/{cast.n} samples")
    binned = bin_1dbar(cast, list(keep))
    spikes = qartod_spike_flags(binned["temperature_c"], spike_threshold)
    n_spikes = sum(bool(x) for x in spikes)
    note = qc_note(cast, binned, n_spikes)
    print(f"    bins: {len(binned['pressure_dbar'])}, temperature spikes: {n_spikes}")
    print(f"    QC note (interpreted): {note}")
    return binned, n_spikes, note


def main() -> None:
    if not (os.getenv("OPENAI_API_KEY") or (_REPO / ".env").exists()):
        raise SystemExit("Set OPENAI_API_KEY (env or .env) -- semipy generates code with a model.")

    casts = [
        read_seabird_cnv(SEABIRD_STN18),   # deep Sea-Bird cast: GENERATE the slots
        read_seabird_cnv(SEABIRD_2014),    # second Sea-Bird cast: REUSE by meaning
        read_castaway_csv(CASTAWAY_2017),  # different instrument: reuse fails -> new version
    ]
    db = make_cruise_db([c.profile_id() for c in casts])
    register_artifact_backend("db", SqliteArtifactBackend(db))
    configure(
        session_source=SESSION_SOURCE,
        verbose=True,
        decisions_enabled=True,   # surface the model's silent forks as #? lines
        effects_enabled=True,
        effect_staging=True,
        effect_gate=True,
        effect_smt=True,          # blast-radius proof from the table schema
        effect_auto_apply=True,
    )

    spike_threshold = 2.0  # deg C; steer this via the #< note (see the poster, step 4a)

    print("=" * 72)
    print("Processing three real CTD casts off three instruments")
    print("=" * 72)
    results = []
    for i, cast in enumerate(casts):
        tag = "GENERATE (first)" if i == 0 else "REUSE expected (same specs, new call)"
        print(f"\n[{i + 1}] {cast.instrument} :: {cast.source}   [{tag}]")
        binned, n_spikes, note = process(cast, spike_threshold)
        results.append((cast, n_spikes, note))

    show_flags(db, "initial")

    # [4] Contained effect: flag exactly one cast, checked before it runs.
    print("\n" + "=" * 72)
    print("Writing QC flags to the shared cruise DB (contained, blast-radius-checked)")
    print("=" * 72)
    for cast, n_spikes, note in results:
        flag = "suspect" if n_spikes else "good"
        res = record_flag(cast.profile_id(), flag, note[:120])
        print(f"    {cast.profile_id()}: applied={res.applied} event={res.event_id[:8]} ({flag})")
    show_flags(db, "after per-cast flags")

    # A station-wide sweep matches many rows -> refused by the gate, like the
    # poster's lighting cue that would have fired a whole scene.
    print("\n--- safety: a table-wide sweep must be refused, not applied ---")
    try:
        semi("In 'db://qc_flags', set flag='bad' on every row. There is no id filter.")
        print("    (returned without applying a sweep)")
    except Exception as e:  # noqa: BLE001 -- demo: show the refusal reason
        print(f"    blocked as expected: {type(e).__name__}: {str(e).splitlines()[0][:100]}")
    show_flags(db, "after the sweep attempt")
    print("\nDone. Inspect versions and #< / #? surfaces in the VS Code slot tree.")


if __name__ == "__main__":
    main()
