"""ZERO core package — offline OKF RAG and self-learning prompt mutator.

Explicit package marker (not a PEP 420 namespace). Required so concurrent /
Streamlit-reload imports of ``core.*`` do not raise KeyError on submodule
names.
"""

try:
    from core.okf_financial_rag import OKFFinancialRAG
except Exception:
    OKFFinancialRAG = None

try:
    from core.self_learning_agent import SelfLearningQuantAgent
except Exception:
    SelfLearningQuantAgent = None

__all__ = [
    "OKFFinancialRAG",
    "SelfLearningQuantAgent",
]
