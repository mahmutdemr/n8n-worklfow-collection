const form = document.querySelector("#search-form");
const query = document.querySelector("#query");
const resultList = document.querySelector("#result-list");
const resultSummary = document.querySelector("#result-summary");
const loading = document.querySelector("#loading");
const status = document.querySelector("#index-status");
const template = document.querySelector("#result-template");
const resultsSection = document.querySelector(".results");
const pagination = document.querySelector("#pagination");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const pageStatus = document.querySelector("#page-status");
const themeSelect = document.querySelector("#theme-select");
const themeIcon = document.querySelector("#theme-icon");
const sourceKeyFilter = document.querySelector("#source-key-filter");
const sourceKeyOptions = document.querySelector("#source-key-options");
const sourceKeySummary = document.querySelector("#source-key-summary");
const sourceKeyHint = document.querySelector("#source-key-hint");
const clearSourceKeys = document.querySelector("#clear-source-keys");
const capabilityFilter = document.querySelector("#capability-filter");
const capabilityOptions = document.querySelector("#capability-options");
const capabilitySummary = document.querySelector("#capability-summary");
const clearCapabilities = document.querySelector("#clear-capabilities");
const detailDrawer = document.querySelector("#node-detail-drawer");
const detailBackdrop = document.querySelector("#detail-backdrop");
const detailClose = document.querySelector("#detail-close");
const rawJsonSection = document.querySelector("#raw-json-section");
const jsonFilter = document.querySelector("#json-filter");
const jsonTree = document.querySelector("#json-tree");
const jsonStatus = document.querySelector("#json-status");
const jsonExpand = document.querySelector("#json-expand");
const jsonCollapse = document.querySelector("#json-collapse");
const jsonCopy = document.querySelector("#json-copy");

const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat("en-US");
const percentNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const pageSize = 30;
let currentOffset = 0;
let nodes = [];
let iconBaseUrl = "";
let detailBaseUrl = "";
let selectedNode = null;
let selectedRawJson = null;
let lastFocusedElement = null;
let detailRequestId = 0;
let detailCloseTimer = null;
const rawJsonCache = new Map();

const themeStorageKey = "n8n-workflow-theme";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

function savedThemePreference() {
  try { return localStorage.getItem(themeStorageKey) || "system"; } catch { return "system"; }
}

function resolvedTheme(preference) {
  return preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
}

function updateNodeIcons(theme) {
  for (const image of document.querySelectorAll(".node-icon img")) {
    const source = image.dataset[theme] || image.dataset.light || image.dataset.dark;
    if (source && image.src !== source) image.src = source;
  }
}

function applyTheme(preference) {
  const resolved = resolvedTheme(preference);
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "light" ? "#f4f7f5" : "#10151d";
  themeIcon.textContent = preference === "system" ? "◐" : (resolved === "light" ? "☀" : "☾");
  updateNodeIcons(resolved);
}

themeSelect.value = savedThemePreference();
applyTheme(themeSelect.value);
themeSelect.addEventListener("change", () => {
  try { localStorage.setItem(themeStorageKey, themeSelect.value); } catch { /* Preference remains session-only. */ }
  applyTheme(themeSelect.value);
});
systemTheme.addEventListener("change", () => { if (savedThemePreference() === "system") applyTheme("system"); });

