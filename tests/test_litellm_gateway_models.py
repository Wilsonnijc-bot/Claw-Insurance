from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nanobot.providers.litellm_provider import LiteLLMProvider


def test_litellm_gateway_rewrites_user_facing_litellm_prefix_to_litellm_proxy():
    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="http://127.0.0.1:4000",
        default_model="litellm/kimi-k2.5",
        provider_name="litellm",
    )

    resolved = provider._resolve_model("litellm/kimi-k2.5")

    assert resolved == "litellm_proxy/kimi-k2.5"


@pytest.mark.asyncio
async def test_litellm_gateway_chat_uses_litellm_proxy_model_for_kimi():
    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="http://127.0.0.1:4000",
        default_model="litellm/kimi-k2.5",
        provider_name="litellm",
    )
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok"),
                finish_reason="stop",
            )
        ],
        usage=None,
    )

    with patch("nanobot.providers.litellm_provider.acompletion", autospec=True) as mock_completion:
        mock_completion.return_value = fake_response

        response = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="litellm/kimi-k2.5",
        )

    assert response.content == "ok"
    assert mock_completion.await_args.kwargs["model"] == "litellm_proxy/kimi-k2.5"


@pytest.mark.asyncio
async def test_litellm_gateway_surfaces_invalid_proxy_token_as_actionable_error():
    provider = LiteLLMProvider(
        api_key="sk-test",
        api_base="http://127.0.0.1:4000",
        default_model="litellm/kimi-k2.5",
        provider_name="litellm",
    )

    with patch("nanobot.providers.litellm_provider.acompletion", autospec=True) as mock_completion:
        mock_completion.side_effect = Exception(
            "litellm.AuthenticationError: AuthenticationError: Litellm_proxyException - "
            "Authentication Error, Invalid proxy server token passed. "
            "Unable to find token in cache or `LiteLLM_VerificationTokenTable`"
        )

        response = await provider.chat(
            messages=[{"role": "user", "content": "hi"}],
            model="litellm/kimi-k2.5",
        )

    assert response.finish_reason == "error"
    assert "LiteLLM proxy authentication failed." in response.content
    assert "providers.litellm.apiKey" in response.content
    assert "master key or virtual key" in response.content
