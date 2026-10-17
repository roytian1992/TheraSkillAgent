#!/usr/bin/env python3
"""Hermes-facing wrapper for TheraSkill hybrid retrieval v2."""

from __future__ import annotations

from retrieval_wrapper_common import HYBRID_V2_RELPATH, load_project_module, project_script_path


HYBRID_RETRIEVER = project_script_path(HYBRID_V2_RELPATH)


def load_retriever():
    return load_project_module("theraskill_hybrid_retrieval_v2", HYBRID_RETRIEVER)


if __name__ == "__main__":
    raise SystemExit(load_retriever().main())
