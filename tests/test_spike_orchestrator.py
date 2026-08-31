"""Focused tests for BiologicalSpikeOrchestrator (registry, audit, unload).

Standalone:  python tests/test_spike_orchestrator.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _tmp_audit():
    handle = tempfile.NamedTemporaryFile(prefix="spike_audit_", suffix=".json", delete=False)
    handle.close()
    return handle.name


def test_registry_maps_real_classes():
    from core.spike_orchestrator import SUBSYSTEM_REGISTRY

    assert SUBSYSTEM_REGISTRY["monitoring"]["class_name"] == "OKFFinancialRAG"
    assert SUBSYSTEM_REGISTRY["monitoring"]["module"] == "core.okf_financial_rag"
    assert "local_semantic_retrieval" in SUBSYSTEM_REGISTRY["monitoring"]["methods"]

    assert SUBSYSTEM_REGISTRY["decision_engine"]["class_name"] == "SelfLearningQuantAgent"
    assert SUBSYSTEM_REGISTRY["decision_engine"]["module"] == "core.self_learning_agent"
    assert (
        "execute_self_reflection_learning"
        in SUBSYSTEM_REGISTRY["decision_engine"]["methods"]
    )

    assert SUBSYSTEM_REGISTRY["risk_mitigation"]["class_name"] == "SituationalQuantTackle"
    assert SUBSYSTEM_REGISTRY["risk_mitigation"]["module"] == "engine.situational_quant"
    assert "inspect_market_vitals" in SUBSYSTEM_REGISTRY["risk_mitigation"]["methods"]

    from core.okf_financial_rag import OKFFinancialRAG
    from core.self_learning_agent import SelfLearningQuantAgent
    from engine.situational_quant import SituationalQuantTackle

    assert callable(OKFFinancialRAG.local_semantic_retrieval)
    assert callable(SelfLearningQuantAgent.execute_self_reflection_learning)
    assert callable(SituationalQuantTackle.inspect_market_vitals)


def test_unknown_key_does_not_raise():
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    path = _tmp_audit()
    try:
        orch = BiologicalSpikeOrchestrator(audit_path=path)
        out = orch.trigger_spike_activation("not_a_node", "anything")
        assert out["ok"] is False
        assert out["error_type"] == "KeyError"
        assert "Registered nodes" in (out["error"] or "")
        assert out["traceback"]
        entries = json.loads(open(path, encoding="utf-8").read())
        assert entries[-1]["ok"] is False
        assert entries[-1]["error_type"] == "KeyError"
    finally:
        os.unlink(path)


def test_dummy_subsystem_unloads_from_sys_modules():
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    name = "zero_spike_dummy_subsystem_mod"
    path = _tmp_audit()
    mod = types.ModuleType(name)

    class Dummy:
        def ping(self, label="ok"):
            return f"pong:{label}"

    mod.Dummy = Dummy
    sys.modules[name] = mod
    try:
        orch = BiologicalSpikeOrchestrator(
            audit_path=path,
            registry={"dummy": {"module": name, "class_name": "Dummy"}},
            reload_if_present=False,
        )
        out = orch.trigger_spike_activation("dummy", "ping", "live")
        assert out["ok"] is True
        assert out["result"] == "pong:live"
        assert name not in sys.modules
        entries = json.loads(open(path, encoding="utf-8").read())
        assert entries[-1]["ok"] is True
        assert name in (entries[-1].get("flushed_modules") or [])
    finally:
        sys.modules.pop(name, None)
        if os.path.exists(path):
            os.unlink(path)


def test_missing_method_is_logged():
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    name = "zero_spike_dummy_missing_method"
    path = _tmp_audit()
    mod = types.ModuleType(name)

    class Dummy:
        def ping(self):
            return "pong"

    mod.Dummy = Dummy
    sys.modules[name] = mod
    try:
        orch = BiologicalSpikeOrchestrator(
            audit_path=path,
            registry={"dummy": {"module": name, "class_name": "Dummy"}},
            reload_if_present=False,
        )
        out = orch.trigger_spike_activation("dummy", "nope")
        assert out["ok"] is False
        assert out["error_type"] == "AttributeError"
        assert "nope" in (out["error"] or "")
    finally:
        sys.modules.pop(name, None)
        if os.path.exists(path):
            os.unlink(path)


def test_risk_mitigation_real_method():
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    path = _tmp_audit()
    preserved = sys.modules.get("engine.situational_quant")
    try:
        orch = BiologicalSpikeOrchestrator(audit_path=path)
        out = orch.trigger_spike_activation(
            "risk_mitigation", "inspect_market_vitals", 18.5, 42.0
        )
        assert out["ok"] is True
        assert out["class_name"] == "SituationalQuantTackle"
        tripped, message = out["result"]
        assert tripped is False
        assert "authorized" in message.lower() or "cleared" in message.lower()
        entries = json.loads(open(path, encoding="utf-8").read())
        assert entries[-1]["ok"] is True
        assert entries[-1]["class_name"] == "SituationalQuantTackle"
    finally:
        if preserved is not None:
            sys.modules["engine.situational_quant"] = preserved
            parent = sys.modules.get("engine")
            if parent is not None:
                setattr(parent, "situational_quant", preserved)
        if os.path.exists(path):
            os.unlink(path)


def test_audit_log_thread_safe_appends():
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    path = _tmp_audit()
    orch = BiologicalSpikeOrchestrator(audit_path=path)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            orch._append_audit(
                {
                    "n": n,
                    "ok": True,
                    "system_node_key": "dummy",
                    "execution_method_name": "ping",
                }
            )
        except BaseException as exc:  # noqa: BLE001 — surface any write failure
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        entries = json.loads(open(path, encoding="utf-8").read())
        assert len(entries) == 8
        assert {item["n"] for item in entries} == set(range(8))
    finally:
        if os.path.exists(path):
            os.unlink(path)


if __name__ == "__main__":
    tests = [
        test_registry_maps_real_classes,
        test_unknown_key_does_not_raise,
        test_dummy_subsystem_unloads_from_sys_modules,
        test_missing_method_is_logged,
        test_risk_mitigation_real_method,
        test_audit_log_thread_safe_appends,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            raise
    if failed:
        raise SystemExit(1)
    print(f"{len(tests)} tests passed.")
