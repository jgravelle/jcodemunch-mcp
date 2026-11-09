"""Cost estimation prices cached prompt tokens at the provider's cache rates (backlog 67).

The provider calls run against stand-in SDK modules, so no network and no key.
"""

import sys
from types import SimpleNamespace

import pytest

from munch_bench import inference
from munch_bench.inference import COST_TABLE, _estimate_cost


def _plain(model, input_tokens, output_tokens):
    """The pre-fix formula: every input token at the plain input rate."""
    i, o = COST_TABLE.get(model, (1.0, 3.0))
    return (input_tokens * i + output_tokens * o) / 1_000_000


@pytest.mark.parametrize("model", sorted(COST_TABLE) + ["not-a-listed-model"])
def test_no_cache_tokens_prices_exactly_as_before(model):
    assert _estimate_cost(model, 12_345, 678) == pytest.approx(_plain(model, 12_345, 678))


def test_openai_cache_read_is_priced_at_the_cached_input_rate():
    # gpt-4o-mini: $0.15 input, $0.075 cached input, $0.60 output per 1M.
    got = _estimate_cost("gpt-4o-mini", 10_000, 500, cache_read_tokens=8_000)
    assert got == pytest.approx((2_000 * 0.15 + 8_000 * 0.075 + 500 * 0.60) / 1_000_000)
    assert got < _plain("gpt-4o-mini", 10_000, 500)


def test_anthropic_cache_reads_and_writes_use_the_published_multipliers():
    # claude-haiku-4-5: $1 input -> read $0.10, 5-minute write $1.25, 1-hour write $2.
    got = _estimate_cost("claude-haiku-4-5", 10_000, 0, cache_read_tokens=4_000,
                         cache_write_tokens=3_000, cache_write_1h_tokens=2_000)
    assert got == pytest.approx((1_000 * 1.0 + 4_000 * 0.10 + 3_000 * 1.25 + 2_000 * 2.0) / 1_000_000)


def test_an_unpriced_model_never_reads_cheaper_for_its_cache():
    # No cache row: cached tokens stay at the plain input rate, so the estimate cannot drop.
    for model in ("llama-3.3-70b-versatile", "not-a-listed-model"):
        assert _estimate_cost(model, 10_000, 100, cache_read_tokens=9_000) == pytest.approx(
            _plain(model, 10_000, 100))


def test_every_openai_model_in_the_cost_table_has_a_cache_row():
    openai_models = {m for m in COST_TABLE if m.startswith("gpt-")}
    assert openai_models <= set(inference._OPENAI_CACHE_TABLE)


def test_cache_rates_are_never_above_plain_input_for_reads():
    for model, (i, _o) in COST_TABLE.items():
        read, _w5, _w1h = inference._cache_rates(model, i)
        assert read <= i, model


# --- the provider call sites -------------------------------------------------------


def _fake_openai(usage):
    msg = SimpleNamespace(content="ok")
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)
    seen = {}

    class _Completions:
        def create(self, **kw):
            seen.update(kw)
            return resp

    class OpenAI:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=_Completions())

    return SimpleNamespace(OpenAI=OpenAI), seen


def test_infer_openai_bills_the_cached_part_of_the_prompt(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=10_000, completion_tokens=500,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=8_000))
    mod, _ = _fake_openai(usage)
    monkeypatch.setitem(sys.modules, "openai", mod)
    r = inference.infer_openai("ctx", "q", model="gpt-4o-mini", api_key="test")
    assert r.input_tokens == 10_000
    assert r.cache_read_tokens == 8_000 and r.cache_write_tokens == 0
    assert r.cost_usd == pytest.approx(_estimate_cost("gpt-4o-mini", 10_000, 500, cache_read_tokens=8_000))
    assert r.cost_usd < _plain("gpt-4o-mini", 10_000, 500)


def test_infer_openai_reads_cache_writes_an_older_sdk_does_not_declare(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=4_000, completion_tokens=10,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=3_000))
    mod, _ = _fake_openai(usage)
    monkeypatch.setitem(sys.modules, "openai", mod)
    r = inference.infer_openai("ctx", "q", model="gpt-4o-mini", api_key="test")
    assert r.cache_write_tokens == 3_000


def test_infer_openai_without_prompt_details_is_unchanged(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=1_000, completion_tokens=100, prompt_tokens_details=None)
    mod, _ = _fake_openai(usage)
    monkeypatch.setitem(sys.modules, "openai", mod)
    r = inference.infer_openai("ctx", "q", model="gpt-4o-mini", api_key="test")
    assert r.cost_usd == pytest.approx(_plain("gpt-4o-mini", 1_000, 100))


def test_infer_anthropic_counts_and_prices_cached_tokens(monkeypatch):
    usage = SimpleNamespace(input_tokens=1_000, output_tokens=200, cache_read_input_tokens=4_000,
                            cache_creation_input_tokens=5_000,
                            cache_creation=SimpleNamespace(ephemeral_5m_input_tokens=3_000,
                                                           ephemeral_1h_input_tokens=2_000))
    resp = SimpleNamespace(usage=usage, content=[SimpleNamespace(type="text", text="ok")])

    class Anthropic:
        def __init__(self, **kw):
            self.messages = SimpleNamespace(create=lambda **kw: resp)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=Anthropic))
    r = inference.infer_anthropic("ctx", "q", model="claude-haiku-4-5", api_key="test")
    assert r.input_tokens == 10_000  # the whole prompt, as on the other providers
    assert r.cache_read_tokens == 4_000 and r.cache_write_tokens == 5_000
    assert r.cost_usd == pytest.approx(_estimate_cost(
        "claude-haiku-4-5", 10_000, 200, cache_read_tokens=4_000,
        cache_write_tokens=3_000, cache_write_1h_tokens=2_000))
