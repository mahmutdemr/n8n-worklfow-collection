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

const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat("en-US");
const percentNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const pageSize = 30;
let currentOffset = 0;
let nodes = [];
let iconBaseUrl = "";

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
  const key = fields.get("key");
  const usage = fields.get("usage");
  const capability = fields.get("capability");
  const minWorkflows = Number(fields.get("min_workflows"));
  const minInstances = Number(fields.get("min_instances"));
  if (category && !node.categories.includes(category)) return false;
  if (group && !node.groups.includes(group)) return false;
  if (packageName && node.packageName !== packageName) return false;
  if (key && !node.keys.includes(key)) return false;
  if (usage === "used" && node.usage.instanceCount === 0) return false;
  if (usage === "unused" && node.usage.instanceCount > 0) return false;
  if (capability === "tool" && !node.usableAsTool) return false;
  if (capability === "credentials" && !node.credentials.length) return false;
  if (capability === "hidden" && !node.hidden) return false;
  if (minWorkflows && node.usage.workflowCount < minWorkflows) return false;
  if (minInstances && node.usage.instanceCount < minInstances) return false;
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
    card.querySelector(".node-package").textContent = node.packageName;
    card.querySelector(".node-rank").textContent = node.usage.workflowCount
      ? `#${fullNumber.format(node.usage.workflowRank)} by workflow reach`
      : "Not used in this collection";
    card.querySelector("h2").textContent = node.displayName || node.name;
    card.querySelector(".node-type").textContent = node.type;
    card.querySelector(".node-description").textContent = node.description || "No description is available for this node.";

    const icon = card.querySelector(".node-icon");
    const image = icon.querySelector("img");
    const fallback = icon.querySelector("span");
    if (["n8n-design-system", "fontawesome", "fallback"].includes(node.icon?.source)) {
      icon.classList.add("monochrome");
    }
    fallback.textContent = (node.displayName || node.name || "?").trim().slice(0, 2).toUpperCase();
    const sources = iconSources(node.icon);
    image.dataset.light = sources.light;
    image.dataset.dark = sources.dark;
    const source = sources[document.documentElement.dataset.theme] || sources.light || sources.dark;
    if (source) {
      image.src = source;
      image.hidden = false;
      fallback.hidden = true;
      image.addEventListener("error", () => { image.hidden = true; fallback.hidden = false; }, { once: true });
    }

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

form.addEventListener("submit", runSearch);
document.querySelector("#clear").addEventListener("click", () => { form.reset(); runSearch(); query.focus(); });
previousPage.addEventListener("click", () => runSearch(undefined, Math.max(0, currentOffset - pageSize)));
nextPage.addEventListener("click", () => runSearch(undefined, currentOffset + pageSize));
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== query && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    query.focus();
  }
});

fetch(document.body.dataset.indexUrl)
  .then((response) => {
    if (!response.ok) throw new Error("The node search index could not be loaded.");
    return response.json();
  })
  .then((index) => {
    iconBaseUrl = index.iconBaseUrl;
    nodes = index.nodes;
    addCountedOptions(document.querySelector("#category"), nodes.flatMap((node) => node.categories));
    addCountedOptions(document.querySelector("#group"), nodes.flatMap((node) => node.groups));
    addCountedOptions(document.querySelector("#package"), nodes.map((node) => node.packageName));
    const keySelect = document.querySelector("#source-key");
    for (const item of index.potentialKeys) {
      const option = document.createElement("option");
      option.value = item.key;
      option.textContent = `${item.key} (${fullNumber.format(item.itemCount)} definitions)`;
      keySelect.append(option);
    }
    const summary = index.summary;
    status.textContent = `${fullNumber.format(nodes.length)} node types indexed · ${fullNumber.format(summary.usedNodeTypeCount)} used in workflows · map generated ${new Date(index.generatedAt).toLocaleDateString("en-US")}`;
    runSearch();
  })
  .catch((error) => {
    status.textContent = error.message;
    resultSummary.textContent = error.message;
  });
