// API base: set window.API_BASE before this script, or use same-origin / local dev.
const API_BASE = (typeof window !== "undefined" && window.API_BASE)
  ? window.API_BASE.replace(/\/$/, "")
  : (window.location.port === "8000" || window.location.hostname === "localhost"
      ? "http://localhost:8000"
      : window.location.origin);

require(["esri/WebMap","esri/views/MapView","esri/widgets/Home","esri/widgets/Zoom","esri/widgets/Search"],
function(WebMap, MapView, Home, Zoom, Search) {
  const webmap = new WebMap({ portalItem: { id: "93eef5bd592f48b4a04e20815dba13b6" } });
  const view = new MapView({ container: "viewDiv", map: webmap, ui: { components: [] } });
  view.ui.add(new Zoom({ view }), "top-left");
  view.ui.add(new Home({ view }), "top-left");
  view.ui.add(new Search({ view }), "top-right");
  window._mapView = view;
  webmap.when(() => {
    let total = 0;
    const qs = webmap.layers.toArray().filter(l=>l.type==="feature")
      .map(l=>l.load().then(()=>l.queryFeatureCount().then(n=>{total+=n;})));
    Promise.all(qs).then(()=>{
      document.getElementById("record-count").textContent=`Live data · ${total} records`;
    }).catch(()=>{
      document.getElementById("record-count").textContent="Live data · connected";
    });
  });
});

const messagesEl = document.getElementById("messages");
const heroEl     = document.getElementById("hero");
const questionEl = document.getElementById("question");
let chatStarted  = false;

function startChat() {
  if (!chatStarted) {
    chatStarted = true;
    heroEl.style.display = "none";
    messagesEl.classList.add("visible");
  }
}

// ─────────────────────────────────────────────
// Normalization: accepts board records AND articles
// ─────────────────────────────────────────────
function normalizeProject(p) {
  if (!p || typeof p !== "object") return null;
  const norm = {
    sourceType: p.source_type || p.sourceType ||
                ((p.article_url || p.articleUrl) ? "website_article" : "board_record"),
    title: p.title || p.article_title || p.articleTitle || p.project_name || "",
    location: nullsafe(p.location),
    summary: nullsafe(p.summary),
    id: nullsafe(p.id) || nullsafe(p.application_id),
    status: nullsafe(p.status),
    date: nullsafe(p.date) || nullsafe(p.meeting_date),
    documentUrl: cleanUrl(p.document_url || p.documentUrl),
    articleUrl: cleanUrl(p.article_url || p.articleUrl || p.url),
    publishDate: nullsafe(p.publish_date) || nullsafe(p.publishDate),
    category: nullsafe(p.category),
  };
  // Must have at least something renderable
  if (!norm.title && !norm.id && !norm.articleUrl && !norm.documentUrl) return null;
  return norm;
}

function nullsafe(v) {
  if (v === null || v === undefined) return "";
  const s = String(v).trim();
  return (s.toLowerCase() === "null" || s.toLowerCase() === "none" || s === "") ? "" : s;
}

function cleanUrl(u) {
  const s = nullsafe(u);
  return s.startsWith("http") ? s : "";
}

// ─────────────────────────────────────────────
// JSON block extraction (primary card path)
// ─────────────────────────────────────────────
function extractJsonCards(text) {
  const cards = [];
  const regex = /```json\s*([\s\S]*?)```/gi;
  let m;
  while ((m = regex.exec(text)) !== null) {
    try {
      const parsed = JSON.parse(m[1].trim());
      if (Array.isArray(parsed)) {
        parsed.forEach(p => { const n = normalizeProject(p); if (n) cards.push(n); });
      } else {
        const n = normalizeProject(parsed);
        if (n) cards.push(n);
      }
    } catch (e) { /* malformed JSON — skip */ }
  }
  return cards;
}

