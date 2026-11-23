# AI tools and records

Codex Desktop and Codex CLI used `gpt-6.2-sol`. Desktop supported implementation, review, and release verification. CLI ran two natural-language walkthroughs as an MCP client. No model API is needed to run the application.

## Files and verification

| File | Source and coverage |
| --- | --- |
| [`desktop-conversation.md`](desktop-conversation.md) | The 16 visible Desktop messages in the original assignment turn, including 4 user messages or choices and 12 Codex progress or final messages. |
| [`project-trace.jsonl`](project-trace.jsonl) | All 129 tool calls and all 129 corresponding results through the first delivery turn, with original order, timestamps, and call IDs. Redactions apply to individual payload fields or output segments, including the restricted segments of mixed tool batches. |
| [`desktop-export-manifest.json`](desktop-export-manifest.json) and [`desktop-attachments/`](desktop-attachments/) | Export bounds, counts, and original-slice SHA-256. The two public attachments preserve the exact recorded binary content. The heatmap preview is one historical tool result. Use the dated image in `usage-heatmap/` for the final chart. |
| `model-demo/search.prompt.txt` and `search.events.jsonl` | The input and `codex exec --json` output for the natural-language search. The model picked `search_assets` and returned `asset-005` and `asset-001`. |
| `model-demo/retry.prompt.txt` and `retry.events.jsonl` | The input and `codex exec --json` output for argument recovery. The model corrected `movie` to `video` after the error. |
| `usage-heatmap/codex-2026-08-23-to-2026-11-23.png` | Daily thread starts from the local Codex thread index from August 23 through November 23, 2026, in Asia/Shanghai. `scripts/generate_usage_heatmap.py` queries only creation times and draws the full date range. |

The Desktop files were exported from the original local rollout JSONL from 2026-11-23 10:58:35 to 11:45:37 Asia/Shanghai. This includes the first submission and its verification. The public export uses `[REDACTED]` for private assignment material, account and infrastructure details, and other restricted payloads. The Markdown represents visible conversation messages and choices. The JSONL records tool inputs and outputs. Internal model and runtime context is outside those two views. The original local session also contains the restricted material, so the public export still has a limitation against the complete, unedited requirement.

The CLI recordings came from two isolated invocations that only used the configured Reel MCP server.

## Commit map

| Milestone | Commits |
| --- | --- |
| Repository and locked environment | `8b54a82`, `2beca7d` |
| Synthetic catalog and read/write MCP capabilities | `bb46086`, `75488b3` |
| Human approval, previews, and cancellation | `5e59816` |
| Transport, trace, and dashboard | `a89e660`, `80c04f6` |
| Reproducible demo, review evidence, and regression checks | `72ac2ca` |
| Initial publication of AI evidence and the usage heatmap | `22ec71c` |
