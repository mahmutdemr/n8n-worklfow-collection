const form = document.querySelector("#search-form");
const query = document.querySelector("#query");
const resultList = document.querySelector("#result-list");
const resultSummary = document.querySelector("#result-summary");
const loading = document.querySelector("#loading");
const status = document.querySelector("#index-status");
const template = document.querySelector("#result-template");
const resultsSection = document.querySelector(".results");

const compactNumber = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat();

function createChip(text) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.textContent = text;
  return chip;
}

function renderResults(results) {
  resultList.replaceChildren();
  if (!results.length) {
    resultSummary.textContent = "Eşleşen iş akışı yok. Daha az kelimeyle arayın veya filtreleri temizleyin.";
    return;
  }
  resultSummary.textContent = `${fullNumber.format(results.length)} eşleşen iş akışı`;
  const fragment = document.createDocumentFragment();
  for (const workflow of results) {
    const card = template.content.cloneNode(true);
    card.querySelector(".workflow-id").textContent = `#${workflow.id}`;
    card.querySelector(".views").textContent = `${compactNumber.format(workflow.views)} görüntüleme`;
    card.querySelector("h2").textContent = workflow.name;
    card.querySelector(".meta").textContent = workflow.creator_name || workflow.creator_username || "Bilinmeyen oluşturan";
    const chips = card.querySelector(".chips");
    for (const category of workflow.categories.split(", ").filter(Boolean).slice(0, 4)) chips.append(createChip(category));
    const gallery = card.querySelector(".gallery");
    gallery.href = workflow.gallery_url;
    const copy = card.querySelector(".copy-path");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(workflow.local_file);
        copy.textContent = "Kopyalandı";
        window.setTimeout(() => { copy.textContent = "Yerel yolu kopyala"; }, 1400);
      } catch {
        copy.textContent = workflow.local_file;
      }
    });
    fragment.append(card);
  }
  resultList.append(fragment);
}

async function submitSearch(event) {
  event?.preventDefault();
  const parameters = new URLSearchParams(new FormData(form));
  const term = parameters.get("q").trim();
  if (!term) {
    resultList.replaceChildren();
    resultSummary.textContent = "Koleksiyonu keşfetmek için bir arama girin.";
    query.focus();
    return;
  }
  resultsSection.setAttribute("aria-busy", "true");
  loading.hidden = false;
  try {
    const response = await fetch(`/api/search?${parameters}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Arama tamamlanamadı.");
    renderResults(payload.results);
  } catch (error) {
    resultList.replaceChildren();
    resultSummary.textContent = error.message;
  } finally {
    loading.hidden = true;
    resultsSection.setAttribute("aria-busy", "false");
  }
}

form.addEventListener("submit", submitSearch);
document.querySelector("#clear").addEventListener("click", () => {
  form.reset();
  resultList.replaceChildren();
  resultSummary.textContent = "Koleksiyonu keşfetmek için bir arama girin.";
  query.focus();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement !== query && !["INPUT", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    query.focus();
  }
});

fetch("/api/stats")
  .then((response) => response.json())
  .then((data) => { status.textContent = `${fullNumber.format(Number(data.indexed_workflows))} iş akışı indekslendi · harita tarihi ${new Date(data.map_generated_at).toLocaleDateString("tr-TR")}`; })
  .catch(() => { status.textContent = "Arama indeksi kullanılamıyor. İndeksi oluşturup sayfayı yenileyin."; });
