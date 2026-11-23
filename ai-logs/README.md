# AI tools and records

Codex Desktop and Codex CLI used `gpt-6.2-sol`. Desktop supported implementation, review, and release verification. CLI ran two natural-language walkthroughs as an MCP client. No model API is needed to run the application.

## Files and verification

| File | Source and coverage |
| --- | --- |
| `project-trace.jsonl` | Chronological projection of the Desktop Codex rollout JSONL through the pre-submission export. Project tool calls and results are reproduced where they are publishable. Placeholders identify omitted private messages, document content, and mixed-scope tool payloads. Local account and project paths are masked. |
| `model-demo/search.prompt.txt` and `search.events.jsonl` | The input and `codex exec --json` output for the natural-language search. The model picked `search_assets` and returned `asset-005` and `asset-001`. |
| `model-demo/retry.prompt.txt` and `retry.events.jsonl` | The input and `codex exec --json` output for argument recovery. The model corrected `movie` to `video` after the error. |
| `usage-heatmap/codex-2026-08-23-to-2026-11-23.png` | Daily thread starts from the local Codex thread index from August 23 through November 23, 2026, in Asia/Shanghai. `scripts/generate_usage_heatmap.py` queries only creation times and draws the full date range. |

The original Desktop rollout contains the private assignment document and unrelated local account and operations details. The public project trace omits those records and tool-internal metadata. It is an edited project trace and does not satisfy a requirement for a complete, unedited Desktop export. The CLI recordings come from two isolated invocations that only used the configured Reel MCP server.

## Commit map

| Milestone | Commits |
| --- | --- |
| Repository and locked environment | `8b54a82`, `2beca7d` |
| Synthetic catalog and read/write MCP capabilities | `bb46086`, `75488b3` |
| Human approval, previews, and cancellation | `5e59816` |
| Transport, trace, and dashboard | `a89e660`, `80c04f6` |
| Reproducible demo, review evidence, and regression checks | `72ac2ca` |