function normalize(value) {
  return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function searchScore(node, terms, mode) {
  if (!terms.length) return 0;
  const fields = [
    [normalize(node.displayName), 12], [normalize(node.type), 7], [normalize(node.name), 5],
    [normalize(node.description), 4], [normalize(node.packageName), 3],
    [normalize(node.categories.join(" ")), 3], [normalize(node.groups.join(" ")), 2],
    [normalize(node.credentials.join(" ")), 2], [normalize(node.keys.join(" ")), 2],
    [normalize(node.availableVersions.join(" ")), 1],
  ];
  let score = 0;
  let matches = 0;
  for (const term of terms) {
    let termScore = 0;
    for (const [text, weight] of fields) if (text.includes(term)) termScore += weight;
    if (termScore) matches += 1;
    score += termScore;
  }
  return mode === "all" && matches !== terms.length ? -1 : score;
}

function matchesFilters(node, fields) {
  const category = fields.get("category");
  const group = fields.get("group");
  const packageName = fields.get("package");
  const keys = fields.getAll("key").filter(Boolean);
  const keyMode = fields.get("key_mode") || "include";
  const usage = fields.get("usage");
  const capabilities = fields.getAll("capability").filter(Boolean);
  const minWorkflows = Number(fields.get("min_workflows"));
  const maxWorkflowsValue = fields.get("max_workflows");
  const maxWorkflows = maxWorkflowsValue === "" ? null : Number(maxWorkflowsValue);
  if (category && !node.categories.includes(category)) return false;
  if (group && !node.groups.includes(group)) return false;
  if (packageName && node.packageName !== packageName) return false;
  if (keys.length) {
    const containsSelectedKey = keys.some((key) => node.keys.includes(key));
    if (keyMode === "include" && !containsSelectedKey) return false;
    if (keyMode === "exclude" && containsSelectedKey) return false;
  }
  if (usage === "used" && node.usage.instanceCount === 0) return false;
  if (usage === "unused" && node.usage.instanceCount > 0) return false;
  if (capabilities.length) {
    const hasSelectedCapability = capabilities.some((capability) => (
      (capability === "tool" && node.usableAsTool)
      || (capability === "credentials" && node.credentials.length > 0)
      || (capability === "hidden" && node.hidden)
    ));
    if (!hasSelectedCapability) return false;
  }
  if (minWorkflows && node.usage.workflowCount < minWorkflows) return false;
  if (maxWorkflows !== null && node.usage.workflowCount > maxWorkflows) return false;
  return true;
}

function createChip(text, className = "") {
  const chip = document.createElement("span");
  chip.className = `chip ${className}`.trim();
  chip.textContent = text;
  return chip;
}

function iconSources(icon) {
  if (!icon || typeof icon !== "object") return { light: "", dark: "" };
  const base = new URL(iconBaseUrl, document.baseURI);
  return {
    light: icon.light ? new URL(icon.light, base).href : "",
    dark: icon.dark ? new URL(icon.dark, base).href : "",
  };
}

function configureIcon(container, node) {
  const image = container.querySelector("img");
  const fallback = container.querySelector("span");
  container.classList.toggle("monochrome", ["n8n-design-system", "fontawesome", "fallback"].includes(node.icon?.source));
  fallback.textContent = (node.displayName || node.name || "?").trim().slice(0, 2).toUpperCase();
  const sources = iconSources(node.icon);
  image.dataset.light = sources.light;
  image.dataset.dark = sources.dark;
  const source = sources[document.documentElement.dataset.theme] || sources.light || sources.dark;
  image.hidden = !source;
  fallback.hidden = Boolean(source);
  if (source) {
    image.src = source;
    image.addEventListener("error", () => { image.hidden = true; fallback.hidden = false; }, { once: true });
  }
}

function appendDetailMetric(container, value, label) {
  const metric = document.createElement("div");
  const strong = document.createElement("strong");
  const span = document.createElement("span");
  strong.textContent = value;
  span.textContent = label;
  metric.append(strong, span);
  container.append(metric);
}

function appendMetadataRow(container, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  if (Array.isArray(value)) {
    if (!value.length) description.textContent = "None";
    else for (const item of value) description.append(createChip(String(item)));
  } else {
    description.textContent = String(value || "None");
  }
  row.append(term, description);
  container.append(row);
}

function updateDetailUrl(nodeType, replace = false) {
  const url = new URL(window.location.href);
  if (nodeType) url.searchParams.set("node", nodeType);
  else url.searchParams.delete("node");
  history[replace ? "replaceState" : "pushState"]({ nodeType: nodeType || null }, "", url);
}

function openNodeDetails(node, { updateUrl = true } = {}) {
  if (!node) return;
  window.clearTimeout(detailCloseTimer);
  selectedNode = node;
  selectedRawJson = null;
  detailRequestId += 1;
  lastFocusedElement = document.activeElement;
  document.querySelector("#detail-title").textContent = node.displayName || node.name;
  document.querySelector("#detail-type").textContent = node.type;
  document.querySelector("#detail-description").textContent = node.description || "No description is available for this node.";
  configureIcon(document.querySelector(".detail-icon"), node);

  const chips = document.querySelector("#detail-chips");
  chips.replaceChildren();
  for (const category of node.categories) chips.append(createChip(category));
  for (const group of node.groups) chips.append(createChip(group, "group-chip"));
  if (node.usableAsTool) chips.append(createChip("Usable as AI tool", "tool-chip"));
  if (node.hidden) chips.append(createChip("Hidden", "hidden-chip"));

  document.querySelector("#detail-rank").textContent = node.usage.workflowCount
    ? `#${fullNumber.format(node.usage.workflowRank)} by workflow reach`
    : "Not used in this collection";
  const metrics = document.querySelector("#detail-metrics");
  metrics.replaceChildren();
  appendDetailMetric(metrics, fullNumber.format(node.usage.workflowCount), "workflows");
  appendDetailMetric(metrics, fullNumber.format(node.usage.instanceCount), "instances");
  appendDetailMetric(metrics, `${percentNumber.format(node.usage.workflowPercentage)}%`, "workflow reach");
  appendDetailMetric(metrics, percentNumber.format(node.usage.averageInstancesPerUsingWorkflow), "avg. per workflow");
  appendDetailMetric(metrics, fullNumber.format(node.usage.enabledInstanceCount), "enabled instances");
  appendDetailMetric(metrics, fullNumber.format(node.usage.disabledInstanceCount), "disabled instances");

  const versionUsage = document.querySelector("#version-usage");
  versionUsage.replaceChildren();
  if (node.usage.versions.length) {
    const heading = document.createElement("h4");
    heading.textContent = "Observed workflow versions";
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>Version</th><th>Workflows</th><th>Instances</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const version of node.usage.versions) {
      const row = document.createElement("tr");
      for (const value of [version.version, fullNumber.format(version.workflowCount), fullNumber.format(version.instanceCount)]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
    table.append(body);
    versionUsage.append(heading, table);
  }

  const metadata = document.querySelector("#detail-metadata");
  metadata.replaceChildren();
  appendMetadataRow(metadata, "Package", node.packageName);
  appendMetadataRow(metadata, "Internal name", node.name);
  appendMetadataRow(metadata, "Catalog definitions", fullNumber.format(node.definitionCount));
  appendMetadataRow(metadata, "Available versions", node.availableVersions);
  appendMetadataRow(metadata, "Credentials", node.credentials);
  appendMetadataRow(metadata, "Source keys", node.keys);

  const documentation = document.querySelector("#detail-documentation");
  documentation.hidden = !node.documentationUrls.length;
  if (node.documentationUrls.length) documentation.href = node.documentationUrls[0];

  rawJsonSection.open = false;
  jsonFilter.value = "";
  jsonTree.replaceChildren();
  jsonStatus.hidden = false;
  jsonStatus.textContent = "Open this section to load the source definition.";
  detailBackdrop.hidden = false;
  detailDrawer.hidden = false;
  document.body.classList.add("detail-open");
  requestAnimationFrame(() => {
    detailBackdrop.classList.add("visible");
    detailDrawer.classList.add("visible");
    detailClose.focus();
  });
  if (updateUrl) updateDetailUrl(node.type);
}

function closeNodeDetails({ updateUrl = true } = {}) {
  if (detailDrawer.hidden) return;
  detailRequestId += 1;
  selectedNode = null;
  selectedRawJson = null;
  detailBackdrop.classList.remove("visible");
  detailDrawer.classList.remove("visible");
  document.body.classList.remove("detail-open");
  detailCloseTimer = window.setTimeout(() => {
    detailBackdrop.hidden = true;
    detailDrawer.hidden = true;
  }, 180);
  if (updateUrl) updateDetailUrl(null);
  if (lastFocusedElement?.isConnected) lastFocusedElement.focus();
}

function jsonValueText(value) {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  return String(value);
}

function jsonContains(value, key, queryText) {
  if (!queryText) return true;
  if (normalize(key).includes(queryText)) return true;
  if (value !== null && typeof value === "object") {
    return Object.entries(value).some(([childKey, childValue]) => jsonContains(childValue, childKey, queryText));
  }
  return normalize(jsonValueText(value)).includes(queryText);
}

function createJsonNode(value, key, depth, queryText = "", includeChildren = false) {
  const keyMatches = Boolean(queryText && normalize(key).includes(queryText));
  if (queryText && !includeChildren && !jsonContains(value, key, queryText)) return null;
  const branch = value !== null && typeof value === "object";
  if (!branch) {
    const row = document.createElement("div");
    row.className = `json-leaf json-${value === null ? "null" : typeof value}`;
    const keyLabel = document.createElement("span");
    keyLabel.className = "json-key";
    keyLabel.textContent = key === null ? "" : `${key}: `;
    const valueLabel = document.createElement("span");
    valueLabel.className = "json-value";
    valueLabel.textContent = jsonValueText(value);
    row.append(keyLabel, valueLabel);
    return row;
  }

  const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item]) : Object.entries(value);
  const details = document.createElement("details");
  details.className = "json-branch";
  const summary = document.createElement("summary");
  const keyLabel = document.createElement("span");
  keyLabel.className = "json-key";
  keyLabel.textContent = key === null ? "root" : key;
  const count = document.createElement("span");
  count.className = "json-count";
  count.textContent = `${Array.isArray(value) ? "[" : "{"}${fullNumber.format(entries.length)}${Array.isArray(value) ? "]" : "}"}`;
  summary.append(keyLabel, count);
  const children = document.createElement("div");
  children.className = "json-children";
  const populate = () => {
    if (details.dataset.loaded === "true") return;
    const fragment = document.createDocumentFragment();
    const showAllChildren = includeChildren || keyMatches;
    for (const [childKey, childValue] of entries) {
      const child = createJsonNode(childValue, childKey, depth + 1, queryText, showAllChildren);
      if (child) fragment.append(child);
    }
    children.append(fragment);
    details.dataset.loaded = "true";
  };
  details._populateJsonChildren = populate;
  details.addEventListener("toggle", () => { if (details.open) populate(); });
  details.append(summary, children);
  if ((queryText && !includeChildren) || depth < 1) {
    details.open = true;
    populate();
  }
  return details;
}

