"""
Pre-cache Hugging Face ``all-MiniLM-L6-v2`` for Aegis-Quant OKF ingest.

Run once after ``pip install -r requirements.txt`` so the first AEGIS QUANT
ingest does not hit the Hub. After this succeeds, you can set
``HF_HUB_OFFLINE=1`` and/or ``ZERO_OKF_OFFLINE=1`` (see ``.env.example``).

This script talks to Hugging Face only when the weights are missing from the
local cache. It does **not** start Streamlit and does **not** write Chroma.

Usage
-----
    python scripts/warmup_okf_embeddings.py
"""
from __future__ import annotations

import os
import sys

_MODEL_ID = "all-MiniLM-L6-v2"
_WARMUP_SENTENCE = "ZERO Aegis-Quant MiniLM cache warmup."


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _offline_forced() -> bool:
    return _env_truthy("HF_HUB_OFFLINE") or _env_truthy("ZERO_OKF_OFFLINE")


def _device() -> str:
    override = os.getenv("ZERO_OKF_DEVICE", "").strip()
    return override or "cpu"


def main() -> int:
    offline = _offline_forced()
    device = _device()
    print(f"Warming '{_MODEL_ID}' on {device} (sentence-transformers)...")
    if offline:
        print(
            "HF_HUB_OFFLINE / ZERO_OKF_OFFLINE is set — load is cache-only. "
            "Unset those flags if this is the first warmup on this machine."
        )

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "ERROR: sentence-transformers is not installed. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    try:
        model = SentenceTransformer(_MODEL_ID, device=device)
        vector = model.encode(_WARMUP_SENTENCE)
    except Exception as exc:
        print(
            f"ERROR: failed to load '{_MODEL_ID}'. "
            f"{'Cache miss under offline flags.' if offline else 'Hub download or local load failed.'} "
            f"Root cause: {exc}",
            file=sys.stderr,
        )
        return 1

    dim = int(getattr(vector, "shape", [0])[-1] or 0)
    cache_home = os.getenv("HF_HOME") or os.getenv("HUGGINGFACE_HUB_CACHE") or "(default Hugging Face cache)"
    print(f"OK: '{_MODEL_ID}' is cached (embedding dim={dim}).")
    print(f"HF cache: {cache_home}")
    print("You can now set HF_HUB_OFFLINE=1 and/or ZERO_OKF_OFFLINE=1 for cache-only Aegis ingest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
