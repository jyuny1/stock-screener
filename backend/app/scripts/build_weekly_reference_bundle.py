"""Compatibility wrapper for the renamed foundation update builder."""
from .build_foundation_update_bundle import *  # noqa: F401,F403
from .build_foundation_update_bundle import main

if __name__ == "__main__":
    raise SystemExit(main())
