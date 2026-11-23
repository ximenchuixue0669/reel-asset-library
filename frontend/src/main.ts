type CallState = "running" | "complete" | "error" | "cancelled" | "received" | "disconnected";

type Call = {
  id: number;
  method: string;
  parameters: { name?: string };
  started_at_ms: number;
  state: CallState;
  duration_ms: number | null;
  result: unknown;
  error: unknown;
  progress: { done: number; total: number; message: string } | null;
};

type Approval = { approval_id: string; asset_id: string; ready: boolean; expires_in_seconds: number };
type Snapshot = {
  calls: Call[];
  debug: { fail_next: boolean; slow_tools: boolean; active_jobs: number };
  catalog: { total: number; by_type: { image: number; video: number; audio: number } };
  approvals: Approval[];
};

const stateLabels: Record<CallState, string> = {
  running: "Running", complete: "Complete", error: "Error", cancelled: "Cancelled",
  received: "Received", disconnected: "Disconnected",
};

function required<T extends Element>(selector: string, root: ParentNode = document): T {
  const target = root.querySelector<T>(selector);
  if (!target) throw new Error(`Missing dashboard element: ${selector}`);
  return target;
}

const feedback = required<HTMLParagraphElement>("#feedback");
const feed = required<HTMLDivElement>("#call-log");
const callTemplate = required<HTMLTemplateElement>("#call-template");
const approvalList = required<HTMLDivElement>("#approval-list");
const online = required<HTMLDivElement>("#connection");
const mcpUrl = required<HTMLInputElement>("#mcp-url");
const resetButton = required<HTMLButtonElement>("#reset-data");
const failNext = required<HTMLInputElement>("#fail-next");
const slowTools = required<HTMLInputElement>("#slow-tools");
const clock = new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const callNodes = new Map<number, HTMLDetailsElement>();
const approvalNodes = new Map<string, HTMLElement>();
let view: "all" | "error" = "all";
let lastSnapshot: Snapshot | undefined;

function showFeedback(message: string, error = false): void {
  feedback.hidden = false;
  feedback.textContent = message;
  feedback.dataset.error = String(error);
}

async function post<T>(path: string): Promise<T> {
  const response = await fetch(new URL(`./api/${path}`, location.href), {
    method: "POST", headers: { "X-Reel-Dashboard": "1" }, cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body as T;
}

async function copy(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    showFeedback(`${label} copied.`);
  } catch {
    showFeedback(`Select and copy the ${label.toLowerCase()} manually.`, true);
  }
}

function renderCalls(calls: Call[]): void {
  const retained = new Set(calls.map((call) => call.id));
  for (const [id, node] of callNodes) {
    if (!retained.has(id)) {
      node.remove();
      callNodes.delete(id);
    }
  }
  calls.forEach((call, index) => {
    let node = callNodes.get(call.id);
    if (!node) {
      node = callTemplate.content.firstElementChild!.cloneNode(true) as HTMLDetailsElement;
      callNodes.set(call.id, node);
    }
    if (feed.children[index] !== node) feed.insertBefore(node, feed.children[index] ?? null);
    node.hidden = view === "error" && call.state !== "error";
    required(".call-name", node).textContent =
      call.method === "tools/call" && call.parameters.name ? call.parameters.name : call.method;
    const status = required<HTMLElement>(".call-state", node);
    status.textContent = stateLabels[call.state];
    status.dataset.state = call.state;
    const time = required<HTMLTimeElement>("time", node);
    const timestamp = new Date(call.started_at_ms);
    time.dateTime = timestamp.toISOString();
    time.textContent = clock.format(timestamp);
    const duration = call.duration_ms;
    required(".duration", node).textContent = duration === null ? "–" :
      duration < 1000 ? `${duration.toFixed(0)} ms` : `${(duration / 1000).toFixed(1)} s`;
    required(".parameters-label", node).textContent = `${call.method} · Parameters`;
    required(".parameters", node).textContent = JSON.stringify(call.parameters, null, 2);
    required(".output-label", node).textContent = call.error === null ? "Result" : "Error";
    required(".output", node).textContent = call.error !== null || call.result !== null
      ? JSON.stringify(call.error ?? call.result, null, 2) : call.state === "received" ? "Notification received." : "Waiting for result.";
    const progressWrap = required<HTMLElement>(".progress-wrap", node);
    progressWrap.hidden = call.progress === null;
    if (call.progress) {
      const progress = required<HTMLProgressElement>("progress", progressWrap);
      progress.max = call.progress.total;
      progress.value = call.progress.done;
      progress.setAttribute("aria-label", call.progress.message);
      required("span", progressWrap).textContent = `${call.progress.done} / ${call.progress.total} · ${call.progress.message}`;
    }
  });
  const empty = required<HTMLParagraphElement>("#empty-log");
  empty.hidden = calls.some((call) => view === "all" || call.state === "error");
  empty.textContent = view === "error" ? "No errors in the current call log." : "Waiting for an MCP client to connect.";
}

function newApproval(approval: Approval): HTMLElement {
  const entry = document.createElement("article");
  entry.className = "approval";
  const title = document.createElement("strong");
  title.textContent = approval.asset_id;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Approve";
  const code = document.createElement("input");
  code.readOnly = true;
  code.hidden = true;
  code.setAttribute("aria-label", `Confirmation code for ${approval.asset_id}`);
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.textContent = "Copy code";
  copyButton.hidden = true;
  copyButton.addEventListener("click", () => void copy(code.value, "Confirmation code"));
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const result = await post<Approval & { confirmation_code: string }>(`approvals/${approval.approval_id}/approve`);
      code.value = result.confirmation_code;
      code.hidden = copyButton.hidden = false;
      showFeedback(`Approved ${approval.asset_id}. Give the code to the MCP client within ${result.expires_in_seconds} seconds.`);
    } catch (error) {
      showFeedback(String(error), true);
    } finally {
      button.disabled = false;
    }
  });
  entry.append(title, button, code, copyButton);
  return entry;
}