function renderRawJson() {
  jsonTree.replaceChildren();
  if (!selectedRawJson) return;
  const filterText = normalize(jsonFilter.value.trim());
  if (filterText && !jsonContains(selectedRawJson, null, filterText)) {
    jsonStatus.hidden = false;
    jsonStatus.textContent = `No raw JSON keys or values match “${jsonFilter.value.trim()}”.`;
    return;
  }
  jsonStatus.hidden = true;
  jsonTree.append(createJsonNode(selectedRawJson, null, 0, filterText));
}

async function loadRawJson() {
  if (!selectedNode || selectedRawJson) return;
  const requestId = ++detailRequestId;
  jsonStatus.hidden = false;
  jsonStatus.textContent = "Loading source definition…";
  try {
    if (!rawJsonCache.has(selectedNode.detailId)) {
      const url = new URL(selectedNode.detailId, new URL(detailBaseUrl, document.baseURI));
      rawJsonCache.set(selectedNode.detailId, fetch(url).then((response) => {
        if (!response.ok) throw new Error("The raw node definition could not be loaded.");
        return response.json();
      }));
    }
    const payload = await rawJsonCache.get(selectedNode.detailId);
    if (requestId !== detailRequestId || !selectedNode) return;
    selectedRawJson = payload;
    renderRawJson();
  } catch (error) {
    rawJsonCache.delete(selectedNode?.detailId);
    if (requestId !== detailRequestId) return;
    jsonStatus.hidden = false;
    jsonStatus.textContent = error.message;
  }
}

