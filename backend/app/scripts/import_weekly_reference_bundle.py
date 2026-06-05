"""Compatibility wrapper for the renamed foundation update importer."""
from .import_foundation_update_bundle import *  # noqa: F401,F403
from .import_foundation_update_bundle import main

if __name__ == "__main__":
    raise SystemExit(main())
