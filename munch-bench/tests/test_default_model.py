"""The OpenAI default is gpt-6-luna, gpt-4o-mini's named replacement (backlog 68).

gpt-4o-mini is shut down on 2027-01-14 (platform.openai.com/docs/deprecations,
notice of 2026-10-14). The provider call runs against a stand-in SDK module.
"""

import ast
import pathlib
import sys
from types import SimpleNamespace

import pytest

from munch_bench import inference
from munch_bench.inference import COST_TABLE, OPENAI_DEFAULT_MODEL

PKG = pathlib.Path(inference.__file__).parent


def test_the_default_is_the_named_replacement():
    assert OPENAI_DEFAULT_MODEL == "gpt-6-luna"


def test_the_default_is_priced_including_its_cache():
    assert OPENAI_DEFAULT_MODEL in COST_TABLE
    assert OPENAI_DEFAULT_MODEL in inference._OPENAI_CACHE_TABLE


def test_every_reasoning_none_model_is_priced():
    for m in inference._OPENAI_REASONING_NONE:
        assert m in COST_TABLE and m in inference._OPENAI_CACHE_TABLE, m


def test_no_module_spells_its_own_openai_default():
    # Three modules each kept a dict of provider defaults; one forgotten copy keeps
    # the retired model. Every "openai" entry must name the shared constant.
    literals = []
    for path in sorted(PKG.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value == "openai" and isinstance(v, ast.Constant):
                        literals.append(f"{path.name}:{v.lineno} {v.value!r}")
    assert literals == []


def test_infer_openai_signature_default():
    import inspect
    assert inspect.signature(inference.infer_openai).parameters["model"].default == OPENAI_DEFAULT_MODEL


def _fake_openai(monkeypatch):
    seen = {}
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage)

    class _Completions:
        def create(self, **kw):
            seen.clear(); seen.update(kw)
            return resp

    class OpenAI:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=OpenAI))
    return seen


def test_dispatch_without_a_model_uses_the_default(monkeypatch):
    seen = _fake_openai(monkeypatch)
    r = inference.infer("ctx", "q", provider="openai", api_key="test")
    assert r.model == OPENAI_DEFAULT_MODEL and seen["model"] == OPENAI_DEFAULT_MODEL


def test_the_default_runs_with_reasoning_off(monkeypatch):
    seen = _fake_openai(monkeypatch)
    inference.infer_openai("ctx", "q", api_key="test")
    assert seen["reasoning_effort"] == "none"
    assert seen["max_completion_tokens"] == 2048
    assert seen["temperature"] == 0.0
    assert "max_tokens" not in seen


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1"])
def test_older_models_get_the_request_they_always_got(monkeypatch, model):
    seen = _fake_openai(monkeypatch)
    inference.infer_openai("ctx", "q", model=model, api_key="test")
    assert {k: v for k, v in seen.items() if k != "messages"} == {
        "model": model, "temperature": 0.0, "max_tokens": 2048}
