"""
engine.data_provenance — where a bar came from, and whether it is real.
=======================================================================

ZERO used to hand synthetic random-walk OHLCV to the AITE exam whenever a
vendor returned too few bars, with only a log line to say so. A bot that
"passed" on padded data was then admitted to the paper book identically to a
bot that passed on NSE history, and nothing in the stored state recorded the
difference.

This module is the single place that answers *"is this price series real?"*.
It is deliberately conservative:

* provenance is **explicitly tagged** by whoever loads the data
  (``df.attrs["provenance"]``) - it is never guessed from the numbers, because
  a synthetic random walk is statistically indistinguishable from a quiet tape;
* where tagging is absent, structural evidence is used to *downgrade* a frame
  (missing/placeholder timestamps, duplicate bars, impossible OHLC), never to
  upgrade it;
* anything unresolved is reported as ``unverified``, and callers that gate
  promotion treat ``unverified`` exactly like ``synthetic``.

Pure standard library. No network, no pandas at import time.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "REAL",
    "SYNTHETIC",
    "MIXED",
    "UNVERIFIED",
    "Provenance",
    "classify_frame",
    "tag_frame",
    "frame_provenance",
    "is_promotable",
    "fingerprint",
    "audit_stored_state",
    "write_manifest",
    "read_manifest",
]

REAL = "real"
SYNTHETIC = "synthetic"
MIXED = "mixed"            # real tail + synthetic prefix: the old padding path
UNVERIFIED = "unverified"  # no evidence either way - treated as synthetic

# Timestamp placeholders produced when a frame had no usable index.
_PLACEHOLDER_PREFIXES = ("bar_", "Bar_", "idx_")


@dataclass
class Provenance:
    """Everything needed to defend (or reject) a backtest."""

    source: str = UNVERIFIED
    vendor: str = ""
    symbol: str = ""
    rows: int = 0
    real_rows: int = 0
    synthetic_rows: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    has_datetime_index: bool = False
    has_placeholder_timestamps: bool = False
    duplicate_bars: int = 0
    ohlc_violations: int = 0
    zero_volume_rows: int = 0
    fingerprint: str = ""
    captured_at: float = field(default_factory=time.time)
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    # ---- derived -----------------------------------------------------------
    @property
    def is_real(self) -> bool:
        return self.source == REAL

    @property
    def is_synthetic(self) -> bool:
        return self.source in (SYNTHETIC, MIXED)

    @property
    def is_promotable(self) -> bool:
        """Only fully real, clean series may gate a bot into the book."""
        return self.is_real and self.ohlc_violations == 0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Provenance":
        if not d:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def summary(self) -> str:
        bits = [self.source or UNVERIFIED]
        if self.vendor:
            bits.append(self.vendor)
        if self.rows:
            bits.append(f"{self.rows} bars")
        if self.synthetic_rows:
            bits.append(f"{self.synthetic_rows} synthetic")
        if self.has_placeholder_timestamps:
            bits.append("no timestamps")
        return " | ".join(bits)


def fingerprint(values: Iterable[float], n: int = 4096) -> str:
    """Stable hash of a price path (used to prove which frame was examined)."""
    h = hashlib.sha256()
    count = 0
    for v in values:
        try:
            h.update(repr(round(float(v), 6)).encode("ascii", "ignore"))
        except (TypeError, ValueError):
            continue
        count += 1
        if count >= n:
            break
    return h.hexdigest()[:32]


def _ts_strings(df) -> Tuple[List[str], bool]:
    """Best-effort timestamp extraction without importing pandas."""
    has_dt_index = type(df.index).__name__ in ("DatetimeIndex", "PeriodIndex")
    for col in ("timestamps", "timestamp", "datetime", "date", "Date"):
        try:
            if col in df.columns:
                return [str(x) for x in df[col].tolist()], has_dt_index
        except Exception:
            continue
    if has_dt_index:
        try:
            return [str(x) for x in df.index.tolist()], True
        except Exception:
            return [], True
    return [], False


def _placeholder(times: List[str]) -> bool:
    if not times:
        return False
    sample = times[: min(50, len(times))]
    return all(str(t).startswith(_PLACEHOLDER_PREFIXES) for t in sample)


def tag_frame(df, *, source: str, vendor: str = "", symbol: str = "",
              real_rows: Optional[int] = None, note: str = ""):
    """Attach (or replace) explicit provenance on a DataFrame. Returns ``df``."""
    if df is None:
        return df
    prov = classify_frame(
        df, source=source, vendor=vendor, symbol=symbol,
        real_rows=real_rows, note=note,
    )
    try:
        df.attrs["provenance"] = prov.as_dict()
    except Exception:
        pass
    return df


def frame_provenance(df) -> Provenance:
    """Read tagged provenance, or classify what is there. Never raises."""
    if df is None:
        return Provenance(source=UNVERIFIED, note="no frame")
    try:
        tagged = getattr(df, "attrs", {}).get("provenance")
    except Exception:
        tagged = None
    if isinstance(tagged, Provenance):
        return tagged
    if isinstance(tagged, dict) and tagged:
        return Provenance.from_dict(tagged)
    return classify_frame(df)


def classify_frame(
    df,
    *,
    source: Optional[str] = None,
    vendor: str = "",
    symbol: str = "",
    real_rows: Optional[int] = None,
    note: str = "",
) -> Provenance:
    """
    Build a Provenance for ``df``.

    ``source`` is the loader's own claim. When it is omitted the frame is
    reported as ``unverified``: absence of evidence is not evidence of real
    data, and assuming anything shaped like OHLCV is real is the bug this
    module exists to stop.
    """
    prov = Provenance(vendor=vendor, symbol=symbol, note=note)
    if df is None:
        return prov

    try:
        prov.rows = int(len(df))
    except Exception:
        return prov

    times, has_dt = _ts_strings(df)
    prov.has_datetime_index = bool(has_dt)
    prov.has_placeholder_timestamps = _placeholder(times)
    if times:
        prov.first_ts = times[0]
        prov.last_ts = times[-1]

    # ---- structural checks (they can only downgrade, never bless) ---------
    try:
        if "close" in df.columns:
            closes = [float(x) for x in df["close"].tolist()]
            prov.fingerprint = fingerprint(closes)
    except Exception:
        pass

    try:
        if all(c in df.columns for c in ("open", "high", "low", "close")):
            o = df["open"].tolist()
            h = df["high"].tolist()
            lo = df["low"].tolist()
            c = df["close"].tolist()
            bad = 0
            for i in range(min(len(o), len(h), len(lo), len(c))):
                try:
                    oi, hi, li, ci = float(o[i]), float(h[i]), float(lo[i]), float(c[i])
                except (TypeError, ValueError):
                    bad += 1
                    continue
                if not all(math.isfinite(v) for v in (oi, hi, li, ci)):
                    bad += 1
                elif li <= 0 or hi + 1e-9 < max(oi, ci) or li - 1e-9 > min(oi, ci) or hi < li:
                    bad += 1
            prov.ohlc_violations = bad
    except Exception:
        pass

    try:
        if "volume" in df.columns:
            vols = [float(x) for x in df["volume"].tolist()]
            prov.zero_volume_rows = sum(1 for v in vols if v <= 0)
    except Exception:
        pass

    try:
        if times:
            prov.duplicate_bars = max(0, len(times) - len(set(times)))
    except Exception:
        pass

    # ---- resolve the claim -------------------------------------------------
    claim = (source or "").strip().lower()
    if claim not in (REAL, SYNTHETIC, MIXED, UNVERIFIED):
        claim = UNVERIFIED

    if real_rows is not None:
        prov.real_rows = max(0, int(real_rows))
        prov.synthetic_rows = max(0, prov.rows - prov.real_rows)
        if prov.synthetic_rows and prov.real_rows:
            claim = MIXED
        elif prov.synthetic_rows and not prov.real_rows:
            claim = SYNTHETIC
    elif claim == REAL:
        prov.real_rows = prov.rows
    elif claim == SYNTHETIC:
        prov.synthetic_rows = prov.rows
    elif claim == MIXED:
        prov.synthetic_rows = prov.rows  # unknown split: assume worst

    # Structural evidence downgrades real -> unverified.
    if claim == REAL:
        if prov.has_placeholder_timestamps and not prov.has_datetime_index:
            claim = UNVERIFIED
            prov.note = (prov.note + "; no usable timestamps").strip("; ")
        elif prov.ohlc_violations > max(1, prov.rows // 100):
            claim = UNVERIFIED
            prov.note = (prov.note + "; OHLC violations").strip("; ")

    prov.source = claim
    return prov


def is_promotable(prov: Provenance, min_bars: int = 252) -> Tuple[bool, str]:
    """Gate used before anything may be called a survivor."""
    if prov.rows < min_bars:
        return False, "insufficient_bars"
    if prov.is_synthetic:
        return False, "synthetic_data_rejected"
    if not prov.is_real:
        return False, "unverified_data_rejected"
    if prov.ohlc_violations:
        return False, "ohlc_integrity_violations"
    if prov.duplicate_bars > max(1, prov.rows // 100):
        return False, "duplicate_bars"
    return True, "ok"


# ---- manifests (so a cache can be audited months later) -------------------

def write_manifest(path: str, prov: Provenance) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(prov.as_dict(), fh, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def read_manifest(path: str) -> Optional[Provenance]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return Provenance.from_dict(json.load(fh))
    except Exception:
        return None


# ---- stored-state audit ---------------------------------------------------

def audit_stored_state(db_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Classify the AITE book that already exists on disk.

    Records written before tagging existed can only be *inferred*, so this
    reports suspects rather than certainties. Two signals are reliable:

    * an explicit ``data_provenance`` field (records written after the fix);
    * trade timestamps of the form ``bar_<n>`` - the exam only emits those when
      the frame carried no usable datetime index, which is the same class of
      frame the synthetic padding path produced.

    Returns a JSON-ready report. Never raises.
    """
    root = db_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "aite"
    )
    report: Dict[str, Any] = {
        "db_dir": root,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bots": {"total": 0, "tagged": 0, "alive": 0, "alive_unverified": 0,
                 "alive_synthetic": 0, "alive_real": 0},
        "portfolio": {"bot_ids": 0, "unverified_ids": 0, "unknown_ids": 0},
        "trades": {"sampled": 0, "placeholder_timestamps": 0,
                   "placeholder_pct": 0.0, "first_ts": None, "last_ts": None},
        "exam_cache": {},
        "suspects": [],
        "verdict": "no_state",
    }

    def _load(name: str) -> Any:
        p = os.path.join(root, name)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    bots_doc = _load("bots.json") or {}
    bots = list(bots_doc.get("bots") or [])
    by_id: Dict[str, Dict[str, Any]] = {}
    for b in bots:
        bid = str(b.get("bot_id") or "")
        if bid:
            by_id[bid] = b
        report["bots"]["total"] += 1
        prov = Provenance.from_dict(b.get("data_provenance") or {})
        if b.get("data_provenance"):
            report["bots"]["tagged"] += 1
        if b.get("status") == "alive":
            report["bots"]["alive"] += 1
            if prov.is_synthetic:
                report["bots"]["alive_synthetic"] += 1
                report["suspects"].append({"bot_id": bid, "name": b.get("name"),
                                           "why": "tagged synthetic provenance"})
            elif prov.is_real:
                report["bots"]["alive_real"] += 1
            else:
                report["bots"]["alive_unverified"] += 1
                report["suspects"].append({"bot_id": bid, "name": b.get("name"),
                                           "why": "no provenance recorded"})

    port = _load("portfolio.json") or {}
    ids = list(port.get("bot_ids") or [])
    report["portfolio"]["bot_ids"] = len(ids)
    for bid in ids:
        b = by_id.get(bid)
        if b is None:
            report["portfolio"]["unknown_ids"] += 1
            continue
        prov = Provenance.from_dict(b.get("data_provenance") or {})
        if not prov.is_real:
            report["portfolio"]["unverified_ids"] += 1

    trades_path = os.path.join(root, "trades.jsonl")
    placeholder = 0
    sampled = 0
    first_ts = last_ts = None
    try:
        with open(trades_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sampled += 1
                et = str(row.get("entry_time", ""))
                if et.startswith("bar_"):
                    placeholder += 1
                if first_ts is None:
                    first_ts = et
                last_ts = str(row.get("exit_time", et))
                if sampled >= 20000:
                    break
    except Exception:
        pass
    report["trades"] = {
        "sampled": sampled,
        "placeholder_timestamps": placeholder,
        "placeholder_pct": round(100.0 * placeholder / sampled, 2) if sampled else 0.0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }

    report["exam_cache"] = _load("exam_cache.json") or {}

    if report["bots"]["total"] == 0:
        report["verdict"] = "no_state"
    elif (report["bots"]["alive_synthetic"] or report["bots"]["alive_unverified"]
          or placeholder):
        report["verdict"] = "contaminated_or_unprovable"
    else:
        report["verdict"] = "clean"
    return report


if __name__ == "__main__":
    print(json.dumps(audit_stored_state(), indent=2, default=str))