function setJsonBranches(open) {
  const visit = (root) => {
    for (const branch of root.querySelectorAll(":scope > .json-branch")) {
      if (open) {
        branch._populateJsonChildren?.();
        branch.open = true;
        visit(branch.querySelector(":scope > .json-children"));
      } else {
        if (branch.dataset.loaded === "true") visit(branch.querySelector(":scope > .json-children"));
        branch.open = false;
      }
    }
  };
  visit(jsonTree);
}

function renderResults(results, total, offset) {
  resultList.replaceChildren();
  if (!results.length) {
    resultSummary.textContent = "No nodes matched. Try fewer filters or clear the search.";
    pagination.hidden = true;
    return;
  }
  const start = offset + 1;
  const end = Math.min(offset + results.length, total);
  resultSummary.textContent = `${fullNumber.format(total)} matching node${total === 1 ? "" : "s"} · Showing ${fullNumber.format(start)}–${fullNumber.format(end)}`;
  const fragment = document.createDocumentFragment();

  for (const node of results) {
    const card = template.content.cloneNode(true);
    const article = card.querySelector(".node-card");
    card.querySelector(".node-package").textContent = node.packageName;
    card.querySelector(".node-rank").textContent = node.usage.workflowCount
      ? `#${fullNumber.format(node.usage.workflowRank)} by workflow reach`
      : "Not used in this collection";
    card.querySelector("h2").textContent = node.displayName || node.name;
    card.querySelector(".node-type").textContent = node.type;
    card.querySelector(".node-description").textContent = node.description || "No description is available for this node.";

    configureIcon(card.querySelector(".node-icon"), node);

    card.querySelector(".workflow-count").textContent = compactNumber.format(node.usage.workflowCount);
    card.querySelector(".instance-count").textContent = compactNumber.format(node.usage.instanceCount);
    card.querySelector(".workflow-share").textContent = `${percentNumber.format(node.usage.workflowPercentage)}%`;

    const chips = card.querySelector(".chips");
    for (const category of node.categories.slice(0, 3)) chips.append(createChip(category));
    for (const group of node.groups.slice(0, 2)) chips.append(createChip(group, "group-chip"));
    if (node.usableAsTool) chips.append(createChip("AI tool", "tool-chip"));
    if (node.hidden) chips.append(createChip("Hidden", "hidden-chip"));

    const versionCount = node.availableVersions.length;
    const credentialCount = node.credentials.length;
    const keyCount = node.keys.length;
    card.querySelector(".node-facts").textContent =
      `${versionCount} version${versionCount === 1 ? "" : "s"} · ${credentialCount} credential${credentialCount === 1 ? "" : "s"} · ${keyCount} source keys`;

    card.querySelector(".versions").textContent = node.availableVersions.join(", ") || "Unknown";
    card.querySelector(".credentials").textContent = node.credentials.join(", ") || "None";
    card.querySelector(".source-keys").textContent = node.keys.join(", ") || "None";

    const documentation = card.querySelector(".documentation");
    if (node.documentationUrls.length) documentation.href = node.documentationUrls[0];
    else documentation.hidden = true;
    card.querySelector(".view-details").addEventListener("click", () => openNodeDetails(node));
    article.addEventListener("click", (event) => {
      if (!event.target.closest("a, button, details")) openNodeDetails(node);
    });
    fragment.append(card);
  }

  resultList.append(fragment);
  pagination.hidden = total <= pageSize;
  previousPage.disabled = offset === 0;
  nextPage.disabled = offset + pageSize >= total;
  pageStatus.textContent = `Page ${Math.floor(offset / pageSize) + 1} of ${Math.ceil(total / pageSize)}`;
}

