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
const themeIcon = document.querySelector("#theme-icon");
const includedNodeFilter = document.querySelector("#included-node-filter");
const includedNodeOptions = document.querySelector("#included-node-options");
const includedNodeSummary = document.querySelector("#included-node-summary");
const includedNodeSearch = document.querySelector("#included-node-search");
const excludedNodeFilter = document.querySelector("#excluded-node-filter");
const excludedNodeOptions = document.querySelector("#excluded-node-options");
const excludedNodeSummary = document.querySelector("#excluded-node-summary");
const excludedNodeSearch = document.querySelector("#excluded-node-search");
const workflowDetailDrawer = document.querySelector("#workflow-detail-drawer");
const workflowDetailBackdrop = document.querySelector("#workflow-detail-backdrop");
const workflowDetailClose = document.querySelector("#workflow-detail-close");
const workflowDiagramStatus = document.querySelector("#workflow-diagram-status");
const workflowDiagramViewport = document.querySelector("#workflow-diagram-viewport");
const workflowDiagram = document.querySelector("#workflow-diagram");
const diagramZoom = document.querySelector("#diagram-zoom");
const mermaidSourceDetails = document.querySelector("#mermaid-source-details");
const mermaidSource = document.querySelector("#mermaid-source");
const workflowNodeInventoryDetails = document.querySelector("#workflow-node-inventory-details");
const workflowNodeInventory = document.querySelector("#workflow-node-inventory");

const compactNumber = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat("en-US");
const pageSize = 30;
let currentOffset = 0;
let workflows = [];
let categoryById = new Map();
let selectedWorkflow = null;
let selectedMermaidSource = "";
let workflowDetailRequestId = 0;
let workflowDiagramRenderId = 0;
let workflowDetailCloseTimer = null;
let workflowDetailLastFocus = null;
let diagramScale = 1;
let diagramBaseSize = { width: 0, height: 0 };
let mermaidModulePromise = null;
let mermaidBucketCount = 0;
let mermaidBaseUrl = "";
let mermaidDownloadObjectUrl = "";
const mermaidBucketCache = new Map();
let workflowNodeCatalogPromise = null;
const mermaidModuleUrl = "https://cdn.jsdelivr.net/npm/mermaid@11.16.0/dist/mermaid.esm.min.mjs";

const themeStorageKey = "n8n-workflow-theme";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

function savedThemePreference() {
  try { return localStorage.getItem(themeStorageKey) || "system"; } catch { return "system"; }
}

