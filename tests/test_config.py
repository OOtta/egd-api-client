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
    p.write_text('[egd]\nclient_id = "file-id"\nclient_secret = "file-secret"\nean = 859\n')
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
    p.write_text('[egd]\nclient_secret = "s"\n[spot]\nfoo = 1\n')
    p.chmod(0o644)
    cfg = load_config(p)
    assert any("chmod 600" in w for w in cfg.warnings)
    assert any("foo" in w for w in cfg.warnings)


def test_invalid_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[egd\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_new_sections_and_cli_use(tmp_path, monkeypatch, capsys):
    import json

    from egd_api_client.cli import main

    p = tmp_path / "c.toml"
    p.write_text(
        "[spot]\nmultiplier = 1.2\nadd_czk_mwh = 400\nnegative_multiplier = 1.0\n"
        "negative_add_czk_mwh = 0\n[spot.fixed]\nprice_czk_kwh = 4\nprice_low_czk_kwh = 2\n"
        '[low_tariff]\nhours = "22:00-06:00"\nholidays = "sunday"\n'
    )
    cfg = load_config(p)
    assert cfg.warnings == []
    assert cfg.spot["negative_add_czk_mwh"] == 0
    assert cfg.fixed["price_low_czk_kwh"] == 2
    assert cfg.low_tariff == {"hours": "22:00-06:00", "holidays": "sunday"}

    monkeypatch.setenv("EGD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EGD_CONFIG", str(p))
    assert main(["hdo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schedule"] == "NT 00:00–06:00, 22:00–24:00; holidays as Sunday"
