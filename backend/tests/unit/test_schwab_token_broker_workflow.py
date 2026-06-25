from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_soxl_price_d1_uses_schwab_token_broker_oidc() -> None:
    content = (ROOT / ".github" / "workflows" / "soxl-price-d1.yml").read_text(encoding="utf-8")

    assert "id-token: write" in content
    assert "SCHWAB_TOKEN_BROKER_URL" in content
    assert "audience=schwab-token-broker" in content
    assert "/v1/schwab/access-token" in content
    assert "SCHWAB_ACCESS_TOKEN=" in content
    assert "SCHWAB_REFRESH_TOKEN" not in content
    assert "refresh_schwab_oauth_token" not in content
    assert "gh secret set SCHWAB_REFRESH_TOKEN" not in content


def test_schwab_token_broker_deploy_workflow_configures_secret_state() -> None:
    content = (ROOT / ".github" / "workflows" / "schwab-token-broker.yml").read_text(encoding="utf-8")

    assert "name: Schwab Token Broker" in content
    assert "workers/schwab-token-broker" in content
    assert "SCHWAB_BROKER_TOKEN_ENCRYPTION_KEY" in content
    assert "SCHWAB_BROKER_ADMIN_BOOTSTRAP_TOKEN" in content
    assert "wrangler deploy" in content
    assert "wrangler secret put TOKEN_ENCRYPTION_KEY" in content