function applyTheme(preference) {
  const resolved = preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
  document.documentElement.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "light" ? "#f4f7f5" : "#10151d";
  themeIcon.textContent = preference === "system" ? "◐" : (resolved === "light" ? "☀" : "☾");
  updateWorkflowInventoryIcons(resolved);
  if (selectedMermaidSource && !workflowDetailDrawer.hidden) void renderWorkflowDiagram(selectedMermaidSource);
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

function categoryLabels(workflow) {
  return workflow.categoryIds.map((id) => categoryById.get(id)?.label).filter(Boolean);
}

function searchScore(workflow, terms, mode) {
  if (!terms.length) return 0;
  const fields = [
    [normalize(workflow.name), 10], [normalize(workflow.slug), 4],
    [normalize(`${workflow.creatorName} ${workflow.creatorUsername}`), 2],
    [normalize(categoryLabels(workflow).join(" ")), 1], [normalize(workflow.description), 2],
    [normalize(workflow.nodeTypes.join(" ")), 1],
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
  const includedNodes = fields.getAll("include_node").filter(Boolean);
  const excludedNodes = fields.getAll("exclude_node").filter(Boolean);
  if (fields.get("category_id") && !workflow.categoryIds.includes(Number(fields.get("category_id")))) return false;
  if (fields.get("creator") && !normalize(`${workflow.creatorName} ${workflow.creatorUsername}`).includes(normalize(fields.get("creator")))) return false;
  if (minViews && workflow.views < minViews) return false;
  if (minNodes && workflow.nodeCount < minNodes) return false;
  if (maxNodes && workflow.nodeCount > maxNodes) return false;
  if (fields.get("default_compatible") === "true" && workflow.defaultCompatible !== true) return false;
  if (fields.get("default_compatible") === "false" && workflow.defaultCompatible !== false) return false;
  if (includedNodes.some((nodeType) => !workflow.nodeTypes.includes(nodeType))) return false;
  if (excludedNodes.some((nodeType) => workflow.nodeTypes.includes(nodeType))) return false;
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

function workflowDetailData(workflow) {
  return {
    id: workflow.id,
    name: workflow.name,
    description: workflow.description,
    views: workflow.views,
    nodeCount: workflow.nodeCount,
    createdAt: workflow.createdAt,
    creator: workflow.creatorName || workflow.creatorUsername || "Unknown creator",
    categories: categoryLabels(workflow),
    nodeTypes: workflow.nodeTypes,
    defaultCompatible: workflow.defaultCompatible,
    missingNodeTypes: workflow.missingNodeTypes,
    missingNodeTypeCount: workflow.missingNodeTypeCount,
    missingNodeInstanceCount: workflow.missingNodeInstanceCount,
    galleryUrl: workflow.galleryUrl,
    mermaidAvailable: workflow.mermaidAvailable === true,
    mermaidError: workflow.mermaidError,
  };
}

async function fetchWorkflowMermaidSource(workflow) {
  if (!mermaidBucketCount || !mermaidBaseUrl) throw new Error("Mermaid bucket configuration is unavailable.");
  const bucket = workflow.id % mermaidBucketCount;
  const width = Math.max(2, String(mermaidBucketCount - 1).length);
  const filename = `${String(bucket).padStart(width, "0")}.json`;
  if (!mermaidBucketCache.has(filename)) {
    const url = new URL(filename, mermaidBaseUrl);
    mermaidBucketCache.set(filename, fetch(url).then(async (response) => {
      if (!response.ok) throw new Error("The Mermaid bucket could not be loaded.");
      return response.json();
    }).catch((error) => {
      mermaidBucketCache.delete(filename);
      throw error;
    }));
  }
  const payload = await mermaidBucketCache.get(filename);
  const source = payload?.[String(workflow.id)];
  if (typeof source !== "string") throw new Error("The workflow is missing from its Mermaid bucket.");
  return source;
}

function loadWorkflowNodeCatalog() {
  if (!workflowNodeCatalogPromise) {
    const indexUrl = new URL("node-search-index.json", document.baseURI);
    const iconBaseUrl = new URL("node-icons/", document.baseURI);
    workflowNodeCatalogPromise = fetch(indexUrl).then(async (response) => {
      if (!response.ok) throw new Error("The node icon catalog could not be loaded.");
      const index = await response.json();
      return new Map(index.nodes.map((node) => [node.type, {
        displayName: node.displayName || node.name,
        icon: {
          light: node.icon?.light ? new URL(node.icon.light, iconBaseUrl).href : "",
          dark: node.icon?.dark ? new URL(node.icon.dark, iconBaseUrl).href : "",
          source: node.icon?.source || "",
        },
      }]));
    }).catch((error) => {
      workflowNodeCatalogPromise = null;
      throw error;
    });
  }
  return workflowNodeCatalogPromise;
}

function readableNodeType(nodeType) {
  const value = String(nodeType || "").split(".").at(-1) || "Node";
  return value.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[-_]+/g, " ");
}

function nodeInitials(label) {
  const words = String(label || "Node").trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  return (words[0] || "N").slice(0, 2).toUpperCase();
}

function updateWorkflowInventoryIcons(theme) {
  for (const image of workflowNodeInventory?.querySelectorAll(".workflow-inventory-icon img") || []) {
    const source = image.dataset[theme] || image.dataset.light || image.dataset.dark;
    if (source && image.src !== source) image.src = source;
  }
}

function nodeDetailsUrl(nodeType) {
  const url = new URL("nodes/", document.baseURI);
  url.searchParams.set("node", nodeType);
  return url.href;
}

function renderWorkflowNodeInventory(workflow) {
  workflowNodeInventory.replaceChildren();
  const missing = new Set(workflow.missingNodeTypes);
  for (const nodeType of workflow.nodeTypes) {
    const isMissing = missing.has(nodeType);
    const item = document.createElement(isMissing ? "div" : "a");
    item.className = "workflow-inventory-node";
    item.dataset.nodeType = nodeType;
    if (isMissing) {
      item.classList.add("missing");
    } else {
      item.href = nodeDetailsUrl(nodeType);
      item.target = "_blank";
      item.rel = "noopener";
      item.title = "Open node details in a new tab";
    }

    const icon = document.createElement("div");
    icon.className = "workflow-inventory-icon";
    const image = document.createElement("img");
    image.alt = "";
    image.hidden = true;
    const fallback = document.createElement("span");
    const fallbackLabel = readableNodeType(nodeType);
    fallback.textContent = nodeInitials(fallbackLabel);
    icon.append(image, fallback);

    const text = document.createElement("div");
    text.className = "workflow-inventory-text";
    const label = document.createElement("strong");
    label.textContent = fallbackLabel;
    const type = document.createElement("code");
    type.textContent = nodeType;
    text.append(label, type);
    item.append(icon, text);

    const status = document.createElement("small");
    status.textContent = isMissing ? "Unavailable" : "Details ↗";
    item.append(status);
    workflowNodeInventory.append(item);
  }
}

async function hydrateWorkflowNodeInventory() {
  if (!selectedWorkflow || !workflowNodeInventoryDetails.open) return;
  const workflowId = selectedWorkflow.id;
  try {
    const catalog = await loadWorkflowNodeCatalog();
    if (!selectedWorkflow || selectedWorkflow.id !== workflowId || !workflowNodeInventoryDetails.open) return;
    for (const item of workflowNodeInventory.querySelectorAll(".workflow-inventory-node")) {
      const node = catalog.get(item.dataset.nodeType);
      if (!node) continue;
      item.querySelector(".workflow-inventory-text strong").textContent = node.displayName;
      const icon = item.querySelector(".workflow-inventory-icon");
      const image = icon.querySelector("img");
      const fallback = icon.querySelector("span");
      fallback.textContent = nodeInitials(node.displayName);
      image.dataset.light = node.icon.light;
      image.dataset.dark = node.icon.dark;
      icon.classList.toggle("monochrome", ["n8n-design-system", "fontawesome", "fallback"].includes(node.icon.source));
      const theme = document.documentElement.dataset.theme;
      const source = image.dataset[theme] || image.dataset.light || image.dataset.dark;
      if (source) {
        image.src = source;
        image.hidden = false;
        fallback.hidden = true;
        image.addEventListener("error", () => {
          image.hidden = true;
          fallback.hidden = false;
        }, { once: true });
      }
    }
  } catch {
    // Text labels and generated initials remain available when the icon catalog cannot load.
  }
}

function appendWorkflowMetric(container, value, label) {
  const metric = document.createElement("div");
  const strong = document.createElement("strong");
  const span = document.createElement("span");
  strong.textContent = value;
  span.textContent = label;
  metric.append(strong, span);
  container.append(metric);
}

function appendWorkflowMetadata(container, label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value || "Unknown";
  row.append(term, description);
  container.append(row);
}

function formattedWorkflowDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(date);
}

function updateWorkflowDetailUrl(workflowId, replace = false) {
  const url = new URL(window.location.href);
  if (workflowId) url.searchParams.set("workflow", String(workflowId));
  else url.searchParams.delete("workflow");
  history[replace ? "replaceState" : "pushState"]({ workflowId: workflowId || null }, "", url);
}

function setWorkflowDiagramError(message) {
  workflowDiagram.replaceChildren();
  workflowDiagramViewport.hidden = true;
  diagramZoom.hidden = true;
  workflowDiagramStatus.hidden = false;
  workflowDiagramStatus.classList.add("error");
  workflowDiagramStatus.textContent = message;
}

function applyDiagramScale(scale) {
  diagramScale = Math.min(2, Math.max(0.35, scale));
  document.querySelector("#diagram-zoom-value").textContent = `${Math.round(diagramScale * 100)}%`;
  const svg = workflowDiagram.querySelector("svg");
  if (!svg || !diagramBaseSize.width || !diagramBaseSize.height) return;
  svg.style.width = `${Math.round(diagramBaseSize.width * diagramScale)}px`;
  svg.style.height = `${Math.round(diagramBaseSize.height * diagramScale)}px`;
}

async function mermaidRenderer() {
  if (!mermaidModulePromise) {
    mermaidModulePromise = import(mermaidModuleUrl).then((module) => module.default);
  }
  return mermaidModulePromise;
}

async function renderWorkflowDiagram(source) {
  const requestId = workflowDetailRequestId;
  const renderRequestId = ++workflowDiagramRenderId;
  try {
    const mermaid = await mermaidRenderer();
    if (requestId !== workflowDetailRequestId || renderRequestId !== workflowDiagramRenderId || !selectedWorkflow) return;
    const isDark = document.documentElement.dataset.theme === "dark";
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: isDark ? "dark" : "neutral",
      themeVariables: isDark ? {
        background: "#111820",
        primaryColor: "#263a30",
        primaryBorderColor: "#7ea386",
        primaryTextColor: "#eef7f0",
        secondaryColor: "#2e4738",
        tertiaryColor: "#22352a",
        lineColor: "#9dbba5",
      } : {},
      flowchart: { htmlLabels: false, useMaxWidth: false },
    });
    const renderId = `workflow-preview-${selectedWorkflow.id}-${Date.now()}`;
    const { svg } = await mermaid.render(renderId, source);
    if (requestId !== workflowDetailRequestId || renderRequestId !== workflowDiagramRenderId || !selectedWorkflow) return;
    workflowDiagram.innerHTML = svg;
    const svgElement = workflowDiagram.querySelector("svg");
    const viewBox = svgElement?.viewBox?.baseVal;
    diagramBaseSize = {
      width: Math.max(Number(viewBox?.width) || Number.parseFloat(svgElement?.getAttribute("width")) || 800, 1),
      height: Math.max(Number(viewBox?.height) || Number.parseFloat(svgElement?.getAttribute("height")) || 400, 1),
    };
    workflowDiagramStatus.hidden = true;
    workflowDiagramStatus.classList.remove("error");
    workflowDiagramViewport.hidden = false;
    diagramZoom.hidden = false;
    const availableWidth = Math.max(workflowDiagramViewport.clientWidth - 48, 320);
    applyDiagramScale(Math.min(1, Math.max(0.4, availableWidth / diagramBaseSize.width)));
    workflowDiagramViewport.scrollTo({ top: 0, left: 0 });
  } catch (error) {
    if (requestId !== workflowDetailRequestId || renderRequestId !== workflowDiagramRenderId) return;
    setWorkflowDiagramError(`Diagram rendering failed: ${error.message}`);
  }
}

