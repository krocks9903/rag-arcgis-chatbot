# Map UX plan — user-friendly ArcGIS features

Status: **proposal** (March 2026). The chat UI currently embeds an Esri Instant
App in an iframe. That choice trades away programmatic control — **Show on map**
from chat cards and query-result highlighting do not work today. Several items
below depend on restoring an embedded `@arcgis/core` MapView first.

## Current state

| Piece | Today |
| --- | --- |
| Map surface | Instant App iframe (`Nearby` template) |
| Data | Public FeatureServer *Estero Board Records — All Categories* |
| Chat → map | `mapViewStore.ts` can pan/zoom and drop result pins, but `MapPanel` sets `setMapView(null)` because the iframe has no SDK handle |
| Card actions | Directions (Google Maps) works; **Show on map** is best-effort only |
| Categories | Eight land-use categories in gold/ArcGIS exports (rezoning, commercial, etc.) |

## Design principles

1. **Plain language over GIS jargon** — labels like “Shops & restaurants” not “C-2”.
2. **Answer the question on the map** — chat results should appear as pins without extra clicks.
3. **Progressive disclosure** — default view is simple; filters and layers are one tap away.
4. **Mobile-first** — thumb-sized controls, collapsible legend, no hover-only affordances.
5. **Trust through context** — every pin shows date, outcome, and a link to source minutes.

---

## Phase 1 — Fix the broken loop (highest impact)

These restore what users already expect from the chat cards.

### 1.1 Embedded MapView (replace iframe)

**Why:** Users tap “Show on map” and nothing meaningful happens inside the iframe.

**What:**
- Load webmap `84a56d2f741d49f5a70c547923fb45d5` (or current production webmap) via `Map` + `MapView` in `MapPanel.tsx`.
- Call `setMapView(view)` on mount so `showResultsOnMap`, `panToCoords`, and `captureViewState` work again.
- Keep “Open full map” as an external link to the Instant App for power users.

**User benefit:** Chat answers and map stay in sync — one product, not two tabs that ignore each other.

### 1.2 Query-result pins from chat

**Why:** After “What’s happening on Corkscrew Road?”, users should *see* those projects.

**What:** Re-enable `showResultsOnMap()` (already wired from `useChat.ts`) with:
- Distinct blue markers for “your search results” vs. base layer pins.
- Auto-zoom to the best-ranked result; fit-all when there are 2–6 results.
- Clear pins on **New chat**.

**User benefit:** No manual searching on the map after the bot already found the answer.

### 1.3 Popups that read like chat cards

**Why:** Default ArcGIS popups are often field dumps.

**What:** `PopupTemplate` with:
- Project title
- Status (Approved / Denied / Pending) with color chip
- Meeting date
- One-line summary
- Buttons: **View minutes** · **Directions** · **Ask about this**

**User benefit:** The map is self-explanatory without reading the chat thread.

---

## Phase 2 — Make exploration easy (filters & search)

### 2.1 Category filter chips

**Why:** 2,000+ points overwhelm non-GIS users.

**What:** Horizontal chips above the map (same visual language as Pulse event chips):

| Chip | Maps to layer / filter |
| --- | --- |
| All | No filter |
| Rezoning | `LandUseCategory` = rezoning |
| Commercial | commercial |
| Residential | residential |
| … | remaining gold categories |

- Single-select or multi-select; updating the FeatureLayer `definitionExpression`.
- Chip shows count when filtered (“Commercial · 42”).

**User benefit:** “Show me commercial projects” becomes one tap, not layer-panel archaeology.

### 2.2 Simple search box

**Why:** Users think in addresses and project names, not SQL.

**What:**
- Single input: “Search address, road, or application ID…”
- Combine:
  - **Geocode** (World GeocodeServer, biased to Estero bbox)
  - **Attribute search** on `ApplicationId`, `ProjectName`, `AddressNormalized`
- Show top 5 matches in a dropdown; selecting one pans + opens popup.

**User benefit:** Same mental model as Google Maps + “find my permit”.

### 2.3 Outcome filter (Approved / Denied / Pending)

**Why:** “What got approved last year?” is a common civic question.

