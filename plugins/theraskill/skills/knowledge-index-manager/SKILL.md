---
name: knowledge-index-manager
description: 导入独立知识包，规划和构建可配置的检索索引，并验证、启用和回滚。
---

# Knowledge Index Manager

## Input

A user-selected knowledge JSONL path or installed knowledge ID; retrieval mode;
optional embedding profile. Fields may use a declarative mapping. Do not infer
approval of candidate knowledge from a request to index it.

## Workflow

1. Call `theraskill_resources` with `parameters.operation=list` to inspect installed resources.
2. Use `plan` with `knowledge` and `mode` to inspect counts and estimated embedding requests. This does not call a model.
3. Use `build` with the same input plus `index_id`. Only select `hybrid` and an `embedding_profile` when the user's task includes model-backed construction. Poll `status` with the returned `job_id`; report failures explicitly. `cancel` stops work at the next batch boundary.
4. For interrupted builds use the same input, ID and embedding profile with `resume=true`. A changed input or model requires a new index ID.
5. Use `verify`, then `search` with `index` and `query`. Use `activate` only when the user wants this index enabled. Use `rollback` to restore the previous selection.
6. A separately downloaded package is installed using `install` and either `archive` or `catalog` + `package`. Installation does not activate it.

## Output and validation

Report the job ID, status, indexed/excluded counts, embedding identity (if any),
and selected index. Never claim all knowledge is vectorized when only eligible
records are embedded. Preserve evidence and retrieval restrictions. Never expose
credentials or overwrite an immutable index. CLI `index` and `knowledge` commands
and the workbench “Knowledge & indexes” page use these same APIs.
