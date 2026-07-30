"""설정 로드·병합 단위 테스트."""

import json

import pytest

from src.config import Config, ConfigError, _deep_merge, load_config, load_dotenv


class TestDeepMerge:
    def test_override_scalar(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_merge_keeps_untouched_keys(self):
        base = {"http": {"timeout": 10, "delay": 1}}
        out = _deep_merge(base, {"http": {"timeout": 5}})
        assert out == {"http": {"timeout": 5, "delay": 1}}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        _deep_merge(base, {"a": {"b": 2}})
        assert base == {"a": {"b": 1}}


class TestResolveSources:
    def _cfg(self):
        data = {"sources": {"yonhap": {}, "hankyung": {}}, "paths": {}}
        return Config(data, path=None)

    def test_all(self):
        assert set(self._cfg().resolve_sources("all")) == {"yonhap", "hankyung"}

    def test_none_means_all(self):
        assert set(self._cfg().resolve_sources(None)) == {"yonhap", "hankyung"}

    def test_comma_list(self):
        assert self._cfg().resolve_sources("yonhap") == ["yonhap"]

    def test_random_returns_one(self):
        assert len(self._cfg().resolve_sources("random")) == 1

    def test_unknown_source_raises(self):
        with pytest.raises(ConfigError):
            self._cfg().resolve_sources("chosun")


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(tmp_path / "none.json")

    def test_defaults_merged(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"sources": {"a": {}}}), encoding="utf-8")
        cfg = load_config(p)
        assert cfg.http["timeout"] == 10          # 기본값
        assert cfg.ai["model"] == "gpt-4o-mini"   # 기본값
        assert "a" in cfg.sources                 # 사용자값

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{ not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(p)


class TestLoadDotenv:
    def test_reads_values_without_overriding_existing(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("FOO_TEST_KEY=frome_file\nexport BAR_TEST=quoted\n", encoding="utf-8")
        monkeypatch.delenv("FOO_TEST_KEY", raising=False)
        monkeypatch.setenv("BAR_TEST", "already_set")

        loaded = load_dotenv(env)

        import os
        assert os.environ["FOO_TEST_KEY"] == "frome_file"
        assert os.environ["BAR_TEST"] == "already_set"  # 기존값 우선
        assert "FOO_TEST_KEY" in loaded

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_dotenv(tmp_path / "nope.env") == []