function runSearch(event, offset = 0) {
  event?.preventDefault();
  if (event?.type === "submit") {
    sourceKeyFilter.open = false;
    capabilityFilter.open = false;
  }
  const fields = new FormData(form);
  const terms = normalize(fields.get("q")).match(/[\p{L}\p{N}_]+/gu) || [];
  resultsSection.setAttribute("aria-busy", "true");
  loading.hidden = false;
  window.setTimeout(() => {
    const ranked = nodes
      .map((node) => ({ node, score: searchScore(node, terms, fields.get("mode")) }))
      .filter(({ node, score }) => score >= 0 && matchesFilters(node, fields));
    const sort = fields.get("sort");
    ranked.sort((left, right) => {
      if (sort === "name") return (left.node.displayName || left.node.name).localeCompare(right.node.displayName || right.node.name);
      if (sort === "instances") return right.node.usage.instanceCount - left.node.usage.instanceCount || right.node.usage.workflowCount - left.node.usage.workflowCount;
      if (sort === "rank" && terms.length) return right.score - left.score || right.node.usage.workflowCount - left.node.usage.workflowCount;
      return right.node.usage.workflowCount - left.node.usage.workflowCount || right.node.usage.instanceCount - left.node.usage.instanceCount;
    });
    currentOffset = offset;
    renderResults(ranked.slice(offset, offset + pageSize).map(({ node }) => node), ranked.length, offset);
    loading.hidden = true;
    resultsSection.setAttribute("aria-busy", "false");
  }, 0);
}

