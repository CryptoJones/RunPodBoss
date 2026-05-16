# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Allow `python -m runpodboss` as an alternate entry point."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
