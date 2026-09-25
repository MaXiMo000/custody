"""Run: python tests/test_shim.py -- the deprecated import paths still work."""
import importlib
import sys
import unittest
import warnings


class TestShim(unittest.TestCase):
    def test_old_imports_resolve_to_receipt_evidence_and_warn(self):
        sys.modules.pop("custody", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import custody
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertEqual(custody.__version__, "0.2.0")
        self.assertIs(importlib.import_module("custody.core"), importlib.import_module("receipt_evidence.claude.core"))
        self.assertIs(importlib.import_module("custody.hook"), importlib.import_module("receipt_evidence.claude.hook"))


if __name__ == "__main__":
    unittest.main()
