import { useEffect, useState } from "react";
import { setMapView, registerMapPanTarget, type MapPanTarget } from "../../lib/mapViewStore";

// Esri "Nearby" Instant App wrapping webmap 84a56d2f741d49f5a70c547923fb45d5
// (same webmap this panel used to load directly via the JS SDK). Embedding
// the hosted app itself — not the raw webmap — was a deliberate choice: it
// picks up the app's own theme/search config, at the cost of native SDK
// control (no MapView to call .goTo() on for a cross-origin iframe). "Show
// on map" on a chat card instead drives the iframe's own center/level URL
// parameters (Esri's documented Instant Apps deep-link params — see
// mapViewStore.ts's panToCoords) so panning still works without one.
const INSTANT_APP_ID = "90d68fdd2de841b295cc1c3cfd6df524";
const INSTANT_APP_BASE_URL = `https://eccl-swfl-safety.maps.arcgis.com/apps/instant/nearby/index.html?appid=${INSTANT_APP_ID}`;

function buildMapUrl(target: MapPanTarget | null): string {
  if (!target) return INSTANT_APP_BASE_URL;
  return `${INSTANT_APP_BASE_URL}&center=${target.lng},${target.lat}&level=${target.zoom}`;
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