async function loadWorkflowDiagram(workflow) {
  if (!workflow.mermaidAvailable) {
    setWorkflowDiagramError(workflow.mermaidError || "A diagram is not available for this workflow.");
    return;
  }
  const requestId = workflowDetailRequestId;
  workflowDiagramStatus.hidden = false;
  workflowDiagramStatus.classList.remove("error");
  workflowDiagramStatus.textContent = "Loading Mermaid preview…";
  try {
    const source = await fetchWorkflowMermaidSource(workflow);
    if (!source.startsWith("flowchart ")) throw new Error("The generated Mermaid source is invalid.");
    if (requestId !== workflowDetailRequestId || selectedWorkflow?.id !== workflow.id) return;
    selectedMermaidSource = source;
    mermaidSource.textContent = source;
    mermaidSourceDetails.hidden = false;
    const download = document.querySelector("#download-mermaid-source");
    if (mermaidDownloadObjectUrl) URL.revokeObjectURL(mermaidDownloadObjectUrl);
    mermaidDownloadObjectUrl = URL.createObjectURL(new Blob([source], { type: "text/plain;charset=utf-8" }));
    download.href = mermaidDownloadObjectUrl;
    download.download = `workflow-${workflow.id}.mmd`;
    await renderWorkflowDiagram(source);
  } catch (error) {
    if (requestId !== workflowDetailRequestId) return;
    setWorkflowDiagramError(workflow.mermaidError || error.message);
  }
}

