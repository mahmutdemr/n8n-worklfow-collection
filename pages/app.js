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

const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat("en-US");
const pageSize = 30;
let currentOffset = 0;
let workflows = [];
let categoryById = new Map();

function normalize(value) {
  return String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function categoryLabels(workflow) {
  return workflow.categoryIds.map((id) => categoryById.get(id)?.label).filter(Boolean);
}

function searchScore(workflow, terms, mode) {
  if (!terms.length) return 0;
  const fields = [
    [normalize(workflow.name), 10], [normalize(workflow.slug), 4],
    [normalize(`${workflow.creatorName} ${workflow.creatorUsername}`), 2],
    [normalize(categoryLabels(workflow).join(" ")), 1], [normalize(workflow.description), 2],
    [normalize(workflow.missingNodeTypes.join(" ")), 1], [normalize(workflow.missingNodePackages.join(" ")), 1],
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

function matchesFilters(workflow, fields, terms) {
  const minViews = Number(fields.get("min_views"));
  const minNodes = Number(fields.get("min_nodes"));
  const maxNodes = Number(fields.get("max_nodes"));
  const createdWithin = Number(fields.get("created_within"));
  if (fields.get("category_id") && !workflow.categoryIds.includes(Number(fields.get("category_id")))) return false;
  if (fields.get("creator") && !normalize(`${workflow.creatorName} ${workflow.creatorUsername}`).includes(normalize(fields.get("creator")))) return false;
  if (minViews && workflow.views < minViews) return false;
  if (minNodes && workflow.nodeCount < minNodes) return false;
  if (maxNodes && workflow.nodeCount > maxNodes) return false;
  if (fields.get("default_compatible") === "true" && workflow.defaultCompatible !== true) return false;
  if (fields.get("default_compatible") === "false" && workflow.defaultCompatible !== false) return false;
  if (createdWithin) {
    const boundary = new Date();
    boundary.setUTCDate(boundary.getUTCDate() - createdWithin);
    if (!workflow.createdAt || new Date(workflow.createdAt) < boundary) return false;
  }
  return searchScore(workflow, terms, fields.get("mode")) >= 0;
}

function createChip(text) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = text;
  return chip;
}

function renderResults(results, total, offset) {
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
    card.querySelector(".views").textContent = `${workflow.nodeCount} nodes · ${compactNumber.format(workflow.views)} views`;
    card.querySelector("h2").textContent = workflow.name;
    card.querySelector(".meta").textContent = workflow.creatorName || workflow.creatorUsername || "Unknown creator";
    const compatibility = card.querySelector(".compatibility");
    if (workflow.defaultCompatible === true) {
      compatibility.textContent = "Default nodes";
      compatibility.classList.add("compatible");
    } else if (workflow.defaultCompatible === false) {
      const count = workflow.missingNodeTypeCount;
      compatibility.textContent = `Needs ${count} unavailable node type${count === 1 ? "" : "s"}`;
      compatibility.title = workflow.missingNodeTypes.join("\n");
      compatibility.classList.add("incompatible");
    } else compatibility.hidden = true;
    const chips = card.querySelector(".chips");
    for (const label of categoryLabels(workflow).slice(0, 4)) chips.append(createChip(label));
    const gallery = card.querySelector(".gallery");
    gallery.href = workflow.galleryUrl;
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
    const ranked = workflows
      .map((workflow) => ({ workflow, score: searchScore(workflow, terms, fields.get("mode")) }))
      .filter(({ workflow, score }) => score >= 0 && matchesFilters(workflow, fields, terms));
    const sort = fields.get("sort");
    ranked.sort((left, right) => {
      if (sort === "nodes") return right.workflow.nodeCount - left.workflow.nodeCount || right.workflow.views - left.workflow.views;
      if (sort === "views" || !terms.length) return right.workflow.views - left.workflow.views || right.workflow.nodeCount - left.workflow.nodeCount;
      return right.score - left.score || right.workflow.views - left.workflow.views;
    });
    currentOffset = offset;
    renderResults(ranked.slice(offset, offset + pageSize).map(({ workflow }) => workflow), ranked.length, offset);
    loading.hidden = true;
    resultsSection.setAttribute("aria-busy", "false");
  }, 0);
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

fetch("search-index.json")
  .then((response) => { if (!response.ok) throw new Error("The public search index could not be loaded."); return response.json(); })
  .then((index) => {
    workflows = index.workflows;
    categoryById = new Map(index.categories.map((item) => [item.id, item]));
    for (const item of index.categories) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.label} (${fullNumber.format(item.workflowCount)})${item.parentName ? ` · ${item.parentName}` : ""}`;
      category.append(option);
    }
    status.textContent = `${fullNumber.format(workflows.length)} workflows indexed · map generated ${new Date(index.generatedAt).toLocaleDateString("en-US")}`;
    runSearch();
  })
  .catch((error) => { status.textContent = error.message; resultSummary.textContent = error.message; });
