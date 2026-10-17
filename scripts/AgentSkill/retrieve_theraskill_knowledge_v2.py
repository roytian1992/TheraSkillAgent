#!/usr/bin/env python3
"""Hermes-facing wrapper for TheraSkill advanced retrieval v2."""

from __future__ import annotations

from retrieval_wrapper_common import ADVANCED_V2_RELPATH, load_project_module, project_script_path


ADVANCED_RETRIEVER = project_script_path(ADVANCED_V2_RELPATH)


def load_retriever():
    return load_project_module("theraskill_advanced_retrieval_v2", ADVANCED_RETRIEVER)


if __name__ == "__main__":
    raise SystemExit(load_retriever().main())