function renderApprovals(approvals: Approval[]): void {
  const pending = new Set(approvals.map((approval) => approval.approval_id));
  for (const [id, node] of approvalNodes) {
    if (!pending.has(id)) {
      node.remove();
      approvalNodes.delete(id);
    }
  }
  for (const approval of approvals) {
    if (!approvalNodes.has(approval.approval_id)) {
      const node = newApproval(approval);
      approvalNodes.set(approval.approval_id, node);
      approvalList.append(node);
    }
  }
  required<HTMLParagraphElement>("#empty-approvals").hidden = approvals.length > 0;
}

function render(snapshot: Snapshot): void {
  lastSnapshot = snapshot;
  required("#total-assets").textContent = String(snapshot.catalog.total);
  const counts = snapshot.catalog.by_type;
  required("#asset-types").textContent = `${counts.image} images · ${counts.video} video · ${counts.audio} audio`;
  required("#active-jobs").textContent = String(snapshot.debug.active_jobs);
  failNext.checked = snapshot.debug.fail_next;
  slowTools.checked = snapshot.debug.slow_tools;
  renderCalls(snapshot.calls);
  renderApprovals(snapshot.approvals);
}

function setConnection(label: string, live: boolean): void {
  required("span", online).textContent = label;
  online.dataset.live = String(live);
}

for (const button of document.querySelectorAll<HTMLButtonElement>("[data-filter]")) {
  button.addEventListener("click", () => {
    view = button.dataset.filter === "error" ? "error" : "all";
    document.querySelectorAll("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    renderCalls(lastSnapshot?.calls ?? []);
  });
}

function bindToggle(input: HTMLInputElement, name: "fail_next" | "slow_tools"): void {
  input.addEventListener("change", async () => {
    input.disabled = true;
    try {
      await post(`debug/${name}/${input.checked ? "on" : "off"}`);
    } catch (error) {
      input.checked = lastSnapshot?.debug[name] ?? false;
      showFeedback(String(error), true);
    } finally {
      input.disabled = false;
    }
  });
}

bindToggle(failNext, "fail_next");
bindToggle(slowTools, "slow_tools");
resetButton.addEventListener("click", async () => {
  resetButton.disabled = true;
  try {
    const result = await post<Snapshot["catalog"]>("debug/reset");
    showFeedback(`Restored ${result.total} sample assets.`);
  } catch (error) {
    showFeedback(String(error), true);
  } finally {
    resetButton.disabled = false;
  }
});

mcpUrl.value = new URL("./mcp", location.href).href;
required("#copy-url").addEventListener("click", () => void copy(mcpUrl.value, "MCP endpoint"));
const events = new EventSource(new URL("./api/events", location.href));
events.onopen = () => setConnection("Live", true);
events.onerror = () => setConnection("Reconnecting", false);
events.addEventListener("snapshot", (event: MessageEvent<string>) => render(JSON.parse(event.data) as Snapshot));