function openWorkflowDetails(workflowRecord, { updateUrl = true } = {}) {
  if (!workflowRecord) return;
  window.clearTimeout(workflowDetailCloseTimer);
  if (workflowDetailDrawer.hidden) workflowDetailLastFocus = document.activeElement;
  const workflow = workflowDetailData(workflowRecord);
  selectedWorkflow = workflow;
  selectedMermaidSource = "";
  if (mermaidDownloadObjectUrl) URL.revokeObjectURL(mermaidDownloadObjectUrl);
  mermaidDownloadObjectUrl = "";
  workflowDetailRequestId += 1;
  diagramBaseSize = { width: 0, height: 0 };

  document.querySelector("#workflow-detail-id").textContent = `Workflow #${workflow.id}`;
  document.querySelector("#workflow-detail-title").textContent = workflow.name;
  document.querySelector("#workflow-detail-description").textContent =
    workflow.description || "No description is available for this workflow.";

  const chips = document.querySelector("#workflow-detail-chips");
  chips.replaceChildren();
  for (const categoryLabel of workflow.categories) chips.append(createChip(categoryLabel));
  const compatibility = createChip(
    workflow.defaultCompatible === true
      ? "Default nodes only"
      : workflow.defaultCompatible === false
        ? `Needs ${workflow.missingNodeTypeCount} unavailable node type${workflow.missingNodeTypeCount === 1 ? "" : "s"}`
        : "Node availability unknown"
  );
  compatibility.classList.add(workflow.defaultCompatible === true ? "detail-compatible" : "detail-incompatible");
  chips.append(compatibility);

  const metrics = document.querySelector("#workflow-detail-metrics");
  metrics.replaceChildren();
  appendWorkflowMetric(metrics, fullNumber.format(workflow.nodeCount), "nodes");
  appendWorkflowMetric(metrics, fullNumber.format(workflow.views), "views");
  appendWorkflowMetric(metrics, formattedWorkflowDate(workflow.createdAt), "created");

  const metadata = document.querySelector("#workflow-detail-metadata");
  metadata.replaceChildren();
  appendWorkflowMetadata(metadata, "Creator", workflow.creator);
  appendWorkflowMetadata(metadata, "Created", formattedWorkflowDate(workflow.createdAt));
  appendWorkflowMetadata(metadata, "Compatibility", workflow.defaultCompatible === true
    ? "Uses only installed default nodes"
    : workflow.defaultCompatible === false
      ? `${workflow.missingNodeTypeCount} unavailable types · ${workflow.missingNodeInstanceCount} instances`
      : "Unknown");
  appendWorkflowMetadata(metadata, "Gallery id", String(workflow.id));

  document.querySelector("#workflow-node-count-label").textContent =
    `${fullNumber.format(workflow.nodeTypes.length)} unique node type${workflow.nodeTypes.length === 1 ? "" : "s"}`;
  workflowNodeInventoryDetails.open = false;
  renderWorkflowNodeInventory(workflow);

  const gallery = document.querySelector("#workflow-detail-gallery");
  gallery.href = workflow.galleryUrl;
  workflowDiagram.replaceChildren();
  workflowDiagramViewport.hidden = true;
  diagramZoom.hidden = true;
  mermaidSourceDetails.hidden = true;
  mermaidSource.textContent = "";
  workflowDiagramStatus.hidden = false;
  workflowDiagramStatus.classList.remove("error");
  workflowDiagramStatus.textContent = "Loading diagram…";

  workflowDetailBackdrop.hidden = false;
  workflowDetailDrawer.hidden = false;
  document.body.classList.add("workflow-detail-open");
  requestAnimationFrame(() => {
    workflowDetailBackdrop.classList.add("visible");
    workflowDetailDrawer.classList.add("visible");
    workflowDetailClose.focus();
  });
  if (updateUrl) updateWorkflowDetailUrl(workflow.id);
  void loadWorkflowDiagram(workflow);
}

