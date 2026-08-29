"""
ZERO ENGINE — Offline OKF Financial RAG
=======================================

Parses unstructured transcripts / filings / news into Open Knowledge Format
(OKF) blocks and indexes them in a **local** Chroma collection using the
``all-MiniLM-L6-v2`` sentence-transformer.  Retrieval is cosine similarity
over that on-disk store.

Offline contract
----------------
* No REST calls of our own. Embeddings and Chroma run on local disk.
* Hugging Face Hub is contacted only if the MiniLM weights are missing
  **and** ``HF_HUB_OFFLINE`` is unset. Set ``HF_HUB_OFFLINE=1`` (or
  ``ZERO_OKF_OFFLINE=1``) to refuse network entirely.
* Chroma anonymized telemetry is disabled.

Dependencies (lazy — this module always imports):
    langchain-community, langchain-core, chromadb, sentence-transformers

Usage
-----
    from core.okf_financial_rag import OKFFinancialRAG

    rag = OKFFinancialRAG()  # persist_dir defaults to db/okf_financial_chroma
    okf = rag.convert_to_okf_format("Reliance Q3", transcript, "Corporate_Filing")
    rag.index_okf_document(okf)
    ctx = rag.local_semantic_retrieval("margin commentary", k=2)
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import uuid
import warnings
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger("ZERO_OKF_RAG")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PERSIST_REL = os.path.join("db", "okf_financial_chroma")
_COLLECTION_NAME = "okf_financial"
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_MAX_CHUNK_CHARS = 1600
_CHUNK_OVERLAP = 200

_MISSING_DEPS_MSG = (
    "OKF financial RAG dependencies are not installed. Install "
    "langchain-huggingface, langchain-chroma, langchain-community, "
    "langchain-core, chromadb, and sentence-transformers "
    "(see requirements.txt), then retry."
)

_lock = threading.RLock()


def _env_truthy(name: str) -> bool:
    raw = os.getenv(name, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_persist_dir(persist_dir: str) -> str:
    """Absolute persist path; relative paths are rooted at the project root."""
    if not persist_dir or not str(persist_dir).strip():
        persist_dir = _DEFAULT_PERSIST_REL
    persist_dir = os.path.expanduser(str(persist_dir).strip())
    if os.path.isabs(persist_dir):
        return os.path.normpath(persist_dir)
    return os.path.normpath(os.path.join(_PROJECT_ROOT, persist_dir))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _offline_forced() -> bool:
    return _env_truthy("HF_HUB_OFFLINE") or _env_truthy("ZERO_OKF_OFFLINE")


def _embedding_device() -> str:
    override = os.getenv("ZERO_OKF_DEVICE", "").strip()
    return override or "cpu"


def _sanitize_yaml_scalar(value: str, fallback: str = "UNNAMED") -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or "")).strip()
    text = text.replace(":", " -")
    return text or fallback


def _document_identifier(title: str) -> str:
    raw = re.sub(r"\s+", "_", str(title or "").strip().upper())
    raw = re.sub(r"[^A-Z0-9._-]", "", raw)
    return raw or "UNTITLED_DOCUMENT"


def _split_text(text: str, max_chars: int = _MAX_CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Newline-aware character splitter (no extra langchain splitter dep)."""
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= max_chars:
        return [body]

    chunks: List[str] = []
    start = 0
    n = len(body)
    step_floor = max(max_chars - max(overlap, 0), 1)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            cut = body.rfind("\n", start, end)
            if cut > start + (max_chars // 4):
                end = cut
        chunk = body[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - max(overlap, 0), start + step_floor)
    return chunks or [body[:max_chars]]


def _parse_okf_frontmatter(okf_string: str) -> dict:
    """Best-effort YAML-ish header parse; never raises."""
    meta = {
        "okf_schema_type": "STRUCTURAL_FINANCIAL_RECORD",
        "document_identifier": "",
        "document_metadata_source": "",
        "generation_timestamp": "",
    }
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", okf_string or "", re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_n = key.strip().upper()
        val = value.strip()
        if key_n == "OKF_SCHEMA_TYPE":
            meta["okf_schema_type"] = val
        elif key_n == "DOCUMENT_IDENTIFIER":
            meta["document_identifier"] = val
        elif key_n == "DOCUMENT_METADATA_SOURCE":
            meta["document_metadata_source"] = val
        elif key_n == "GENERATION_TIMESTAMP":
            meta["generation_timestamp"] = val
    return meta


def _import_rag_stack():
    """Import langchain + chromadb. Raises RuntimeError with a clear message."""
    missing: List[str] = []
    embeddings_cls = None
    chroma_cls = None
    document_cls = None
    chromadb_mod = None

    try:
        from langchain_core.documents import Document as document_cls  # type: ignore
    except Exception:
        missing.append("langchain-core")

    try:
        from langchain_huggingface import HuggingFaceEmbeddings as embeddings_cls  # type: ignore
    except Exception:
        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings as embeddings_cls  # type: ignore
        except Exception:
            missing.append("langchain-huggingface or langchain-community (HuggingFaceEmbeddings)")

    try:
        from langchain_chroma import Chroma as chroma_cls  # type: ignore
    except Exception:
        try:
            from langchain_community.vectorstores import Chroma as chroma_cls  # type: ignore
        except Exception:
            missing.append("langchain-chroma or langchain-community (Chroma)")

    try:
        import chromadb as chromadb_mod  # type: ignore
    except Exception:
        missing.append("chromadb")

    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        missing.append("sentence-transformers")

    if missing or embeddings_cls is None or chroma_cls is None or document_cls is None or chromadb_mod is None:
        detail = ", ".join(missing) if missing else "unknown import failure"
        raise RuntimeError(f"{_MISSING_DEPS_MSG} Missing: {detail}")

    return embeddings_cls, chroma_cls, document_cls, chromadb_mod


class OKFFinancialRAG:
    """Offline OKF transcript splitter and persistent Chroma vector store.

    Parameters
    ----------
    persist_dir:
        On-disk Chroma directory. Relative paths resolve against the project
        root (not the process cwd) so Streamlit / CLI launches agree.
    collection_name:
        Single reused collection. Indexing **adds** documents; it does not
        rebuild the store via ``from_documents``.
    embedding_model:
        Sentence-Transformers model id (local Hub cache / disk).
    """

    def __init__(
        self,
        persist_dir: str = "db/okf_financial_chroma",
        collection_name: str = _COLLECTION_NAME,
        embedding_model: str = _EMBEDDING_MODEL,
    ) -> None:
        self.persist_dir = _resolve_persist_dir(persist_dir)
        self.collection_name = collection_name or _COLLECTION_NAME
        self.embedding_model = embedding_model or _EMBEDDING_MODEL
        os.makedirs(self.persist_dir, exist_ok=True)

        self._embeddings = None
        self._vectorstore = None
        self._stack: Optional[Tuple[Any, Any, Any, Any]] = None

        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    # ── lazy stack ────────────────────────────────────────────────────────

    def _get_stack(self) -> Tuple[Any, Any, Any, Any]:
        if self._stack is None:
            self._stack = _import_rag_stack()
        return self._stack

    def _build_embeddings(self):
        HuggingFaceEmbeddings, _, _, _ = self._get_stack()
        device = _embedding_device()
        model_kwargs: dict = {"device": device}
        encode_kwargs = {"normalize_embeddings": True}

        def _make(*, local_files_only: bool):
            kw = dict(model_kwargs)
            if local_files_only:
                kw["local_files_only"] = True
            try:
                return HuggingFaceEmbeddings(
                    model_name=self.embedding_model,
                    model_kwargs=kw,
                    encode_kwargs=encode_kwargs,
                )
            except TypeError:
                kw.pop("local_files_only", None)
                return HuggingFaceEmbeddings(
                    model_name=self.embedding_model,
                    model_kwargs=kw,
                    encode_kwargs=encode_kwargs,
                )

        prefer_local = _offline_forced()
        if prefer_local:
            try:
                return _make(local_files_only=True)
            except Exception as exc:
                raise RuntimeError(
                    f"Offline load of '{self.embedding_model}' failed (HF_HUB_OFFLINE / "
                    f"ZERO_OKF_OFFLINE). Pre-cache the weights locally, then retry. "
                    f"Root cause: {exc}"
                ) from exc

        try:
            return _make(local_files_only=True)
        except Exception:
            logger.info(
                "Local cache miss for %s; attempting Hub load (set HF_HUB_OFFLINE=1 to forbid).",
                self.embedding_model,
            )
            return _make(local_files_only=False)

    def _get_embeddings(self):
        if self._embeddings is None:
            with _lock:
                if self._embeddings is None:
                    self._embeddings = self._build_embeddings()
        return self._embeddings

    @property
    def embeddings(self):
        """Lazy HuggingFaceEmbeddings instance (loaded on first index/query)."""
        return self._get_embeddings()

    def _get_vectorstore(self):
        """Get-or-create a persistent Chroma collection. Empty dirs are fine."""
        if self._vectorstore is not None:
            return self._vectorstore

        with _lock:
            if self._vectorstore is not None:
                return self._vectorstore

            os.makedirs(self.persist_dir, exist_ok=True)
            _, Chroma, _, chromadb = self._get_stack()
            embeddings = self._get_embeddings()

            client = None
            try:
                from chromadb.config import Settings  # type: ignore

                try:
                    client = chromadb.PersistentClient(
                        path=self.persist_dir,
                        settings=Settings(anonymized_telemetry=False),
                    )
                except TypeError:
                    client = chromadb.PersistentClient(path=self.persist_dir)
            except Exception as exc:
                logger.debug("PersistentClient construction fallback: %s", exc)
                client = None

            store = None
            if client is not None:
                try:
                    store = Chroma(
                        client=client,
                        collection_name=self.collection_name,
                        embedding_function=embeddings,
                        collection_metadata={"hnsw:space": "cosine"},
                    )
                except TypeError:
                    store = Chroma(
                        client=client,
                        collection_name=self.collection_name,
                        embedding_function=embeddings,
                    )
                except Exception as exc:
                    logger.warning("Chroma client bind failed (%s); retrying persist_directory.", exc)
                    store = None

            if store is None:
                try:
                    store = Chroma(
                        collection_name=self.collection_name,
                        persist_directory=self.persist_dir,
                        embedding_function=embeddings,
                        collection_metadata={"hnsw:space": "cosine"},
                    )
                except TypeError:
                    store = Chroma(
                        collection_name=self.collection_name,
                        persist_directory=self.persist_dir,
                        embedding_function=embeddings,
                    )

            self._vectorstore = store
            return self._vectorstore

    def _persist_store(self, store) -> None:
        persist = getattr(store, "persist", None)
        if not callable(persist):
            return
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=".*persist.*", category=DeprecationWarning)
                persist()
        except Exception as exc:
            logger.debug("Chroma persist() skipped: %s", exc)

    # ── public API ────────────────────────────────────────────────────────

    def convert_to_okf_format(
        self,
        document_title: str,
        raw_content: str,
        source_type: str,
    ) -> str:
        """Transform unstructured text into the standardized Open Knowledge Format."""
        title = str(document_title or "").strip()
        body = str(raw_content or "").strip()
        source = str(source_type or "").strip()

        if not title:
            raise ValueError("document_title is empty; cannot build an OKF record.")
        if not body:
            raise ValueError("raw_content is empty; refuse to index a blank OKF record.")
        if not source:
            source = "UNSPECIFIED_SOURCE"

        yaml_title = _sanitize_yaml_scalar(title)
        yaml_source = _sanitize_yaml_scalar(source, fallback="UNSPECIFIED_SOURCE")
        identifier = _document_identifier(title)

        okf_structure = f"""---
OKF_SCHEMA_TYPE: STRUCTURAL_FINANCIAL_RECORD
GENERATION_TIMESTAMP: {_utc_now_iso()}
DOCUMENT_METADATA_SOURCE: {yaml_source}
DOCUMENT_IDENTIFIER: {identifier}
---

# ARCHITECTURAL BRIEF: {yaml_title}
```text
{body}
```

# SEMANTIC INDEX CONTEXT
- **Ingestion Line:** Processed under zero-cost localized edge parameters.
- **Quant Strategy Routing:** Operationalized for Agentic Multi-Agent Review.
"""
        return okf_structure.strip()

    def index_okf_document(self, okf_string: str) -> str:
        """Append an OKF record to the local Chroma collection (no store rebuild)."""
        payload = str(okf_string or "").strip()
        if not payload:
            return "OKF index skipped: empty document."

        try:
            _, _, Document, _ = self._get_stack()
            meta = _parse_okf_frontmatter(payload)
            identifier = meta.get("document_identifier") or "UNTITLED_DOCUMENT"
            chunks = _split_text(payload)
            if not chunks:
                return "OKF index skipped: no indexable chunks after split."

            documents = []
            ids: List[str] = []
            batch_token = uuid.uuid4().hex[:10]
            for idx, chunk in enumerate(chunks):
                digest = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:12]
                doc_id = f"{identifier}::{idx}::{digest}"
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "document_identifier": identifier,
                            "okf_schema_type": meta.get("okf_schema_type", ""),
                            "document_metadata_source": meta.get("document_metadata_source", ""),
                            "generation_timestamp": meta.get("generation_timestamp", ""),
                            "chunk_index": idx,
                            "chunk_count": len(chunks),
                            "batch_id": batch_token,
                        },
                    )
                )
                ids.append(doc_id)

            with _lock:
                store = self._get_vectorstore()
                added = self._add_documents(store, documents, ids)
                # Chroma 0.4+ persists automatically; older clients expose persist().
                self._persist_store(store)

            logger.info(
                "Indexed OKF document %s (%s chunk(s), %s new).",
                identifier,
                len(chunks),
                added,
            )
            return (
                "Document structured and indexed into local vector matrix successfully "
                f"({added} chunk(s), id={identifier})."
            )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.exception("OKF index failed")
            return f"OKF index failed: {exc}"

    def _add_documents(self, store, documents: Sequence[Any], ids: Sequence[str]) -> int:
        """Add documents; treat duplicate Chroma IDs as already indexed."""
        try:
            store.add_documents(list(documents), ids=list(ids))
            return len(documents)
        except TypeError:
            store.add_documents(list(documents))
            return len(documents)
        except Exception as exc:
            msg = str(exc).lower()
            if "exist" in msg or "duplicate" in msg:
                added = 0
                for doc, doc_id in zip(documents, ids):
                    try:
                        store.add_documents([doc], ids=[doc_id])
                        added += 1
                    except TypeError:
                        store.add_documents([doc])
                        added += 1
                    except Exception:
                        continue
                return added
            raise

    def local_semantic_retrieval(self, query: str, k: int = 2) -> str:
        """Query the offline vector matrix. Empty store / query → empty string."""
        q = str(query or "").strip()
        if not q:
            return ""

        try:
            k_int = int(k)
        except (TypeError, ValueError):
            k_int = 2
        k_int = max(1, min(k_int, 20))

        if not os.path.isdir(self.persist_dir):
            return ""

        try:
            entries = os.listdir(self.persist_dir)
        except OSError:
            return ""
        if not entries:
            return ""

        try:
            with _lock:
                store = self._get_vectorstore()
                try:
                    count_fn = getattr(store, "_collection", None)
                    if count_fn is not None and hasattr(count_fn, "count"):
                        if int(count_fn.count()) <= 0:
                            return ""
                except Exception:
                    pass
                try:
                    results = store.similarity_search(q, k=k_int)
                except Exception as exc:
                    logger.info("similarity_search on empty/unready store: %s", exc)
                    return ""
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("OKF retrieval failed: %s", exc)
            return ""

        if not results:
            return ""
        return "\n\n".join(
            (getattr(r, "page_content", "") or "").strip()
            for r in results
            if getattr(r, "page_content", "")
        ).strip()
