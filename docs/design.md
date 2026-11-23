# Design notes

## Data and tool contracts

SQLite holds metadata for 30 records across three projects. The source tree contains generated artwork, tone samples, and a synthetic video slate. All paths exposed by the API are relative to the media root. Metadata edits leave source files in place. Every SQLite connection is closed after use. Tag changes and soft deletions obtain a write transaction before reading the current asset state.

Search filters are combined with AND. Date bounds require a time zone, normalize to UTC, and represent an inclusive start and exclusive end. Results are ordered by creation time descending and then ID. `next_offset` provides small pages and may shift when the shared dataset is edited between calls. `path_prefix` accepts named folder components. The domain also checks that a media path resolves inside the library before opening it, including symlink resolution.

The official Python MCP SDK handles discovery, the Streamable HTTP session lifecycle, and request validation at the protocol level. Pydantic describes the individual tool inputs and produces field-specific errors. Tool errors return `isError` and a small structured payload so a client can correct its arguments. The prompt requests a plan before edits. Resource statistics reflect active records.

## Approval and cancellation

The first deletion call previews an asset and registers a request. The dashboard issues a time-limited code through a separate human action. Only the code digest is stored. The code is scoped to the asset, expires after three minutes, and is consumed after a successful soft deletion. It is masked in job traces. The approval flow guards against accidents. In an unauthenticated instance, anyone with dashboard access can perform the human step, which is suitable for a shared synthetic demo.

Thumbnail preparation is paced to take about 15 seconds. A batch selects up to 12 active images and puts JPEGs in a temporary directory. It publishes the directory in a single rename after the final progress update. A cancellation or a failure before that rename cleans up staging. If cancellation races with publishing, a committed batch remains complete. Up to two batches can run at a time. The server retains up to eight completed batches under typical filesystem operation. Reset requires the active batches to finish or be cancelled first.

## Observability and operational boundaries

The SDK server is wrapped at its message-stream boundary. Incoming JSON-RPC calls and outgoing results are correlated by request ID within each MCP session. Dashboard SSE snapshots include progress even when no MCP progress token was requested. The client receives protocol-native progress when it opts in. Cancellation uses the SDK's handling of `notifications/cancelled`. The dashboard follows observed outcomes and displays transport rejections separately from tool errors. Logs are bounded and may truncate unusually large payloads.

One process owns the dashboard history, debug switches, and short-lived approvals. SQLite metadata and published thumbnails persist independently of that process. This is a single-instance demo. Separate instances and isolated data directories are the intended way to run concurrent walkthroughs. Public demos should only receive synthetic inputs. Use the shared-token option when the instance is intended for a specific audience.

Protocol references [Progress](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/progress) and [Cancellation](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation).