function closeWorkflowDetails({ updateUrl = true } = {}) {
  if (workflowDetailDrawer.hidden) return;
  workflowDetailRequestId += 1;
  selectedWorkflow = null;
  selectedMermaidSource = "";
  if (mermaidDownloadObjectUrl) URL.revokeObjectURL(mermaidDownloadObjectUrl);
  mermaidDownloadObjectUrl = "";
  workflowDetailBackdrop.classList.remove("visible");
  workflowDetailDrawer.classList.remove("visible");
  document.body.classList.remove("workflow-detail-open");
  workflowDetailCloseTimer = window.setTimeout(() => {
    workflowDetailBackdrop.hidden = true;
    workflowDetailDrawer.hidden = true;
  }, 180);
  if (updateUrl) updateWorkflowDetailUrl(null);
  if (workflowDetailLastFocus?.isConnected) workflowDetailLastFocus.focus();
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
    const article = card.querySelector(".result-card");
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
    card.querySelector(".view-workflow-details").addEventListener("click", () => openWorkflowDetails(workflow));
    article.addEventListener("click", (event) => {
      if (!event.target.closest("a, button")) openWorkflowDetails(workflow);
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
    includedNodeFilter.open = false;
    excludedNodeFilter.open = false;
  }
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

function appendNodeOption(container, fieldName, item) {
  const option = document.createElement("label");
  option.className = "workflow-node-option";
  option.dataset.search = normalize(`${item.label} ${item.type}`);
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = fieldName;
  input.value = item.type;
  input.dataset.label = item.label;
  const text = document.createElement("span");
  text.className = "workflow-node-option-text";
  const label = document.createElement("strong");
  label.textContent = item.label;
  const type = document.createElement("code");
  type.textContent = item.type;
  text.append(label, type);
  const count = document.createElement("small");
  count.textContent = `${compactNumber.format(item.workflowCount)} wf`;
  option.append(input, text, count);
  container.append(option);
}

function updateNodeFilterSummary(options, summary, emptyText, selectedText) {
  const selected = [...options.querySelectorAll('input[type="checkbox"]:checked')];
  if (!selected.length) summary.textContent = emptyText;
  else if (selected.length === 1) summary.textContent = selected[0].dataset.label;
  else summary.textContent = `${selected.length} ${selectedText}`;
  summary.title = selected.map((input) => input.value).join("\n");
}

function filterNodeOptions(searchInput, options) {
  const term = normalize(searchInput.value);
  for (const option of options.querySelectorAll(".workflow-node-option")) {
    option.hidden = Boolean(term) && !option.dataset.search.includes(term);
  }
}

function clearNodeSelection(options, updateSummary) {
  for (const input of options.querySelectorAll('input[type="checkbox"]:checked')) input.checked = false;
  updateSummary();
}

function reconcileNodeSelection(event, otherOptions) {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || input.type !== "checkbox" || !input.checked) return;
  const counterpart = [...otherOptions.querySelectorAll('input[type="checkbox"]')]
    .find((candidate) => candidate.value === input.value);
  if (counterpart) counterpart.checked = false;
}

function closeNodeFilterOnEscape(filter, event) {
  if (event.key === "Escape" && filter.open) {
    filter.open = false;
    filter.querySelector("summary").focus();
  }
}

const updateIncludedNodeSummary = () => updateNodeFilterSummary(
  includedNodeOptions, includedNodeSummary, "No required nodes", "required nodes"
);
const updateExcludedNodeSummary = () => updateNodeFilterSummary(
  excludedNodeOptions, excludedNodeSummary, "No excluded nodes", "excluded nodes"
);

form.addEventListener("submit", runSearch);
document.querySelector("#clear").addEventListener("click", () => { form.reset(); runSearch(); query.focus(); });
form.addEventListener("reset", () => window.setTimeout(() => {
  includedNodeSearch.value = "";
  excludedNodeSearch.value = "";
  filterNodeOptions(includedNodeSearch, includedNodeOptions);
  filterNodeOptions(excludedNodeSearch, excludedNodeOptions);
  updateIncludedNodeSummary();
  updateExcludedNodeSummary();
}, 0));
includedNodeOptions.addEventListener("change", (event) => {
  reconcileNodeSelection(event, excludedNodeOptions);
  updateIncludedNodeSummary();
  updateExcludedNodeSummary();
});
excludedNodeOptions.addEventListener("change", (event) => {
  reconcileNodeSelection(event, includedNodeOptions);
  updateIncludedNodeSummary();
  updateExcludedNodeSummary();
});
includedNodeSearch.addEventListener("input", () => filterNodeOptions(includedNodeSearch, includedNodeOptions));
excludedNodeSearch.addEventListener("input", () => filterNodeOptions(excludedNodeSearch, excludedNodeOptions));
document.querySelector("#clear-included-nodes").addEventListener("click", () => clearNodeSelection(includedNodeOptions, updateIncludedNodeSummary));
document.querySelector("#clear-excluded-nodes").addEventListener("click", () => clearNodeSelection(excludedNodeOptions, updateExcludedNodeSummary));
includedNodeFilter.addEventListener("keydown", (event) => closeNodeFilterOnEscape(includedNodeFilter, event));
excludedNodeFilter.addEventListener("keydown", (event) => closeNodeFilterOnEscape(excludedNodeFilter, event));
previousPage.addEventListener("click", () => runSearch(undefined, Math.max(0, currentOffset - pageSize)));
nextPage.addEventListener("click", () => runSearch(undefined, currentOffset + pageSize));
workflowDetailClose.addEventListener("click", () => closeWorkflowDetails());
workflowDetailBackdrop.addEventListener("click", () => closeWorkflowDetails());
workflowNodeInventoryDetails.addEventListener("toggle", () => {
  if (workflowNodeInventoryDetails.open) void hydrateWorkflowNodeInventory();
});
document.querySelector("#diagram-zoom-out").addEventListener("click", () => applyDiagramScale(diagramScale - 0.15));
document.querySelector("#diagram-zoom-in").addEventListener("click", () => applyDiagramScale(diagramScale + 0.15));
document.querySelector("#diagram-zoom-reset").addEventListener("click", () => applyDiagramScale(1));
document.querySelector("#copy-mermaid-source").addEventListener("click", async (event) => {
  if (!selectedMermaidSource) return;
  const button = event.currentTarget;
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(selectedMermaidSource);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  window.setTimeout(() => { button.textContent = original; }, 1400);
});
document.addEventListener("keydown", (event) => {
  if (workflowDetailDrawer.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeWorkflowDetails();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...workflowDetailDrawer.querySelectorAll('button:not([disabled]), a[href]:not([hidden]), summary')]
    .filter((element) => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== query && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    query.focus();
  }
});
document.addEventListener("click", (event) => {
  for (const filter of document.querySelectorAll(".workflow-node-select[open]")) {
    if (!filter.contains(event.target)) filter.open = false;
  }
});
window.addEventListener("popstate", () => {
  const workflowId = Number(new URL(window.location.href).searchParams.get("workflow"));
  if (!workflowId) closeWorkflowDetails({ updateUrl: false });
  else {
    const workflow = workflows.find((item) => item.id === workflowId);
    if (workflow) openWorkflowDetails(workflow, { updateUrl: false });
    else {
      closeWorkflowDetails({ updateUrl: false });
      updateWorkflowDetailUrl(null, true);
    }
  }
});

fetch("search-index.json")
  .then((response) => { if (!response.ok) throw new Error("The public search index could not be loaded."); return response.json(); })
  .then((index) => {
    workflows = index.workflows;
    mermaidBucketCount = Number(index.mermaid?.bucketCount) || 0;
    mermaidBaseUrl = index.mermaid?.baseUrl ? new URL(index.mermaid.baseUrl, document.baseURI).href : "";
    categoryById = new Map(index.categories.map((item) => [item.id, item]));
    for (const item of index.categories) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = `${item.label} (${fullNumber.format(item.workflowCount)})${item.parentName ? ` · ${item.parentName}` : ""}`;
      category.append(option);
    }
    for (const item of index.nodeTypes) {
      appendNodeOption(includedNodeOptions, "include_node", item);
      appendNodeOption(excludedNodeOptions, "exclude_node", item);
    }
    status.textContent = `${fullNumber.format(workflows.length)} workflows indexed · map generated ${new Date(index.generatedAt).toLocaleDateString("en-US")}`;
    runSearch();
    const requestedWorkflowId = Number(new URL(window.location.href).searchParams.get("workflow"));
    if (requestedWorkflowId) {
      const requestedWorkflow = workflows.find((workflow) => workflow.id === requestedWorkflowId);
      if (requestedWorkflow) openWorkflowDetails(requestedWorkflow, { updateUrl: false });
      else updateWorkflowDetailUrl(null, true);
    }
  })
  .catch((error) => { status.textContent = error.message; resultSummary.textContent = error.message; });
