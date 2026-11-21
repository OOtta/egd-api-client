import os

import pytest

from egd_api_client.config import ConfigError, load_config


def test_missing_default_config_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("EGD_CONFIG", raising=False)
    assert load_config().path is None


def test_explicit_missing_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_env_overrides_file(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text(
        '[egd]\nclient_id = "file-id"\nclient_secret = "file-secret"\nean = 859\n', encoding="utf-8"
    )
    p.chmod(0o600)
    monkeypatch.setenv("EGD_CLIENT_ID", "env-id")
    monkeypatch.delenv("EGD_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("EGD_EAN", raising=False)
    cfg = load_config(p)
    assert cfg.client_id == "env-id"
    assert cfg.client_secret == "file-secret"
    assert cfg.ean == "859"
    assert cfg.warnings == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_world_readable_secret_warns(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[egd]\nclient_secret = "s"\n[spot]\nfoo = 1\n', encoding="utf-8")
    p.chmod(0o644)
    cfg = load_config(p)
    assert any("chmod 600" in w for w in cfg.warnings)
    assert any("foo" in w for w in cfg.warnings)


def test_invalid_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[egd\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_new_sections(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        "[spot]\nmultiplier = 1.2\nadd_czk_mwh = 400\nnegative_multiplier = 1.0\n"
        "negative_add_czk_mwh = 0\n[spot.fixed]\nprice_czk_kwh = 4\nprice_low_czk_kwh = 2\n"
        '[low_tariff]\nhours = "22:00-06:00"\nholidays = "weekend"\n'
        "[distribution]\nhigh_czk_kwh = 2.1\nlow_czk_kwh = 0.4\nmonthly_fee = 300\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.warnings == []
    assert cfg.spot["negative_add_czk_mwh"] == 0
    assert cfg.fixed["price_low_czk_kwh"] == 2
    assert cfg.low_tariff == {"hours": "22:00-06:00", "holidays": "weekend"}
    assert cfg.distribution == {"high_czk_kwh": 2.1, "low_czk_kwh": 0.4, "monthly_fee": 300}

    p.write_text('[low_tariff]\nhdo_code = "405"\n', encoding="utf-8")
    assert any("hdo_code" in w for w in load_config(p).warnings)
