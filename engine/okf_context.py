"""ZERO ENGINE — OKF RAG facade (session-safe singleton).

Other agents import this module — not ``OKFFinancialRAG`` directly.
Persist paths resolve against the project root (same contract as
``core.okf_financial_rag``). The RAG instance is a process-wide lazy
singleton so Streamlit reruns and CLI jobs share one store.

Debate / consensus callers
--------------------------
    from engine.okf_context import get_okf_hits
    ctx = get_okf_hits(f"{index_name} earnings macro", k=3)
    # ctx is joined local-hit text, or "" if the store is empty / RAG
    # deps are missing. Never raises for a normal miss.

Public API
----------
* ``get_okf_hits(query, k=3) -> str``
* ``ingest_okf(title, raw, source_type) -> str``
* ``ingest_headlines(headlines, source_type="Market_News") -> str``
* ``ingest_last_headlines(n=20) -> str`` — feedparser / market_news path,
  no Gemini.

``ingest_*`` may raise ``RuntimeError`` when RAG dependencies are missing;
callers that must not block a UI should catch it, log, and continue.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Iterable, List, Optional, Sequence, Union

logger = logging.getLogger("ZERO_OKF_CONTEXT")

_lock = threading.RLock()
_rag_singleton: Any = None
_rag_error: Optional[str] = None
_ingested_ids: set[str] = set()

Headline = Union[str, dict]

__all__ = [
    "get_okf_hits",
    "ingest_okf",
    "ingest_headlines",
    "ingest_last_headlines",
]


def _note_rag_error(exc: BaseException) -> None:
    """Remember a missing-deps / stack failure so later calls fail fast."""
    global _rag_error
    if _rag_error is None:
        _rag_error = str(exc)


def _get_rag():
    """Lazy process singleton. Persist dir is project-root relative."""
    global _rag_singleton, _rag_error
    if _rag_error is not None:
        raise RuntimeError(_rag_error)
    if _rag_singleton is not None:
        return _rag_singleton
    with _lock:
        if _rag_error is not None:
            raise RuntimeError(_rag_error)
        if _rag_singleton is not None:
            return _rag_singleton
        try:
            from core.okf_financial_rag import OKFFinancialRAG

            _rag_singleton = OKFFinancialRAG()
            return _rag_singleton
        except RuntimeError as exc:
            _note_rag_error(exc)
            raise
        except Exception as exc:
            _note_rag_error(RuntimeError(f"OKF RAG unavailable: {exc}"))
            raise RuntimeError(_rag_error) from exc


def _headline_parts(item: Headline, index: int) -> tuple[str, str]:
    """Return ``(title, raw_body)`` for one headline (str or dict)."""
    if isinstance(item, dict):
        title = str(
            item.get("title")
            or item.get("headline")
            or item.get("text")
            or ""
        ).strip()
        source = str(item.get("source") or "").strip()
        link = str(item.get("link") or "").strip()
        published = str(item.get("published") or "").strip()
        bits = [title]
        if source:
            bits.append(f"Source: {source}")
        if published:
            bits.append(f"Published: {published}")
        if link:
            bits.append(f"Link: {link}")
        raw = "\n".join(bits).strip()
        if not title:
            title = f"Headline {index + 1}"
        return title, raw
    title = str(item or "").strip()
    return title, title


def get_okf_hits(query: str, k: int = 3) -> str:
    """Local semantic hits as one string. Empty store / missing deps → ``""``."""
    q = str(query or "").strip()
    if not q:
        return ""
    try:
        rag = _get_rag()
        return rag.local_semantic_retrieval(q, k=k) or ""
    except RuntimeError as exc:
        _note_rag_error(exc)
        return ""
    except Exception as exc:
        logger.warning("get_okf_hits failed: %s", exc)
        return ""


def ingest_okf(title: str, raw: str, source_type: str) -> str:
    """Convert one document to OKF and append it to the local Chroma store."""
    rag = _get_rag()
    try:
        okf = rag.convert_to_okf_format(title, raw, source_type)
        return rag.index_okf_document(okf)
    except RuntimeError as exc:
        _note_rag_error(exc)
        raise


def ingest_headlines(
    headlines: Sequence[Headline] | Iterable[Headline] | None,
    source_type: str = "Market_News",
) -> str:
    """Convert+index a batch of headlines (str or dict). Raises RuntimeError if RAG deps are missing."""
    items: List[Headline] = list(headlines or [])
    if not items:
        return "OKF ingest skipped: no headlines."

    rag = _get_rag()
    src = str(source_type or "Market_News").strip() or "Market_News"
    n_ok = 0
    n_skip = 0
    last_status = ""

    for idx, item in enumerate(items):
        title, raw = _headline_parts(item, idx)
        if not title or not raw:
            n_skip += 1
            continue
        hid = hashlib.sha1(title.strip().lower().encode("utf-8")).hexdigest()[:16]
        if hid in _ingested_ids:
            n_skip += 1
            continue
        try:
            okf = rag.convert_to_okf_format(title, raw, src)
            last_status = rag.index_okf_document(okf)
            _ingested_ids.add(hid)
            n_ok += 1
        except ValueError:
            n_skip += 1
        except RuntimeError as exc:
            _note_rag_error(exc)
            raise
        except Exception as exc:
            logger.warning("OKF headline ingest failed (%s): %s", title[:80], exc)
            n_skip += 1

    if n_ok == 0 and last_status:
        return last_status
    return (
        f"Indexed {n_ok} headline(s) into local OKF "
        f"({n_skip} skipped, source={src})."
    )


def ingest_last_headlines(n: int = 20, source_type: str = "Market_News") -> str:
    """Pull the current market_news / feedparser tape and index the last ``n`` items.

    No Gemini. Uses ``data.market_news.get_global_news`` (cache / last-good /
    RSS). May raise ``RuntimeError`` when RAG deps are missing.
    """
    try:
        n_int = int(n)
    except (TypeError, ValueError):
        n_int = 20
    n_int = max(1, min(n_int, 50))

    from data.market_news import get_global_news

    items = get_global_news() or []
    return ingest_headlines(items[:n_int], source_type=source_type)
