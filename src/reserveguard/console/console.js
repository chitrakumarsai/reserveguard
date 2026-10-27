// ReserveGuard ops console. Plain DOM, no framework. Every value from the API is written
// with textContent, never as HTML. The API token lives in sessionStorage for this tab only.
"use strict";

const TOKEN_KEY = "reserveguard.token";
const CAP_BPS = 4000;
const ARM_TIMEOUT_MS = 5000;
const DEFAULT_GROSS_USD = { A: 3000, B: 3000, C: 4000 };
const KIND_LABELS = {
  base: "Tier base rate",
  complaint: "Complaint",
  complaint_cluster: "Complaint cluster",
  tracking_failure: "Tracking failure",
  delivery_confirmed: "Delivery confirmed",
};

const $ = (id) => document.getElementById(id);
const state = { token: null, sellers: [], selected: null, samples: null, armTimer: null };

// ---------- formatting ----------
const usdFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const usd = (cents) => usdFormat.format(cents / 100);
const pct = (bps) => `${(bps / 100).toFixed(2)}%`;

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  const { text, className, dataset, attrs } = props;
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  Object.assign(node.dataset, dataset || {});
  for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
  for (const child of children) node.append(child);
  return node;
}

function setText(id, value) {
  const node = $(id);
  if (node.textContent !== value) {
    node.textContent = value;
    node.dataset.changed = "true";
    setTimeout(() => delete node.dataset.changed, 400);
  }
}

function toast(message, tone = "info") {
  const node = $("toast");
  node.textContent = message;
  node.dataset.tone = tone;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (node.hidden = true), tone === "bad" ? 8000 : 4500);
}

// ---------- API ----------
class ApiError extends Error {}

