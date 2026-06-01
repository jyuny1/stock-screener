"""Unit tests for the env-driven OpenAI-compatible IBD tiebreaker."""
import pytest

from app.services.llm.openai_compatible_client import (
    OpenAICompatibleTiebreaker,
    _resolve_litellm_model,
    build_ibd_tiebreaker,
    match_choice,
)

SHORTLIST = ["Computers-Software", "Computers-Hardware", "Medical-Drugs"]


def test_match_choice_exact_case_insensitive():
    assert match_choice("computers-software", SHORTLIST) == "Computers-Software"
    assert match_choice("  Medical-Drugs  ", SHORTLIST) == "Medical-Drugs"


def test_match_choice_substring_longest_first():
    # Response embeds the answer in prose; longest matching group wins.
    txt = "The best fit is Computers-Software for this company."
    assert match_choice(txt, SHORTLIST) == "Computers-Software"


def test_match_choice_none_when_unmatched():
    assert match_choice("Energy-Oil", SHORTLIST) is None
    assert match_choice("", SHORTLIST) is None


def test_resolve_litellm_model_prefixing():
    assert _resolve_litellm_model("deepseek-chat") == "openai/deepseek-chat"
    assert _resolve_litellm_model("minimax/MiniMax-M2.7") == "minimax/MiniMax-M2.7"
    assert _resolve_litellm_model("openai/gpt-4o") == "openai/gpt-4o"


def test_tiebreaker_call_uses_injected_completion():
    captured = {}

    def fake_complete(prompt: str) -> str:
        captured["prompt"] = prompt
        return "Computers-Hardware"

    tb = OpenAICompatibleTiebreaker(
        model="deepseek-chat", api_base="https://x/v1", api_key="k",
        complete_fn=fake_complete,
    )
    assert tb.model_id == "deepseek-chat"
    assert tb("A chip maker", SHORTLIST) == "Computers-Hardware"
    assert "A chip maker" in captured["prompt"]
    assert "Computers-Software" in captured["prompt"]  # shortlist rendered


def test_tiebreaker_call_swallows_errors():
    def boom(prompt: str) -> str:
        raise RuntimeError("network down")

    tb = OpenAICompatibleTiebreaker(model="x", api_base=None, api_key=None, complete_fn=boom)
    assert tb("text", SHORTLIST) is None  # never propagates


def test_build_tiebreaker_env_driven(monkeypatch):
    monkeypatch.setenv("IBD_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("IBD_LLM_API_BASE", "https://api.deepseek.com/v1")
    monkeypatch.setenv("IBD_LLM_API_KEY", "sk-test")
    tb, model_id = build_ibd_tiebreaker()
    assert model_id == "deepseek-chat"
    assert isinstance(tb, OpenAICompatibleTiebreaker)
    assert tb.api_base == "https://api.deepseek.com/v1"


def test_build_tiebreaker_none_when_unconfigured(monkeypatch):
    # No env-driven model, and no sanctioned provider key available → free-only.
    monkeypatch.delenv("IBD_LLM_MODEL", raising=False)
    monkeypatch.setattr(
        "app.services.llm.openai_compatible_client._sanctioned_key_available",
        lambda: False,
    )
    tb, model_id = build_ibd_tiebreaker()
    assert tb is None
    assert model_id is None


def test_build_tiebreaker_sanctioned_when_key_present(monkeypatch):
    # No env model but a sanctioned key exists → returns the in-repo tiebreaker.
    monkeypatch.delenv("IBD_LLM_MODEL", raising=False)
    monkeypatch.setattr(
        "app.services.llm.openai_compatible_client._sanctioned_key_available",
        lambda: True,
    )
    tb, model_id = build_ibd_tiebreaker()
    assert tb is not None
    assert model_id == "minimax/MiniMax-M2.7"  # ibd_classification preset primary