// Remove JSON blocks + dangling lead-in sentences from prose
function cleanProse(text) {
  return text
    .replace(/```json[\s\S]*?```/gi, "")
    .replace(/```[\s\S]*?```/g, "")
    // dangling lead-ins the model tends to write before a JSON block
    .replace(/^.*(here'?s?|the) (is )?(the )?most relevant (item|project|json block|record)( is)?[:.]?\s*$/gim, "")
    .replace(/^.*json (block|output|details?)[:.]?\s*$/gim, "")
    .replace(/^\s*relevant details?[:.]?\s*$/gim, "")
    // collapse 3+ newlines
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// ─────────────────────────────────────────────
// Legacy delimiter parser (fallback)
// ─────────────────────────────────────────────
function parseProjects(text) {
  const projects = [];
  const patterns = [
    /START_PROJECT([\s\S]*?)END_PROJECT/g,
    /===PROJECT===([\s\S]*?)===END===/g,
    /---PROJECT---([\s\S]*?)---END---/g,
    /START_ARTICLE([\s\S]*?)END_ARTICLE/g,
  ];

  let matched = false;
  for (const regex of patterns) {
    let match;
    regex.lastIndex = 0;
    while ((match = regex.exec(text)) !== null) {
      matched = true;
      const block = match[1];
      const get = (key) => {
        const m2 = block.match(new RegExp(key + ":\\s*(.+)"));
        return m2 ? m2[1].trim() : "";
      };
      const p = normalizeProject({
        title:        get("Title"),
        id:           get("ID"),
        location:     get("Location"),
        summary:      get("Summary"),
        status:       get("Status"),
        date:         get("Date"),
        document_url: get("DocumentURL"),
        article_url:  get("ArticleURL"),
        publish_date: get("PublishDate"),
        category:     get("Category"),
        source_type:  get("SourceType"),
      });
      if (p) projects.push(p);
    }
    if (matched) break;
  }

  let prose = text;
  for (const regex of patterns) {
    regex.lastIndex = 0;
    prose = prose.replace(regex, "");
  }
  if (matched) {
    prose = prose
      .replace(/(Title|ID|Location|Summary|Status|Date|DocumentURL|ArticleURL|PublishDate|Category|SourceType):.*\n?/g, "")
      .replace(/(START|END)_(PROJECT|ARTICLE).*\n?/g, "");
  }

  return { projects, prose: cleanProse(prose) };
}

// ─────────────────────────────────────────────
// Status helpers
// ─────────────────────────────────────────────
function statusClass(s) {
  s = (s||"").toLowerCase();
  if (s.includes("approved") || s.includes("accepted"))  return "status-approved";
  if (s.includes("denied"))    return "status-denied";
  if (s.includes("continued") || s.includes("recommended")) return "status-continued";
  return "status-unknown";
}
function statusEmoji(s) {
  s = (s||"").toLowerCase();
  if (s.includes("approved") || s.includes("accepted"))  return "✅";
  if (s.includes("denied"))    return "❌";
  if (s.includes("continued")) return "⏳";
  if (s.includes("recommended")) return "🔁";
  return "⚪";
}

function isArticle(p) {
  return p.sourceType === "website_article" || (!!p.articleUrl && !p.documentUrl);
}

// ─────────────────────────────────────────────
// Card renderers
// ─────────────────────────────────────────────
function renderProjectCard(p) {
  if (!p) return document.createDocumentFragment();
  if (isArticle(p)) return renderArticleCard(p);

  const card = document.createElement("div");
  card.className = "proj-card";
  const meta = [p.id, p.location].filter(Boolean).join(" · ");
  card.innerHTML = `
    <div class="card-tag card-tag-board">🏛 Board Record</div>
    <div class="proj-title">${escHtml(p.title || p.id || "Project")}</div>
    ${meta ? `<div class="proj-meta">${escHtml(meta)}</div>` : ""}
    ${p.summary ? `<div class="proj-body">${escHtml(p.summary)}</div>` : ""}
    ${p.status ? `<div class="proj-status ${statusClass(p.status)}">${statusEmoji(p.status)} ${escHtml(p.status)}${p.date?" · "+escHtml(p.date):""}</div>` : (p.date ? `<div class="proj-meta">📅 ${escHtml(p.date)}</div>` : "")}
    <div class="proj-actions">
      ${p.documentUrl?`<a class="btn-minutes" href="${escHtml(p.documentUrl)}" target="_blank" rel="noopener">📄 View Minutes</a>`:""}
      ${p.location?`<button class="btn-dir" onclick="panAndDirect('${escAttr(p.location)}')">📍 Directions</button>`:""}
    </div>`;
  return card;
}

function renderArticleCard(p) {
  const card = document.createElement("div");
  card.className = "proj-card article-card";
  const meta = [p.category, p.publishDate].filter(Boolean).join(" · ");
  card.innerHTML = `
    <div class="card-tag card-tag-article">📰 EsteroToday Article</div>
    <div class="proj-title">${escHtml(p.title || "Article")}</div>
    ${meta ? `<div class="proj-meta">${escHtml(meta)}</div>` : ""}
    ${p.summary ? `<div class="proj-body">${escHtml(p.summary)}</div>` : ""}
    <div class="proj-actions">
      ${p.articleUrl?`<a class="btn-article" href="${escHtml(p.articleUrl)}" target="_blank" rel="noopener">📰 Read Article ↗</a>`:""}
      ${p.location?`<button class="btn-dir" onclick="panAndDirect('${escAttr(p.location)}')">📍 Directions</button>`:""}
    </div>`;
  return card;
}

// ─────────────────────────────────────────────
// Message rendering
// ─────────────────────────────────────────────
function renderMarkdown(prose) {
  const el = document.createElement("div");
  el.className = "prose";
  try {
    if (typeof marked !== "undefined" && marked.parse) {
      el.innerHTML = marked.parse(prose);
    } else if (typeof marked === "function") {
      el.innerHTML = marked(prose);
    } else {
      el.innerHTML = escHtml(prose).replace(/\n/g, "<br>");
    }
  } catch (e) {
    console.warn("marked error:", e);
    el.innerHTML = escHtml(prose).replace(/\n/g, "<br>");
  }
  return el;
}

function buildBotRow(prose, cards) {
  const row = document.createElement("div");
  row.className = "msg-row";
  const botDiv = document.createElement("div");
  botDiv.className = "msg-bot";
  const avatar = document.createElement("div");
  avatar.className = "bot-avatar";
  avatar.textContent = "🏛";
  botDiv.appendChild(avatar);
  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (prose) bubble.appendChild(renderMarkdown(prose));
  (cards || []).forEach(c => { if (c) bubble.appendChild(renderProjectCard(c)); });

  if (!prose && (!cards || cards.length === 0)) {
    const d = document.createElement("div");
    d.textContent = "Sorry, I couldn't find an answer.";
    bubble.appendChild(d);
  }

  botDiv.appendChild(bubble);
  row.appendChild(botDiv);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendBotResponse(data) {
  const cards = ((data.projects || []).concat(data.articles || []))
    .map(normalizeProject).filter(Boolean);
  const prose = cleanProse((data.summary || data.answer || "").trim());
  buildBotRow(prose, cards);
}

function appendMsg(role, content) {
  if (role === "user") {
    const row = document.createElement("div");
    row.className = "msg-row";
    row.innerHTML = `<div class="msg-user"><div class="bubble">${escHtml(content)}</div></div>`;
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return;
  }

  // Bot plain-text path: try JSON cards first, then legacy delimiters
  const jsonCards = extractJsonCards(content);
  if (jsonCards.length > 0) {
    buildBotRow(cleanProse(content), jsonCards);
    return;
  }
  const { projects, prose } = parseProjects(content);
  buildBotRow(prose || cleanProse(content), projects);
}

function showTyping() {
  const row = document.createElement("div"); row.id="typing-row"; row.className="msg-row";
  row.innerHTML=`<div class="msg-bot"><div class="bot-avatar">🏛</div><div class="typing-indicator"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div></div>`;
  messagesEl.appendChild(row); messagesEl.scrollTop=messagesEl.scrollHeight;
}
function removeTyping() { const t=document.getElementById("typing-row"); if(t)t.remove(); }

// ─────────────────────────────────────────────
// Streaming (kept; falls back to /chat)
// ─────────────────────────────────────────────
async function tryStreamChat(question) {
  let res;
  try {
    res = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
  } catch (e) { return false; }
  if (!res.ok || !res.body) return false;

  removeTyping();
  const row = document.createElement("div");
  row.className = "msg-row";
  row.innerHTML = `<div class="msg-bot"><div class="bot-avatar">🏛</div><div class="bubble" id="stream-bubble"></div></div>`;
  messagesEl.appendChild(row);
  const bubble = document.getElementById("stream-bubble");
  const proseEl = document.createElement("div");
  proseEl.className = "prose";
  bubble.appendChild(proseEl);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";
  let donePayload = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.startsWith("data: ")) continue;
      let payload;
      try { payload = JSON.parse(part.slice(6)); } catch(e) { continue; }
      if (payload.type === "token" && payload.text) {
        fullText += payload.text;
        proseEl.textContent = fullText;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (payload.type === "done") {
        donePayload = payload;
      } else if (payload.type === "error") {
        proseEl.textContent = "⚠️ " + (payload.detail || "Stream error");
      }
    }
  }

  // Finalize: re-render prose as markdown, extract cards
  const cards = donePayload
    ? ((donePayload.projects || []).concat(donePayload.articles || [])).map(normalizeProject).filter(Boolean)
    : extractJsonCards(fullText);
  const finalProse = cleanProse((donePayload && donePayload.summary) || fullText);
  bubble.innerHTML = "";
  if (finalProse) bubble.appendChild(renderMarkdown(finalProse));
  cards.forEach(c => bubble.appendChild(renderProjectCard(c)));
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return true;
}

// ─────────────────────────────────────────────
// Main send
// ─────────────────────────────────────────────
async function sendMessage(overrideText) {
  const q = (overrideText || questionEl.value).trim();
  if (!q) return;
  startChat(); questionEl.value=""; autoResize(questionEl);
  appendMsg("user", q); showTyping();
  try {
    const streamed = await tryStreamChat(q);
    if (streamed) return;

    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      removeTyping();
      appendMsg("bot", "⚠️ Backend error " + res.status + ": " + (err.detail || "Unknown error"));
      return;
    }
    const data = await res.json();
    removeTyping();

    // Structured response path
    if (data.projects || data.articles || data.summary) {
      appendBotResponse(data);
      return;
    }

    // Plain answer path: extract JSON card(s) before stripping
    const raw = (data.answer || data.response || "");
    const cards = extractJsonCards(raw);
    const prose = cleanProse(raw);

    if (!prose && cards.length === 0) {
      appendMsg("bot", "I received an empty response from the backend. Please try again.");
      return;
    }
    buildBotRow(prose, cards);
  } catch (e) {
    removeTyping();
    console.error("Fetch error:", e);
    appendMsg("bot", `⚠️ Could not reach the backend at ${API_BASE}. Is uvicorn running?`);
  }
}

function sendChip(el) { sendMessage(el.querySelector("span").textContent); }
function handleKey(e) { if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendMessage();} }
function autoResize(el) { el.style.height="auto"; el.style.height=Math.min(el.scrollHeight,100)+"px"; }
function escHtml(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function escAttr(s) { return escHtml(s).replace(/'/g, "&#39;"); }

function panAndDirect(address) {
  window.open(`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address+", Estero, FL")}`,"_blank");
  if (window._mapView) {
    require(["esri/rest/locator"], function(locator) {
      locator.addressToLocations("https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer",
        { address:{SingleLine:address}, maxLocations:1 })
        .then(r=>{ if(r.length) window._mapView.goTo({target:r[0].location,zoom:15}); })
        .catch(()=>{});
    });
  }
}

async function loadCSV() {
  const file = document.getElementById("csv-file-input").files[0];
  if (!file) { alert("Please select a CSV file first."); return; }
  const statusEl = document.getElementById("load-status");
  statusEl.style.color = "var(--text-muted)";
  statusEl.textContent = "⏳ Loading…";
  const formData = new FormData(); formData.append("file", file);
  try {
    const res = await fetch(`${API_BASE}/load`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
      statusEl.style.color = "var(--denied)";
      statusEl.textContent = "❌ " + (data.detail || "Server error " + res.status);
      console.error("Load error:", data);
    } else {
      statusEl.style.color = "var(--success)";
      statusEl.textContent = "✅ " + (data.message || "Loaded!");
    }
  } catch(e) {
    statusEl.style.color = "var(--denied)";
    statusEl.textContent = `❌ Can't reach backend at ${API_BASE}`;
    console.error(e);
  }
}

function expandMap(e) {
  e.preventDefault();
  const app = document.getElementById("app");
  const expanded = app.style.gridTemplateColumns === "1fr";
  app.style.gridTemplateColumns = expanded ? "46% 1fr" : "1fr";
  document.getElementById("expand-btn").textContent = expanded ? "⤢ Expand" : "⤡ Collapse";
  if (window._mapView) setTimeout(()=>window._mapView.resize(),300);
}
function toggleMobileMap() { document.getElementById("map-panel").classList.toggle("mobile-show"); }
