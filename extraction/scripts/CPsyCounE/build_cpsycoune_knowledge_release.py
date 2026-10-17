#!/usr/bin/env python3
"""Retracted legacy release builder.

This file is kept as a tombstone so historical logs point to an explicit
failure instead of recreating misleading release-named raw-stage manifests.
"""

from __future__ import annotations


def main() -> int:
    raise SystemExit('RETRACTED: raw knowledge extraction must not generate release-named manifests. Use outputs/knowledge/units/*.jsonl with projection_manifest/projection_qa only.')


if __name__ == "__main__":
    main()
