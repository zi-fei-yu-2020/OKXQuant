from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import runtime_features


class RuntimeFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data" / "runtime_features.json"
        self.path.parent.mkdir()
        self.path_patch = patch.object(runtime_features, "CONFIG_PATH", self.path)
        self.path_patch.start()
        self.env_patch = patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        os.environ.pop("OKXQUANT_RUNTIME_PROFILE", None)

    def tearDown(self):
        self.env_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    def test_legacy_install_defaults_to_standard_and_only_optional_features_exist(self):
        result = runtime_features.status()
        self.assertEqual(result["profile"], "standard")
        self.assertEqual(set(result["features"]), set(runtime_features.FEATURES))
        self.assertTrue(all(result["features"].values()))
        self.assertEqual(result["source"], "default")

    def test_light_disables_only_research_allowlist(self):
        result = runtime_features.save_config({"profile": "light"})
        self.assertEqual(result["profile"], "light")
        self.assertTrue(all(value is False for value in result["features"].values()))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("risk", text)
        self.assertNotIn("ledger", text)
        self.assertNotIn("news", text)

    def test_environment_is_bootstrap_default_but_persisted_profile_is_authoritative(self):
        with patch.dict(os.environ, {"OKXQUANT_RUNTIME_PROFILE": "light"}):
            bootstrap = runtime_features.status()
            self.assertEqual(bootstrap["profile"], "light")
            self.assertEqual(bootstrap["source"], "environment")
            runtime_features.save_config({"profile": "standard", "features": {"entry_research": False}})
            result = runtime_features.status()
        self.assertEqual(result["profile"], "standard")
        self.assertEqual(result["source"], "config")
        self.assertFalse(result["features"]["entry_research"])
        self.assertTrue(result["features"]["factor_snapshots"])

    def test_malformed_or_unreadable_config_fails_closed_for_optional_features(self):
        self.path.write_text(json.dumps({"version": 1, "profile": "standard", "features": {"news": False}}), encoding="utf-8")
        with patch.dict(os.environ, {"OKXQUANT_RUNTIME_PROFILE": "standard"}):
            result = runtime_features.status()
        self.assertEqual(result["profile"], "light")
        self.assertFalse(any(result["features"].values()))
        self.assertEqual(result["source"], "config_error")
        self.assertIn("error", result)
        with patch.object(runtime_features.CONFIG_PATH.__class__, "read_text", side_effect=PermissionError("denied")):
            blocked = runtime_features.status()
        self.assertFalse(any(blocked["features"].values()))

    def test_frontend_overrides_alias_is_validated_for_conflicts(self):
        saved = runtime_features.save_config({"profile": "light", "overrides": {"factor_snapshots": True}})
        self.assertTrue(saved["features"]["factor_snapshots"])
        with self.assertRaises(ValueError):
            runtime_features.save_config({"profile": "standard", "features": {}, "overrides": {"entry_research": False}})

    def test_save_rejects_unknown_or_non_boolean_flags_without_replacing_file(self):
        good = runtime_features.save_config({"profile": "standard"})
        original = self.path.read_bytes()
        for payload in (
            {"profile": "standard", "features": {"news": False}},
            {"profile": "standard", "features": {"entry_research": 0}},
            {"profile": "unsafe"},
        ):
            with self.assertRaises(ValueError):
                runtime_features.save_config(payload)
            self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(good["features"]["entry_research"])


if __name__ == "__main__":
    unittest.main()