function describeError(status, body) {
  const detail = body && body.detail;
  if (detail && typeof detail === "object" && detail.error === "payment_not_settled") {
    const moved = detail.ledger_applied ? "Recorded in the ledger; " : "Not recorded; ";
    return `Payment ${detail.payment_id} is ${detail.status}. ${moved}safe to retry.`;
  }
  if (Array.isArray(detail)) return `Invalid input: ${detail.map((d) => d.msg).join("; ")}`;
  if (typeof detail === "string") return detail;
  return `Request failed (${status})`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${state.token}`, "Content-Type": "application/json" },
  });
  const body = await response.json().catch(() => null);
  if (response.status === 401) {
    signOut("Your token was not accepted.");
    throw new ApiError("Not signed in");
  }
  if (!response.ok) throw new ApiError(describeError(response.status, body));
  return body;
}

const optional = (promise) => promise.catch(() => null);

// ---------- sign-in ----------
function signOut(message = "") {
  sessionStorage.removeItem(TOKEN_KEY);
  state.token = null;
  $("token-error").textContent = message;
  if (!$("token-dialog").open) $("token-dialog").showModal();
}

async function connect(token) {
  state.token = token;
  const info = await api("/info");
  sessionStorage.setItem(TOKEN_KEY, token);
  const live = info.airwallex === "sandbox";
  $("env-airwallex").textContent = live ? "Airwallex sandbox · live" : "Simulated Airwallex";
  $("env-airwallex").dataset.tone = live ? "ok" : "muted";
  $("env-classifier").textContent = info.llm_model ? `Claude · ${info.llm_model}` : "Keyword rules";
  $("env-classifier").dataset.tone = info.llm_model ? "held" : "muted";
  state.samples = await optional(api("/samples/evidence"));
  await refreshAll();
}

// ---------- sellers ----------
async function refreshAll() {
  state.sellers = await api("/sellers");
  if (!state.selected && state.sellers.length) state.selected = state.sellers.at(-1).seller_id;
  renderSellers();
  await Promise.all([renderSeller(), renderChecks()]);
}

function renderSellers() {
  const items = state.sellers.map((s) => {
    const fill = el("span");
    fill.style.transform = `scaleX(${Math.min(s.rate_bps / CAP_BPS, 1)})`;
    const button = el("button", { className: "seller", attrs: { type: "button" } }, [
      el("span", { className: "seller-row" }, [
        el("strong", { text: s.name }),
        el("span", { className: "mono", text: pct(s.rate_bps) }),
      ]),
      el("span", { className: "meter", dataset: { tone: s.rate_bps > 2000 ? "risk" : "" } }, [fill]),
      el("span", { className: "seller-row seller-meta" }, [
        el("span", { text: `${s.tier} tier` }),
        el("span", { className: "mono", text: `${usd(s.held_cents)} held` }),
      ]),
    ]);
    button.setAttribute("aria-current", String(s.seller_id === state.selected));
    button.addEventListener("click", () => selectSeller(s.seller_id));
    return el("li", {}, [button]);
  });
  $("seller-list").replaceChildren(...items);
}

async function selectSeller(sellerId) {
  state.selected = sellerId;
  renderSellers();
  await renderSeller();
}

async function renderSeller() {
  const seller = state.sellers.find((s) => s.seller_id === state.selected);
  if (!seller) return;
  const id = encodeURIComponent(seller.seller_id);
  const [statement, why, pending] = await Promise.all([
    api(`/sellers/${id}/statement`),
    api(`/sellers/${id}/explain`),
    optional(api(`/sellers/${id}/pending`)),
  ]);
  $("seller-name").textContent = seller.name;
  $("seller-tier").textContent = `${seller.tier} tier`;
  $("seller-tier").dataset.tone = seller.tier === "new" ? "risk" : "held";
  setText("kpi-rate", pct(statement.rate_bps));
  setText("kpi-held", usd(statement.held_cents));
  setText("kpi-wallet", usd(statement.wallet_cents));
  setText("kpi-debt", usd(statement.debt_cents));
  $("seller-account").textContent = statement.account_id;
  renderWhy(why);
  renderApproval(pending);
  renderLedger(statement.entries);
}

// ---------- why this rate ----------
function renderWhy(why) {
  const width = (bps) => `${(Math.max(bps, 0) / CAP_BPS) * 100}%`;
  const segments = [el("span", { className: "seg", dataset: { kind: "base" }, text: pct(why.base_bps) })];
  segments[0].style.width = width(why.base_bps);
  const counted = why.lines.filter((l) => l.status === "active" && l.weight_bps > 0);
  const cleared = why.lines.filter((l) => l.status === "cleared");
  counted.forEach((line, i) => {
    const seg = el("span", {
      className: "seg",
      dataset: { kind: i % 2 ? "risk-strong" : "risk" },
      text: `+${pct(line.weight_bps)}`,
      attrs: { title: `${KIND_LABELS[line.kind] || line.kind}: ${line.evidence_ids.join(", ")}` },
    });
    seg.style.width = width(line.weight_bps);
    seg.style.animationDelay = `${(i + 1) * 60}ms`;
    segments.push(seg);
  });
  for (const line of cleared) {
    const seg = el("span", { className: "seg", dataset: { kind: "cleared" }, text: pct(line.weight_bps) });
    seg.style.width = width(line.weight_bps);
    segments.push(seg);
  }
  $("rate-stack").replaceChildren(...segments);
  $("rate-stack").setAttribute("aria-label", `Rate ${pct(why.rate_bps)} of a ${pct(CAP_BPS)} cap`);

  const lines = [rateLine("base", why.base_bps, KIND_LABELS.base, "Set by seller tier", "active")];
  for (const line of why.lines) {
    const cites = line.evidence_ids.join(", ");
    const note = line.status === "cleared" ? `${cites} · cleared by ${line.cleared_by.join(", ")}` : cites;
    lines.push(rateLine(line.kind, line.weight_bps, KIND_LABELS[line.kind] || line.kind, note, line.status));
  }
  const total = rateLine("total", why.rate_bps, why.capped ? "Rate (capped)" : "Rate", "", "active");
  total.classList.add("total");
  lines.push(total);
  $("rate-lines").replaceChildren(...lines);

  const note = why.pending
    ? `Evidence on file supports ${pct(why.rate_bps)}; ${pct(why.current_rate_bps)} applies until approved`
    : `${why.lines.length} signal${why.lines.length === 1 ? "" : "s"} on file`;
  $("rate-note").textContent = note;
}

function rateLine(kind, bps, label, note, status) {
  const sign = kind === "base" || kind === "total" ? "" : "+";
  return el("li", { className: "rate-line", dataset: { status } }, [
    el("span", { className: "bps", text: `${sign}${pct(bps)}` }),
    el("span", { className: "what" }, [el("span", { text: label }), el("span", { className: "ids", text: note ? ` · ${note}` : "" })]),
    el("span", { className: "chip", dataset: { tone: status === "cleared" ? "release" : kind === "base" ? "held" : kind === "total" ? "muted" : "risk" }, text: status === "clears" ? "clears" : status === "cleared" ? "cleared" : kind === "total" ? "total" : "counted" }),
  ]);
}

// ---------- approval slip ----------
function slipAction(plan) {
  if (plan.charge_cents > 0) return { action: "charge", cents: plan.charge_cents, label: "Charge seller" };
  if (plan.release_cents > 0) return { action: "release", cents: plan.release_cents, label: "Release to seller" };
  if (plan.debt_cents > 0) return { action: "charge", cents: plan.debt_cents, label: "Record debt" };
  return { action: "release", cents: 0, label: "Rate change only" };
}

function renderApproval(pending) {
  disarm();
  $("approval").hidden = !pending;
  if (!pending) return;
  const { action, cents, label } = slipAction(pending.plan);
  const platform = "Platform account";
  $("approval").dataset.action = action;
  $("slip-action").textContent = label;
  $("slip-value").textContent = usd(cents);
  $("slip-from").textContent = action === "charge" ? pending.account_id : platform;
  $("slip-to").textContent = action === "charge" ? platform : pending.account_id;
  $("slip-wallet").textContent = usd(pending.wallet_cents);
  $("slip-target").textContent = `${usd(pending.plan.held_cents)} → ${usd(pending.plan.target_cents)}`;
  $("slip-memo").textContent = pending.memo;
  $("approve").textContent = cents ? `Approve ${usd(cents)} ${action}` : "Approve rate change";
  $("approve").dataset.cents = String(cents);
  $("approve-hint").textContent = "Two clicks: approve, then confirm.";
}

function disarm() {
  clearTimeout(state.armTimer);
  const button = $("approve");
  if (button.dataset.armed === "true") {
    button.dataset.armed = "false";
    button.textContent = button.dataset.label || button.textContent;
  }
}

async function onApprove() {
  const button = $("approve");
  if (button.dataset.armed !== "true") {
    button.dataset.label = button.textContent;
    button.dataset.armed = "true";
    button.textContent = `Confirm: move ${usd(Number(button.dataset.cents))}`;
    state.armTimer = setTimeout(disarm, ARM_TIMEOUT_MS);
    return;
  }
  disarm();
  await busy(button, "Waiting for Airwallex to settle…", async () => {
    const result = await api(`/sellers/${encodeURIComponent(state.selected)}/approve`, { method: "POST" });
    const { cents, action } = slipAction(result.plan);
    toast(cents ? `${usd(cents)} ${action} settled on Airwallex.` : "Rate change applied.");
    await refreshAll();
  });
}

// ---------- evidence ----------
function loadSample(kind) {
  if (!state.samples) return toast("Samples are unavailable.", "bad");
  $("evidence-input").value = JSON.stringify(state.samples[kind], null, 2);
  $("evidence-input").focus();
}

function readEvidence() {
  const raw = $("evidence-input").value.trim();
  if (!raw) throw new ApiError("Paste evidence or load a sample first.");
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new ApiError("Evidence must be valid JSON.");
  }
  return Array.isArray(parsed) ? parsed : parsed.evidence;
}

async function onAssess() {
  await busy($("assess"), "Reading evidence…", async () => {
    const evidence = readEvidence();
    const id = encodeURIComponent(state.selected);
    const result = await api(`/sellers/${id}/assess`, { method: "POST", body: JSON.stringify({ evidence }) });
    const d = result.decision;
    const by = result.classifier === "claude" ? "Claude" : "keyword rules";
    const next = d.needs_approval ? "waiting for your approval" : "applied";
    toast(`${pct(d.old_rate_bps)} → ${pct(d.new_rate_bps)} (signals by ${by}), ${next}.`);
    await refreshAll();
  });
}

// ---------- ledger ----------
function renderLedger(entries) {
  if (!entries.length) {
    const empty = el("td", { className: "empty", text: "No money has moved for this seller yet.", attrs: { colspan: "4" } });
    $("ledger-rows").replaceChildren(el("tr", {}, [empty]));
    return;
  }
  const rows = entries.map((e) =>
    el("tr", {}, [
      el("td", { className: "kind", text: e.kind.replaceAll("_", " ") }),
      el("td", { className: "num", text: usd(e.amount_cents) }),
      el("td", { className: "ref", text: e.reference }),
      el("td", { className: "pid", text: e.object_id || "—" }),
    ]),
  );
  $("ledger-rows").replaceChildren(...rows);
}

// ---------- reconciliation ----------
const CHECK_LABELS = {
  seller_identity: (seller) => `Seller ${seller}: books balance`,
  tenant_isolation: (seller) => `Seller ${seller}: only its own money`,
  platform_balance: () => "Platform balance matches the ledger",
};

function checkLabel(name) {
  const [kind, seller] = name.split(":");
  const label = CHECK_LABELS[kind];
  return label ? label(seller) : name.replaceAll("_", " ");
}

// Check details report amounts as "name=cents"; show them as dollars.
const checkDetail = (detail) => detail.replace(/=(-?\d+)/g, (_, cents) => ` ${usd(Number(cents))}`);

async function renderChecks() {
  const checks = await api("/reconciliation");
  const failed = checks.filter((c) => !c.passed).length;
  $("checks-summary").textContent = failed ? `${failed} failing` : `${checks.length} passing`;
  $("checks-summary").dataset.tone = failed ? "bad" : "ok";
  $("check-list").replaceChildren(
    ...checks.map((c) =>
      el("li", { className: "check", dataset: { passed: String(c.passed) } }, [
        el("span", { className: "check-dot", attrs: { "aria-hidden": "true" } }),
        el("span", {}, [
          el("span", { className: "check-name", text: checkLabel(c.name) }),
          el("span", { className: "check-detail", text: checkDetail(c.detail) }),
        ]),
      ]),
    ),
  );
}

// ---------- settlement ----------
function openSettle() {
  const fields = state.sellers.flatMap((s) => {
    const id = `gross-${s.seller_id}`;
    const input = el("input", { attrs: { id, type: "number", min: "0", step: "0.01", inputmode: "decimal" } });
    input.value = String(DEFAULT_GROSS_USD[s.seller_id] ?? 1000);
    input.dataset.seller = s.seller_id;
    return [el("label", { text: `${s.name}: gross sales (USD)`, attrs: { for: id } }), input];
  });
  $("settle-fields").replaceChildren(...fields);
  $("settle-dialog").showModal();
}

async function onSettle(event) {
  event.preventDefault();
  const gross = {};
  for (const input of $("settle-fields").querySelectorAll("input")) {
    gross[input.dataset.seller] = Math.round(Number(input.value) * 100);
  }
  $("settle-dialog").close();
  const cycleId = `console-${new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14)}`;
  await busy($("settle-open"), "Paying out…", async () => {
    const lines = await api("/settle", { method: "POST", body: JSON.stringify({ cycle_id: cycleId, gross_cents: gross }) });
    const paid = lines.reduce((sum, l) => sum + l.payout_cents, 0);
    toast(`Cycle ${cycleId}: ${usd(paid)} paid out to ${lines.length} sellers, all settled.`);
    await refreshAll();
  });
}

// ---------- plumbing ----------
async function busy(button, label, work) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    await work();
  } catch (error) {
    if (!(error instanceof ApiError && error.message === "Not signed in")) toast(error.message, "bad");
  } finally {
    button.disabled = false;
    if (button.textContent === label) button.textContent = original;
  }
}

function wire() {
  $("token-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = $("token-input").value.trim();
    try {
      await connect(token);
      $("token-input").value = "";
      $("token-dialog").close();
    } catch (error) {
      $("token-error").textContent = error.message;
      if (!$("token-dialog").open) $("token-dialog").showModal();
    }
  });
  $("token-dialog").addEventListener("cancel", (event) => event.preventDefault());
  $("lock").addEventListener("click", () => signOut());
  $("approve").addEventListener("click", onApprove);
  $("assess").addEventListener("click", onAssess);
  $("load-risk").addEventListener("click", () => loadSample("risk"));
  $("load-deliveries").addEventListener("click", () => loadSample("deliveries"));
  $("refresh-checks").addEventListener("click", () => busy($("refresh-checks"), "Checking…", renderChecks));
  $("settle-open").addEventListener("click", openSettle);
  $("settle-cancel").addEventListener("click", () => $("settle-dialog").close());
  $("settle-form").addEventListener("submit", onSettle);
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  const saved = sessionStorage.getItem(TOKEN_KEY);
  if (!saved) return $("token-dialog").showModal();
  connect(saved).catch(() => signOut("Please sign in again."));
});
