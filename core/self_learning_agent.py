"""
ZERO ENGINE — Self-Learning Quant Prompt Mutator
================================================

Offline reinforcement-style reflection loop. Compares a predicted close
against the realized close, accumulates a per-index rolling variance
window (~20 sessions), and rewrites the Lead PM Agent system prompt when
the rolling mean exceeds 2%.

State lives in ``db/self_learning_quant_weights.json`` (atomic replace).
Stdlib only — no network, no ML runtime.

Usage
-----
    from core.self_learning_agent import SelfLearningQuantAgent

    agent = SelfLearningQuantAgent()
    log = agent.execute_self_reflection_learning(24200.0, 23500.0, index="NIFTY")
    prompt = agent.get_current_prompt("NIFTY")
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union

logger = logging.getLogger("ZERO_SELF_LEARNING")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_STATE_REL = os.path.join("db", "self_learning_quant_weights.json")

DRIFT_VARIANCE_THRESHOLD = 0.02
ROLLING_WINDOW = 20
SCHEMA_VERSION = 2
_ZERO_CLOSE_EPS = 1e-12

TRACKED_INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

# Canonical key <- display / ticker aliases (uppercased, spaces collapsed).
_INDEX_ALIASES = {
    "NIFTY": "NIFTY",
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "^NSEI": "NIFTY",
    "NSEI": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "^NSEBANK": "BANKNIFTY",
    "NSEBANK": "BANKNIFTY",
    "SENSEX": "SENSEX",
    "BSE SENSEX": "SENSEX",
    "BSESENSEX": "SENSEX",
    "^BSESN": "SENSEX",
    "BSESN": "SENSEX",
}

DEFAULT_LEAD_PM_PROMPT = (
    "You are the Lead PM Agent. Maintain high-conviction risk metrics "
    "and focus structural balance on overnight macro indicators."
)

OPTIMIZED_LEAD_PM_PROMPT = (
    "You are the Lead PM Agent. System accuracy is optimized. "
    "Execute macro positions aggressively with standard confidence variables."
)

NumberLike = Union[int, float, str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_index_name(name: Any) -> Optional[str]:
    """Map a display/ticker string to NIFTY | BANKNIFTY | SENSEX, or None."""
    if name is None:
        return None
    raw = str(name).strip()
    if not raw:
        return None
    key = " ".join(raw.upper().replace("_", " ").replace("-", " ").split())
    if key in _INDEX_ALIASES:
        return _INDEX_ALIASES[key]
    compact = key.replace(" ", "")
    for alias, canon in _INDEX_ALIASES.items():
        if alias.replace(" ", "") == compact:
            return canon
    return None


def _resolve_state_path(path: str) -> str:
    if not path or not str(path).strip():
        path = _DEFAULT_STATE_REL
    path = os.path.expanduser(str(path).strip())
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(_PROJECT_ROOT, path))


def _atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    """Write JSON via temp file + os.replace so readers never see a half file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp", prefix=".self_learning_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=4, ensure_ascii=False, default=str)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


def _coerce_float(value: NumberLike, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not a finite number: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} is not a finite number: {value!r}")
    return number


def _rolling_mean(history: List[float]) -> float:
    if not history:
        return 0.0
    return float(sum(history) / len(history))


def _tighten_prompt(rolling_mean: float) -> str:
    pct = round(rolling_mean * 100.0, 2)
    return (
        f"You are the Lead PM Agent. WARNING: System is experiencing predictive drift "
        f"of {pct}% (rolling-session mean). Tighten entry parameters, minimize "
        f"algorithmic position weights, and rely heavily on macro risk targets."
    )