**What:** Three toggle chips or a small dropdown tied to `Outcome` / `Status`.
- Default: all outcomes visible.
- Optional “Hide denied” for cleaner development-focused views.

**User benefit:** Reduces noise; matches how residents talk about projects.

### 2.4 Basemap toggle (Streets / Imagery)

**Why:** Satellite imagery helps people recognize *their* neighborhood.

**What:** Two-button toggle (not a long basemap gallery).
- Default: light gray or streets (readable labels).
- Alternate: recent aerial imagery.

**User benefit:** “Oh, that’s the empty lot next to the Publix” — recognition without GIS skill.

---

## Phase 3 — Time, sharing, and polish

### 3.1 Time filter (year or “last N years”)

**Why:** Planning is historical; users ask about trends.

**What:**
- Slider or preset chips: `2026` · `2025` · `2024` · `All years`
- Filter `MeetingDate` / `MeetingYear` on the layer.
- Pair with recency messaging in chat (“showing decisions since 2024”).

**User benefit:** Answers “what’s new?” vs. “what happened before?” visually.

### 3.2 Clustering at city scale

**Why:** Pin soup when zoomed out over Estero.

**What:** `featureReduction: { type: "cluster" }` with cluster popup: “12 projects — click to zoom”.
- Disable clustering at zoom ≥ 15 so parcel-level detail stays precise.

**User benefit:** Overview doesn’t look broken; detail stays accurate when zoomed in.

### 3.3 Share this map view

**Why:** Residents forward links to neighbors and HOA boards.

**What:** “Share” copies a URL with hash params: `?lat=&lng=&zoom=&category=&year=`
- On load, apply params to view + filters.
- Fallback: copy coordinates + project name text for email.

**User benefit:** Advocacy and neighborhood groups can pass around one link.

### 3.4 “Near me” (optional, privacy-safe)

**Why:** The current Instant App uses the Nearby template — users may expect it.

**What:**
- Button: **Center on my location** (browser geolocation, one-shot, no tracking).
- Optional buffer: “Projects within 1 mi” using `geometryEngine.geodesicBuffer`.
- Clear permission denial message; never required for core flows.

**User benefit:** Hyperlocal discovery without typing an address.

### 3.5 Print / screenshot-friendly layout

**Why:** Village meetings and HOA packets still use paper.

**What:** `view.takeScreenshot()` or browser print CSS that hides chat chrome and shows legend + date stamp.

**User benefit:** Export for agendas and public comment.

---

## Phase 4 — Advanced (only if Phase 1–2 ship)

| Feature | User value | Cost |
| --- | --- | --- |
| Parcel boundaries (Lee County) | Context at lot level | Extra layer + attribution |
| Compare two projects side-by-side | Developers / activists | Custom UI |
| Draw “area I care about” polygon | Subscribe to alerts in zone | Sketch widget + backend |
| 3D buildings | Low value in Estero suburb | High |
| Routing inside map | Duplicates Google Maps | Low priority |

---

## Recommended build order

```text
1. Embedded MapView + result pins + rich popups     ← unblocks chat/map promise
2. Category chips + outcome filter + search box   ← daily usability
3. Basemap toggle + clustering                    ← visual clarity
4. Year filter + share link                       ← civic power users
5. Near me + print                                ← nice-to-have
```

## Success metrics (lightweight)

- **Show on map** click → map centers correctly (manual QA checklist).
- Time from chat answer to visible pin &lt; 2 s on desktop.
- Filter chip changes layer count without full page reload.
- Mobile: map usable at 375 px width without horizontal scroll.

## Technical notes

- `@arcgis/core` is already a dependency; `mapViewStore.ts` is the integration point.
- Gold CSV + FeatureServer share the same category vocabulary — keep chip labels in one config file (`frontend-react/src/lib/mapCategories.ts`).
- Do not re-introduce brittle third-party venue scrapers on the map; events stay on Pulse `/api/events`.

## Out of scope (for now)

- Editing geometry or submitting permits on the map.
- Replacing Google Directions with Esri routing.
- Loading all 719 PDFs as map attachments (link from popup to chat/RAG instead).