function addCountedOptions(select, values, suffix = "nodes") {
  const counts = new Map();
  for (const value of values) counts.set(value, (counts.get(value) || 0) + 1);
  for (const [value, count] of [...counts].sort(([left], [right]) => left.localeCompare(right))) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = `${value} (${fullNumber.format(count)} ${suffix})`;
    select.append(option);
  }
}

function updateSourceKeySummary() {
  const selected = [...sourceKeyOptions.querySelectorAll('input[name="key"]:checked')].map((input) => input.value);
  const excludes = form.elements.key_mode.value === "exclude";
  if (!selected.length) sourceKeySummary.textContent = "All source keys";
  else if (selected.length <= 2) sourceKeySummary.textContent = `${excludes ? "Without: " : ""}${selected.join(", ")}`;
  else sourceKeySummary.textContent = `${excludes ? "Exclude" : "Include"} ${selected.length} keys`;
  sourceKeySummary.title = `${excludes && selected.length ? "Without: " : ""}${selected.join(", ")}`;
  sourceKeyHint.textContent = excludes
    ? "Show nodes containing none of the selected keys"
    : "Show nodes containing any selected key";
}

function updateCapabilitySummary() {
  const selected = [...capabilityOptions.querySelectorAll('input[name="capability"]:checked')];
  const labels = selected.map((input) => input.dataset.label);
  if (!labels.length) capabilitySummary.textContent = "All capabilities";
  else if (labels.length === 1) capabilitySummary.textContent = labels[0];
  else capabilitySummary.textContent = `${labels.length} capabilities selected`;
  capabilitySummary.title = labels.join(", ");
}

function closeOnEscape(filter, event) {
  if (event.key === "Escape" && filter.open) {
    filter.open = false;
    filter.querySelector("summary").focus();
  }
}

function appendCheckboxOption(container, name, value, labelText, count, countSuffix) {
  const option = document.createElement("label");
  option.className = "multi-select-option";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = name;
  input.value = value;
  input.dataset.label = labelText;
  const label = document.createElement("span");
  label.textContent = labelText;
  const countLabel = document.createElement("small");
  countLabel.textContent = `${fullNumber.format(count)} ${countSuffix}`;
  option.append(input, label, countLabel);
  container.append(option);
}