class SelfLearningQuantAgent:
    """Offline reflection loop that mutates the Lead PM system prompt.

    Parameters
    ----------
    state_history_path:
        JSON file holding prompt weights. Relative paths resolve against
        the project root.
    drift_threshold:
        Relative |pred - actual| / |actual| rolling-mean above which the
        drift prompt is written. Default 0.02 (2%), matching the spec.
    rolling_window:
        Sessions kept per index for the variance mean. Default 20.
    """

    def __init__(
        self,
        state_history_path: str = "db/self_learning_quant_weights.json",
        drift_threshold: float = DRIFT_VARIANCE_THRESHOLD,
        rolling_window: int = ROLLING_WINDOW,
    ) -> None:
        self.state_path = _resolve_state_path(state_history_path)
        self.default_prompt_template = DEFAULT_LEAD_PM_PROMPT
        try:
            threshold = float(drift_threshold)
        except (TypeError, ValueError):
            threshold = DRIFT_VARIANCE_THRESHOLD
        self.drift_threshold = (
            threshold if math.isfinite(threshold) and threshold > 0 else DRIFT_VARIANCE_THRESHOLD
        )
        try:
            window = int(rolling_window)
        except (TypeError, ValueError):
            window = ROLLING_WINDOW
        self.rolling_window = window if window > 0 else ROLLING_WINDOW
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {}
        self._initialize_state()

    def _blank_index_state(self) -> Dict[str, Any]:
        return {
            "variance_history": [],
            "rolling_mean_variance": 0.0,
            "current_dynamic_prompt": self.default_prompt_template,
            "tuning_cycles_completed": 0,
            "last_variance_error": None,
            "last_predicted_close": None,
            "last_actual_close": None,
            "last_updated": None,
            "last_reflected_date": None,
            "mode": "neutral",
        }

    def _blank_state(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "system_generation_version": 1.0,
            "current_dynamic_prompt": self.default_prompt_template,
            "cumulative_historical_variance": 0.0,
            "tuning_cycles_completed": 0,
            "last_variance_error": None,
            "last_predicted_close": None,
            "last_actual_close": None,
            "last_updated": None,
            "last_reflected_date": None,
            "drift_threshold": self.drift_threshold,
            "rolling_window": self.rolling_window,
            "indices": {name: self._blank_index_state() for name in TRACKED_INDICES},
        }

    def _normalize_index_bucket(self, raw: Any) -> Dict[str, Any]:
        bucket = self._blank_index_state()
        if not isinstance(raw, dict):
            return bucket
        history: List[float] = []
        raw_hist = raw.get("variance_history")
        if isinstance(raw_hist, list):
            for item in raw_hist:
                try:
                    number = float(item)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number) and number >= 0:
                    history.append(number)
        elif raw.get("last_variance_error") is not None:
            try:
                number = float(raw["last_variance_error"])
                if math.isfinite(number) and number >= 0:
                    history.append(number)
            except (TypeError, ValueError):
                pass
        bucket["variance_history"] = history[-self.rolling_window :]
        bucket["rolling_mean_variance"] = round(_rolling_mean(bucket["variance_history"]), 8)
        prompt = raw.get("current_dynamic_prompt")
        if isinstance(prompt, str) and prompt.strip():
            bucket["current_dynamic_prompt"] = prompt.strip()
        try:
            bucket["tuning_cycles_completed"] = int(raw.get("tuning_cycles_completed", 0) or 0)
        except (TypeError, ValueError):
            pass
        for key in (
            "last_variance_error",
            "last_predicted_close",
            "last_actual_close",
            "last_updated",
            "last_reflected_date",
        ):
            if key in raw:
                bucket[key] = raw[key]
        mode = str(raw.get("mode") or "").strip().lower()
        prompt_l = str(bucket.get("current_dynamic_prompt") or "").lower()
        if mode in ("tighten", "aggressive", "neutral"):
            bucket["mode"] = mode
        elif "warning" in prompt_l or "tighten" in prompt_l:
            bucket["mode"] = "tighten"
        elif "aggressively" in prompt_l or "optimized" in prompt_l:
            bucket["mode"] = "aggressive"
        else:
            bucket["mode"] = "neutral"
        return bucket

    def _seed_indices_from_legacy(self, raw: Mapping[str, Any]) -> Dict[str, Any]:
        """v1 JSON had a single global variance/prompt — copy into every index.

        Cycle count stays on NIFTY only so the global sum does not triple.
        """
        seed = {
            "variance_history": [],
            "current_dynamic_prompt": raw.get("current_dynamic_prompt"),
            "tuning_cycles_completed": 0,
            "last_variance_error": raw.get("last_variance_error"),
            "last_predicted_close": raw.get("last_predicted_close"),
            "last_actual_close": raw.get("last_actual_close"),
            "last_updated": raw.get("last_updated"),
            "last_reflected_date": raw.get("last_reflected_date"),
        }
        if raw.get("last_variance_error") is not None:
            seed["variance_history"] = [raw.get("last_variance_error")]
        buckets = {name: self._normalize_index_bucket(seed) for name in TRACKED_INDICES}
        try:
            buckets["NIFTY"]["tuning_cycles_completed"] = int(
                raw.get("tuning_cycles_completed", 0) or 0
            )
        except (TypeError, ValueError):
            pass
        return buckets

    def _normalize_state(self, raw: Any) -> Dict[str, Any]:
        state = self._blank_state()
        if not isinstance(raw, dict):
            return state
        if "current_dynamic_prompt" in raw and isinstance(raw["current_dynamic_prompt"], str):
            prompt = raw["current_dynamic_prompt"].strip()
            if prompt:
                state["current_dynamic_prompt"] = prompt
        try:
            state["system_generation_version"] = float(raw.get("system_generation_version", 1.0))
        except (TypeError, ValueError):
            pass
        try:
            state["cumulative_historical_variance"] = float(
                raw.get("cumulative_historical_variance", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            pass
        try:
            state["tuning_cycles_completed"] = int(raw.get("tuning_cycles_completed", 0) or 0)
        except (TypeError, ValueError):
            pass
        for key in (
            "last_variance_error",
            "last_predicted_close",
            "last_actual_close",
            "last_updated",
            "last_reflected_date",
        ):
            if key in raw:
                state[key] = raw[key]
        try:
            state["schema_version"] = int(raw.get("schema_version") or SCHEMA_VERSION)
        except (TypeError, ValueError):
            state["schema_version"] = SCHEMA_VERSION

        raw_indices = raw.get("indices")
        if isinstance(raw_indices, dict) and raw_indices:
            migrated: Dict[str, Any] = {name: self._blank_index_state() for name in TRACKED_INDICES}
            for raw_key, raw_bucket in raw_indices.items():
                canon = normalize_index_name(raw_key) or (
                    str(raw_key).strip().upper() if str(raw_key).strip().upper() in TRACKED_INDICES else None
                )
                if canon in TRACKED_INDICES:
                    migrated[canon] = self._normalize_index_bucket(raw_bucket)
            state["indices"] = migrated
        else:
            # v1 file: no per-index map — seed all three from the global fields.
            state["indices"] = self._seed_indices_from_legacy(raw)
            state["schema_version"] = SCHEMA_VERSION

        self._sync_global_from_indices(state)
        return state

    def _sync_global_from_indices(self, state: Dict[str, Any]) -> None:
        """Global prompt = worst tightening book, else highest rolling-mean book."""
        buckets = state.get("indices") if isinstance(state.get("indices"), dict) else {}
        worst_key = None
        worst_mean = -1.0
        tighten_key = None
        tighten_mean = -1.0
        latest_ts = None
        total_cycles = 0
        total_var = 0.0
        for name in TRACKED_INDICES:
            bucket = buckets.get(name) or {}
            try:
                mean = float(bucket.get("rolling_mean_variance") or 0.0)
            except (TypeError, ValueError):
                mean = 0.0
            if mean > worst_mean:
                worst_mean = mean
                worst_key = name
            if str(bucket.get("mode") or "") == "tighten" and mean > tighten_mean:
                tighten_mean = mean
                tighten_key = name
            try:
                total_cycles += int(bucket.get("tuning_cycles_completed") or 0)
            except (TypeError, ValueError):
                pass
            hist = bucket.get("variance_history") or []
            if isinstance(hist, list):
                for item in hist:
                    try:
                        total_var += float(item)
                    except (TypeError, ValueError):
                        pass
            ts = bucket.get("last_updated")
            if ts and (latest_ts is None or str(ts) >= str(latest_ts)):
                latest_ts = ts
                state["last_variance_error"] = bucket.get("last_variance_error")
                state["last_predicted_close"] = bucket.get("last_predicted_close")
                state["last_actual_close"] = bucket.get("last_actual_close")
                state["last_reflected_date"] = bucket.get("last_reflected_date")
        chosen = tighten_key or worst_key
        if chosen and isinstance(buckets.get(chosen), dict):
            prompt = buckets[chosen].get("current_dynamic_prompt")
            if isinstance(prompt, str) and prompt.strip():
                state["current_dynamic_prompt"] = prompt.strip()
        state["tuning_cycles_completed"] = total_cycles
        state["cumulative_historical_variance"] = round(total_var, 8)
        if latest_ts:
            state["last_updated"] = latest_ts
        state["schema_version"] = SCHEMA_VERSION
        state["drift_threshold"] = self.drift_threshold
        state["rolling_window"] = self.rolling_window

    def _read_state_from_disk(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.state_path):
            return None
        try:
            if os.path.getsize(self.state_path) <= 0:
                return None
        except OSError:
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                return self._normalize_state(json.load(handle))
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            logger.warning("Self-learning state unreadable (%s); reinitializing.", exc)
            return None

    def _write_state(self, state: Mapping[str, Any]) -> None:
        _atomic_write_json(self.state_path, dict(state))
        self._state = dict(state)

    def _initialize_state(self) -> None:
        """Create parent dir + seed JSON if missing; reload if present."""
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with self._lock:
            loaded = self._read_state_from_disk()
            if loaded is None:
                initial = self._blank_state()
                initial["last_updated"] = _utc_now_iso()
                self._write_state(initial)
            else:
                self._state = loaded
                needs_persist = loaded.get("schema_version") != SCHEMA_VERSION
                if not needs_persist:
                    try:
                        with open(self.state_path, "r", encoding="utf-8") as handle:
                            raw_disk = json.load(handle)
                        needs_persist = not isinstance(
                            raw_disk.get("indices") if isinstance(raw_disk, dict) else None,
                            dict,
                        )
                    except Exception:
                        needs_persist = False
                if needs_persist:
                    loaded["schema_version"] = SCHEMA_VERSION
                    self._write_state(loaded)

    def _cached_state(self) -> Dict[str, Any]:
        """In-memory copy. Does not touch disk (debate must not reload per token)."""
        if self._state:
            return dict(self._state)
        loaded = self._read_state_from_disk()
        if loaded is not None:
            self._state = loaded
            return dict(loaded)
        state = self._blank_state()
        state["last_updated"] = _utc_now_iso()
        self._write_state(state)
        return dict(self._state)

    def get_state(self) -> Dict[str, Any]:
        """Copy of the cached learning state (safe for Streamlit display)."""
        with self._lock:
            return self._cached_state()

    def get_index_state(self, index: str | None = None) -> Dict[str, Any]:
        """Per-index bucket. Unknown/None → empty blank bucket (not a write)."""
        with self._lock:
            state = self._cached_state()
            key = normalize_index_name(index)
            buckets = state.get("indices") if isinstance(state.get("indices"), dict) else {}
            if key and key in buckets:
                return dict(buckets[key])
            return self._blank_index_state()

    def get_current_prompt(self, index: str | None = None) -> str:
        """Lead PM prompt currently in force.

        Per-index when ``index`` is a known name; otherwise the global
        worst-of-indices prompt (highest rolling-mean variance).
        Reads the in-memory cache — does not reload JSON.
        """
        with self._lock:
            state = self._cached_state()
            key = normalize_index_name(index)
            if key:
                buckets = state.get("indices") if isinstance(state.get("indices"), dict) else {}
                bucket = buckets.get(key) if isinstance(buckets, dict) else None
                if isinstance(bucket, dict):
                    prompt = bucket.get("current_dynamic_prompt") or self.default_prompt_template
                    return str(prompt)
            prompt = state.get("current_dynamic_prompt") or self.default_prompt_template
            return str(prompt)

    def execute_self_reflection_learning(
        self,
        predicted_close: NumberLike,
        actual_close: NumberLike,
        index: str | None = None,
        *,
        mutate_prompt: bool = True,
        session_date: str | None = None,
    ) -> str:
        """Update per-index rolling variance from predicted vs realized close.

        Relative variance is ``abs(pred - actual) / abs(actual)``. A zero
        (or near-zero) actual close is rejected so we never divide by zero.

        Prompt rewrite uses the **rolling mean** of the last ``rolling_window``
        sessions for that index, not a single print. ``mutate_prompt=False``
        still records the session (so the window stays honest) but leaves
        the Lead PM prompt unchanged — used when the debate ACTION was right.

        ``index`` None / unknown maps to NIFTY so legacy callers keep working.
        """
        try:
            predicted = _coerce_float(predicted_close, "predicted_close")
            actual = _coerce_float(actual_close, "actual_close")
        except ValueError as exc:
            logger.warning("%s", exc)
            return f"[SELF-LEARNING SKIPPED] {exc}"

        if abs(actual) < _ZERO_CLOSE_EPS:
            logger.warning("actual_close is zero; skipping relative variance.")
            return (
                "[SELF-LEARNING SKIPPED] actual_close is zero; "
                "cannot compute relative variance."
            )

        variance_error = abs(predicted - actual) / abs(actual)
        key = normalize_index_name(index) or "NIFTY"
        reflected_date = str(session_date).strip()[:10] if session_date else None

        with self._lock:
            state = self._cached_state()
            buckets = state.setdefault("indices", {})
            if not isinstance(buckets, dict):
                buckets = {}
                state["indices"] = buckets
            if key not in buckets or not isinstance(buckets.get(key), dict):
                buckets[key] = self._blank_index_state()
            bucket = dict(buckets[key])
            previous_prompt = str(bucket.get("current_dynamic_prompt") or "")

            history = list(bucket.get("variance_history") or [])
            history.append(round(variance_error, 8))
            history = history[-self.rolling_window :]
            rolling = _rolling_mean(history)

            bucket["variance_history"] = history
            bucket["rolling_mean_variance"] = round(rolling, 8)
            bucket["tuning_cycles_completed"] = int(bucket.get("tuning_cycles_completed") or 0) + 1
            bucket["last_variance_error"] = round(variance_error, 8)
            bucket["last_predicted_close"] = predicted
            bucket["last_actual_close"] = actual
            bucket["last_updated"] = _utc_now_iso()
            if reflected_date:
                bucket["last_reflected_date"] = reflected_date

            mutated = False
            if mutate_prompt:
                if rolling > self.drift_threshold:
                    bucket["current_dynamic_prompt"] = _tighten_prompt(rolling)
                    bucket["mode"] = "tighten"
                else:
                    bucket["current_dynamic_prompt"] = OPTIMIZED_LEAD_PM_PROMPT
                    bucket["mode"] = "aggressive"
                if bucket["current_dynamic_prompt"] != previous_prompt:
                    mutated = True
                    try:
                        version = float(state.get("system_generation_version") or 1.0)
                    except (TypeError, ValueError):
                        version = 1.0
                    state["system_generation_version"] = round(version + 0.1, 1)

            buckets[key] = bucket
            self._sync_global_from_indices(state)
            self._write_state(state)
            cycles = bucket["tuning_cycles_completed"]
            version = state["system_generation_version"]
            mode = bucket.get("mode") or "neutral"

        logger.info(
            "Self-learning cycle %s complete (index=%s variance=%.4f rolling=%.4f "
            "mutated=%s version=%s)",
            cycles,
            key,
            variance_error,
            rolling,
            mutated,
            version,
        )
        return (
            "[SELF-LEARNING COMPLETED] Internal system weights calibrated. "
            f"Current Prompt Rule version: {cycles} index={key} "
            f"rolling_mean={round(rolling, 6)} mode={mode} mutated={str(mutated).lower()}"
        )
