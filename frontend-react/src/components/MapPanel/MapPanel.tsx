import { useEffect, useState } from "react";
import { setMapView, registerMapPanTarget, type MapPanTarget } from "../../lib/mapViewStore";

// Esri's dedicated Embed map viewer, wrapping the same underlying webmap
// (84a56d2f741d49f5a70c547923fb45d5) this panel used to load directly via
// the JS SDK. This used to be the "Nearby" Instant App instead (picks up the
// app's own theme/search config) — switched away from it after finding two
// real problems in production: (1) it's built assuming same-origin or
// top-level embedding, and Safari/WebKit — unlike Chromium — actually
// enforces that: the app's own JS throws "Blocked a frame with origin ...
// from accessing a frame with origin ..." trying to touch window.parent/top
// cross-origin, and the WebGL map canvas silently never renders (UI chrome
// around it still does, which makes it look like a rendering bug rather
// than a permissions error). (2) Instant Apps show a first-visit onboarding
// panel over the whole map with no way to disable it from our side (that
// setting lives in the app's own ArcGIS Online config) — easy to mistake
// for the map being broken. The Embed viewer is Esri's purpose-built
// solution for arbitrary third-party iframe embedding: no onboarding
// overlay, and it renders correctly in WebKit. Same center/level deep-link
// URL param convention as Instant Apps, so "Show on map" (see
// mapViewStore.ts's panToCoords) needed no changes.
const WEBMAP_ID = "84a56d2f741d49f5a70c547923fb45d5";
const EMBED_BASE_URL = `https://www.arcgis.com/apps/Embed/index.html?webmap=${WEBMAP_ID}&zoom=true&scale=true&search=true`;

// The board-records feature layer has a hard server-side minScale (1:108,468,
// set on the hosted FeatureServer itself — see
// services2.arcgis.com/.../FeatureServer/0?f=json) beyond which it stops
// rendering entirely, in every app that embeds it, not just this one. The
// webmap's own saved default extent is much more zoomed in than that (~1.2mi
// wide — level ~15), so leaving center/level unset showed only a handful of
// records near its one saved spot. This default instead sits right at the
// edge of what minScale allows, showing the widest area the layer will
// actually render across.
const DEFAULT_CENTER: MapPanTarget = { lat: 26.435, lng: -81.8, zoom: 13 };

function buildMapUrl(target: MapPanTarget | null): string {
  const t = target ?? DEFAULT_CENTER;
  return `${EMBED_BASE_URL}&center=${t.lng},${t.lat}&level=${t.zoom}`;
}

// The app's underlying feature layer is public on its own, so the record
// count in TopBar ("Live data · N records") can still be queried directly —
// no need to lose that just because the map itself moved into an iframe.
const RECORD_COUNT_URL =
  "https://services2.arcgis.com/UzlfiFv8kzq0Q4vo/arcgis/rest/services/Estero_Board_Records_%E2%80%94_All_Categories/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json";

interface MapPanelProps {
  expanded: boolean;
  onToggleExpand: () => void;
  onRecordCount: (count: number | null) => void;
}

export default function MapPanel({ expanded, onToggleExpand, onRecordCount }: MapPanelProps) {
  const [loadError, setLoadError] = useState(false);
  const [panTarget, setPanTarget] = useState<MapPanTarget | null>(null);
  const mapUrl = buildMapUrl(panTarget);

  useEffect(() => {
    // No live MapView to hand out from an iframe — clear any stale reference.
    setMapView(null);
    // Let mapViewStore's panToCoords() drive our iframe src (see MapPanel
    // comment above and mapViewStore.ts) — this is what makes "Show on map"
    // actually pan instead of no-op.
    registerMapPanTarget(setPanTarget);

    let cancelled = false;
    fetch(RECORD_COUNT_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<{ count?: number; error?: unknown }>;
      })
      .then((data) => {
        if (cancelled) return;
        if (typeof data.count === "number") onRecordCount(data.count);
        else onRecordCount(null);
      })
      .catch(() => {
        if (!cancelled) onRecordCount(null);
      });

    return () => {
      cancelled = true;
      registerMapPanTarget(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new pan target means a fresh iframe navigation — give it its own
  // chance to fail/succeed rather than carrying over a stale error banner.
  useEffect(() => {
    setLoadError(false);
  }, [panTarget]);

  return (
    <section id="map-panel">
      <div id="map-header">
        <div id="map-header-left">
          <div id="map-icon">🗺</div>
          <div id="map-title">
            <strong>Project Map</strong>
            <span>Village of Estero · Live ArcGIS</span>
          </div>
        </div>
        <div id="map-actions">
          <button type="button" className="map-btn" id="expand-btn" onClick={onToggleExpand}>
            {expanded ? "⤡ Collapse" : "⤢ Expand"}
          </button>
          <a className="map-btn" href={mapUrl} target="_blank" rel="noopener noreferrer">
            ↗ Open
          </a>
        </div>
      </div>
      <div id="map-label">Estero Board Records</div>
      {loadError && (
        <div id="map-error-banner" role="alert">
          ⚠️ The map failed to load.{" "}
          <a href={mapUrl} target="_blank" rel="noopener noreferrer">
            Open it directly on ArcGIS Online
          </a>
          .
        </div>
      )}
      <iframe
        id="viewDiv"
        title="Estero Board Records map"
        src={mapUrl}
        // Safari/WebKit enforces iframe permissions policy far more strictly
        // than Chromium: with no `allow` attribute at all, the Esri app's own
        // UI chrome (search bar, zoom controls) still rendered, but the WebGL
        // map canvas itself silently stayed blank — Chromium degrades this
        // gracefully (a console warning), WebKit just doesn't render.
        // geolocation covers the app's "Use current location" search option.
        allow="fullscreen; geolocation"
        // Cross-origin iframe: this can catch a hard network/navigation
        // failure, but not an error the Esri app renders inside its own
        // page — that's invisible to us. The "Open directly" link above is
        // the real fallback for that case.
        onError={() => setLoadError(true)}
        style={{ border: "none", width: "100%", height: "100%" }}
      />
      <div id="map-footer">
        <div id="map-footer-dot" />
        Powered by Esri · ArcGIS Online
      </div>
    </section>
  );
}