form.addEventListener("submit", runSearch);
document.querySelector("#clear").addEventListener("click", () => { form.reset(); runSearch(); query.focus(); });
form.addEventListener("reset", () => window.setTimeout(() => {
  updateSourceKeySummary();
  updateCapabilitySummary();
}, 0));
sourceKeyFilter.addEventListener("change", updateSourceKeySummary);
clearSourceKeys.addEventListener("click", () => {
  for (const input of sourceKeyOptions.querySelectorAll('input[name="key"]:checked')) input.checked = false;
  updateSourceKeySummary();
});
capabilityOptions.addEventListener("change", updateCapabilitySummary);
clearCapabilities.addEventListener("click", () => {
  for (const input of capabilityOptions.querySelectorAll('input[name="capability"]:checked')) input.checked = false;
  updateCapabilitySummary();
});
sourceKeyFilter.addEventListener("keydown", (event) => closeOnEscape(sourceKeyFilter, event));
capabilityFilter.addEventListener("keydown", (event) => closeOnEscape(capabilityFilter, event));
previousPage.addEventListener("click", () => runSearch(undefined, Math.max(0, currentOffset - pageSize)));
nextPage.addEventListener("click", () => runSearch(undefined, currentOffset + pageSize));
detailClose.addEventListener("click", () => closeNodeDetails());
detailBackdrop.addEventListener("click", () => closeNodeDetails());
rawJsonSection.addEventListener("toggle", () => { if (rawJsonSection.open) loadRawJson(); });
jsonFilter.addEventListener("input", renderRawJson);
jsonExpand.addEventListener("click", () => setJsonBranches(true));
jsonCollapse.addEventListener("click", () => setJsonBranches(false));
jsonCopy.addEventListener("click", async () => {
  if (!selectedRawJson) return;
  const originalText = jsonCopy.textContent;
  try {
    await navigator.clipboard.writeText(JSON.stringify(selectedRawJson, null, 2));
    jsonCopy.textContent = "Copied";
  } catch {
    jsonCopy.textContent = "Copy failed";
  }
  window.setTimeout(() => { jsonCopy.textContent = originalText; }, 1400);
});
document.addEventListener("keydown", (event) => {
  if (!detailDrawer.hidden && event.key === "Escape") {
    event.preventDefault();
    closeNodeDetails();
    return;
  }
  if (!detailDrawer.hidden && event.key === "Tab") {
    const focusable = [...detailDrawer.querySelectorAll('button:not([disabled]), a[href]:not([hidden]), input:not([disabled]), summary')]
      .filter((element) => element.offsetParent !== null);
    if (focusable.length) {
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    return;
  }
  if (event.key === "/" && document.activeElement !== query && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    query.focus();
  }
});
window.addEventListener("popstate", () => {
  const nodeType = new URL(window.location.href).searchParams.get("node");
  if (!nodeType) closeNodeDetails({ updateUrl: false });
  else openNodeDetails(nodes.find((node) => node.type === nodeType), { updateUrl: false });
});
document.addEventListener("click", (event) => {
  for (const filter of document.querySelectorAll(".multi-select[open]")) {
    if (!filter.contains(event.target)) filter.open = false;
  }
});

fetch(document.body.dataset.indexUrl)
  .then((response) => {
    if (!response.ok) throw new Error("The node search index could not be loaded.");
    return response.json();
  })
  .then((index) => {
    iconBaseUrl = index.iconBaseUrl;
    detailBaseUrl = index.detailBaseUrl;
    nodes = index.nodes;
    addCountedOptions(document.querySelector("#category"), nodes.flatMap((node) => node.categories));
    addCountedOptions(document.querySelector("#group"), nodes.flatMap((node) => node.groups));
    addCountedOptions(document.querySelector("#package"), nodes.map((node) => node.packageName));
    for (const item of index.potentialKeys) {
      appendCheckboxOption(sourceKeyOptions, "key", item.key, item.key, item.itemCount, "defs");
    }
    appendCheckboxOption(capabilityOptions, "capability", "tool", "Usable as AI tool", nodes.filter((node) => node.usableAsTool).length, "nodes");
    appendCheckboxOption(capabilityOptions, "capability", "credentials", "Requires credentials", nodes.filter((node) => node.credentials.length).length, "nodes");
    appendCheckboxOption(capabilityOptions, "capability", "hidden", "Hidden nodes", nodes.filter((node) => node.hidden).length, "nodes");
    const summary = index.summary;
    status.textContent = `${fullNumber.format(nodes.length)} node types indexed · ${fullNumber.format(summary.usedNodeTypeCount)} used in workflows · map generated ${new Date(index.generatedAt).toLocaleDateString("en-US")}`;
    runSearch();
    const requestedNodeType = new URL(window.location.href).searchParams.get("node");
    if (requestedNodeType) {
      const requestedNode = nodes.find((node) => node.type === requestedNodeType);
      if (requestedNode) openNodeDetails(requestedNode, { updateUrl: false });
      else updateDetailUrl(null, true);
    }
  })
  .catch((error) => {
    status.textContent = error.message;
    resultSummary.textContent = error.message;
  });
