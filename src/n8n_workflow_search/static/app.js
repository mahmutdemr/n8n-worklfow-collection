const form = document.querySelector("#search-form");
const query = document.querySelector("#query");
const resultList = document.querySelector("#result-list");
const resultSummary = document.querySelector("#result-summary");
const loading = document.querySelector("#loading");
const status = document.querySelector("#index-status");
const category = document.querySelector("#category");
const template = document.querySelector("#result-template");
const resultsSection = document.querySelector(".results");
const pagination = document.querySelector("#pagination");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const pageStatus = document.querySelector("#page-status");
const themeSelect = document.querySelector("#theme-select");

const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat("en-US");
const pageSize = 30;
let currentOffset = 0;
let currentTotal = 0;

const themeStorageKey = "n8n-workflow-theme";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

function savedThemePreference() {
  try { return localStorage.getItem(themeStorageKey) || "system"; } catch { return "system"; }
}

function applyTheme(preference) {
  const resolved = preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "light" ? "#f4f7f5" : "#10151d";
}

themeSelect.value = savedThemePreference();
applyTheme(themeSelect.value);
themeSelect.addEventListener("change", () => {
  try { localStorage.setItem(themeStorageKey, themeSelect.value); } catch { /* Preference remains session-only. */ }
  applyTheme(themeSelect.value);
});
systemTheme.addEventListener("change", () => { if (savedThemePreference() === "system") applyTheme("system"); });

function createChip(text) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = text;
  return chip;
}

function renderResults(results, total, offset, limit) {
  resultList.replaceChildren();
  if (!results.length) {
    resultSummary.textContent = "No workflows matched. Try fewer filters or clear the search.";
    pagination.hidden = true;
    return;
  }
  const start = offset + 1;
  const end = Math.min(offset + results.length, total);
  resultSummary.textContent = `${fullNumber.format(total)} matching workflow${total === 1 ? "" : "s"} · Showing ${fullNumber.format(start)}–${fullNumber.format(end)}`;
  const fragment = document.createDocumentFragment();
  for (const workflow of results) {
    const card = template.content.cloneNode(true);
    card.querySelector(".workflow-id").textContent = `#${workflow.id}`;
    card.querySelector(".views").textContent = `${workflow.node_count} nodes · ${compactNumber.format(workflow.views)} views`;
    card.querySelector("h2").textContent = workflow.name;
    card.querySelector(".meta").textContent = workflow.creator_name || workflow.creator_username || "Unknown creator";
    const compatibility = card.querySelector(".compatibility");
    if (workflow.default_compatible === 1) {
      compatibility.textContent = "Default nodes";
      compatibility.classList.add("compatible");
    } else if (workflow.default_compatible === 0) {
      const count = workflow.missing_node_type_count;
      compatibility.textContent = `Needs ${count} unavailable node type${count === 1 ? "" : "s"}`;
      compatibility.title = JSON.parse(workflow.missing_node_types).join("\n");
      compatibility.classList.add("incompatible");
    } else {
      compatibility.hidden = true;
    }
    const chips = card.querySelector(".chips");
    for (const category of workflow.categories.split(", ").filter(Boolean).slice(0, 4)) chips.append(createChip(category));
    const gallery = card.querySelector(".gallery");
    gallery.href = workflow.gallery_url;
    const copy = card.querySelector(".copy-path");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(workflow.local_file);
        copy.textContent = "Copied";
        window.setTimeout(() => { copy.textContent = "Copy local path"; }, 1400);
      } catch {
        copy.textContent = workflow.local_file;
      }
    });
    fragment.append(card);
  }
  resultList.append(fragment);
  pagination.hidden = total <= limit;
  previousPage.disabled = offset === 0;
  nextPage.disabled = offset + limit >= total;
  pageStatus.textContent = `Page ${Math.floor(offset / limit) + 1} of ${Math.ceil(total / limit)}`;
}

async function submitSearch(event, offset = 0) {
  event?.preventDefault();
  const parameters = new URLSearchParams(new FormData(form));
  const createdWithin = Number(parameters.get("created_within"));
  parameters.delete("created_within");
  if (createdWithin) {
    const boundary = new Date();
    boundary.setUTCDate(boundary.getUTCDate() - createdWithin);
    parameters.set("created_after", boundary.toISOString().slice(0, 10));
  }
  parameters.set("limit", String(pageSize));
  parameters.set("offset", String(offset));
  resultsSection.setAttribute("aria-busy", "true");
  loading.hidden = false;
  try {
    const response = await fetch(`/api/search?${parameters}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Search could not be completed.");
    currentOffset = payload.offset;
    currentTotal = payload.total;
    renderResults(payload.results, payload.total, payload.offset, payload.limit);
  } catch (error) {
    resultList.replaceChildren();
    resultSummary.textContent = error.message;
    pagination.hidden = true;
  } finally {
    loading.hidden = true;
    resultsSection.setAttribute("aria-busy", "false");
  }
}

form.addEventListener("submit", submitSearch);
document.querySelector("#clear").addEventListener("click", () => {
  form.reset();
  submitSearch();
  query.focus();
});
previousPage.addEventListener("click", () => submitSearch(undefined, Math.max(0, currentOffset - pageSize)));
nextPage.addEventListener("click", () => submitSearch(undefined, currentOffset + pageSize));
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== query && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    query.focus();
  }
});

fetch("/api/stats")
  .then((response) => response.json())
  .then((data) => { status.textContent = `${fullNumber.format(Number(data.indexed_workflows))} workflows indexed · map generated ${new Date(data.map_generated_at).toLocaleDateString("en-US")}`; })
  .catch(() => { status.textContent = "Search index is unavailable. Build it, then restart this page."; });

fetch("/api/categories")
  .then((response) => response.json())
  .then((data) => {
    for (const item of data.categories) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.label} (${fullNumber.format(item.workflow_count)})${item.parent_name ? ` · ${item.parent_name}` : ""}`;
      category.append(option);
    }
  })
  .catch(() => { category.disabled = true; });

submitSearch();
