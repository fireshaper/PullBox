

from pullbox.config import Settings


def test_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("PULLBOX_DEBUG", "true")
    s = Settings()
    assert s.debug is True


def test_default_debug_is_false():
    s = Settings()
    assert s.debug is False


def test_yaml_file_sets_debug(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("debug: true\n")
    monkeypatch.setenv("PULLBOX_CONFIG_FILE", str(config_file))
    monkeypatch.delenv("PULLBOX_DEBUG", raising=False)
    s = Settings()
    assert s.debug is True


def test_env_var_wins_over_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("debug: true\n")
    monkeypatch.setenv("PULLBOX_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("PULLBOX_DEBUG", "false")
    s = Settings()
    assert s.debug is False


def test_missing_yaml_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("PULLBOX_CONFIG_FILE", str(tmp_path / "nonexistent.yaml"))
    s = Settings()
    assert s.debug is False
