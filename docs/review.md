# Code review

The review traced operations from the MCP client and browser into the SQLite and filesystem boundaries. It covered schema accuracy, path containment, tag update transactions, approval expiry and reuse, transport correlation, and cancellation cleanup.

- A wire-level test exposed a progress routing issue. The SDK needs the related request ID to send progress on the active POST stream. The implementation now supplies that ID and tests the same stream for progress and cancellation.
- The dashboard strips confirmation codes from call arguments. An HTTP integration test confirms a real deletion code never appears in the state or SSE payload.
- A batch writes to staging and publishes only after processing every source. The cancellation test checks that a partly processed batch leaves no published or staging files. The reset test checks that a batch in progress produces a clear conflict and can be followed by a successful reset.
- Browser validation covered the live feed, the slow-tool switch and reset. UI content from MCP payloads is rendered as text, and the dashboard page restricts scripts and connections to its own origin.

The tests include 30 records, compound filters and pagination, media fixtures, absent and invalid arguments, traversal and symlink escapes, scoped deletion codes, preview creation, real Streamable HTTP progress and cancellation, debug failure, and HTTP access limits. Run `make check` to reproduce the automated checks. The documented service boundary is a single worker and shared synthetic data.
