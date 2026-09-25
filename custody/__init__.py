"""Deprecated: custody-evidence is now part of receipt-evidence.

`custody-hook` is still the command; the code is `receipt_evidence.claude`.
This package only keeps old imports working; it will get no new features.
"""
import importlib
import sys
import warnings

__version__ = "0.2.0"

warnings.warn(
    "custody-evidence is now part of receipt-evidence; import receipt_evidence instead of custody",
    DeprecationWarning, stacklevel=2)

# Old submodule paths resolve to the new modules themselves, so
# `from custody.X import Y` and `import custody.X` keep working unchanged.
core = sys.modules[__name__ + ".core"] = importlib.import_module("receipt_evidence.claude.core")
hook = sys.modules[__name__ + ".hook"] = importlib.import_module("receipt_evidence.claude.hook")
