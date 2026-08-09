import type MapView from "@arcgis/core/views/MapView";
import type GraphicsLayer from "@arcgis/core/layers/GraphicsLayer";

// Small singleton so non-map components (e.g. the "Directions" button on a
// chat card) can reach the live MapView instance without prop-drilling it
// through the whole tree. Set by MapPanel on mount, cleared on unmount.
let currentView: MapView | null = null;

export function setMapView(view: MapView | null) {
  currentView = view;
}

export function getMapView(): MapView | null {
  return currentView;
}

export async function panToAddress(address: string): Promise<void> {
  const view = currentView;
  if (!view) return;
  try {
    const locator = await import("@arcgis/core/rest/locator.js");
    const result = await locator.addressToLocations(
      "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer",
      { address: { SingleLine: address }, maxLocations: 1 },
    );
    const location = result[0]?.location;
    if (location) {
      view.goTo({ target: location, zoom: 15 });
    }
  } catch {
    // best-effort only; the Google Maps tab is the primary directions path
  }
}

export function openDirections(address: string): void {
  const query = encodeURIComponent(`${address}, Estero, FL`);
  window.open(`https://www.google.com/maps/search/?api=1&query=${query}`, "_blank", "noopener");
  void panToAddress(address);
}

/** Pan/zoom straight to known coordinates — no geocoding round-trip needed.
 * Used by the chat card "Show on map" link, which already has lat/lng from
 * the board record's own metadata. */
export function panToCoords(lat: number, lng: number, zoom = 16): void {
  const view = currentView;
  if (!view) return;
  view.goTo({ center: [lng, lat], zoom }).catch(() => {
    // view may still be mid-resize right after a tab switch — best-effort only
  });
}

export interface ResultPoint {
  lat: number;
  lng: number;
  label?: string;
}

// A dedicated layer for "results of the current query" markers, kept separate
// from the webmap's own feature layers so we can clear it on every new answer
// without touching the base data. Lazily created on first use.
let resultLayer: GraphicsLayer | null = null;

// Zoom level used when centring on a result. Higher = closer; 18 is roughly
// building/parcel level, so the specific location fills the map.
const RESULT_ZOOM = 18;

/** Drop a marker on each result point and centre the map on the top result
 * (the first, best-ranked one) so the location the user asked about sits in the
 * exact centre of the map at a close zoom. Markers are still added for every
 * result. Called after a chat answer so the map follows the user's question. */
export async function showResultsOnMap(points: ResultPoint[]): Promise<void> {
  const view = currentView;
  if (!view || !view.map || points.length === 0) return;
  try {
    const [{ default: Graphic }, { default: GraphicsLayer }, { default: Point }] =
      await Promise.all([
        import("@arcgis/core/Graphic"),
        import("@arcgis/core/layers/GraphicsLayer"),
        import("@arcgis/core/geometry/Point"),
      ]);
    if (!resultLayer) {
      resultLayer = new GraphicsLayer({ title: "Query results", listMode: "hide" });
      view.map.add(resultLayer);
    }
    resultLayer.removeAll();
    const symbol = {
      type: "simple-marker" as const,
      style: "circle" as const,
      color: [37, 99, 235, 0.9],
      size: 12,
      outline: { color: [255, 255, 255], width: 1.75 },
    };
    const graphics = points.map(
      (p) =>
        new Graphic({
          geometry: new Point({ longitude: p.lng, latitude: p.lat }),
          symbol,
          attributes: { label: p.label ?? "" },
          popupTemplate: p.label ? { title: "{label}" } : undefined,
        }),
    );
    resultLayer.addMany(graphics);

    // Let the view settle to its real size before recentring, so the target
    // lands at the true visual centre rather than being offset by a resize
    // that's still pending (e.g. right after the Map tab becomes visible).
    await view.when();

    // Always centre on the top (best-ranked) result. goTo({ center }) puts that
    // point at the exact centre of the map view.
    const top = points[0];
    await view.goTo({ center: [top.lng, top.lat], zoom: RESULT_ZOOM });
  } catch {
    // best-effort — never let a map hiccup break the chat flow
  }
}

/** Remove any current query-result markers (e.g. on New Chat). */
export function clearResultsOnMap(): void {
  resultLayer?.removeAll();
}

interface ViewState {
  center: [number, number];
  zoom: number;
}

let savedViewState: ViewState | null = null;

/** The MapView's container goes through display:none <-> display:flex when
 * the Map/Pulse tab switches (kept mounted rather than unmounted so the
 * expensive WebGL view survives). Toggling a WebGL canvas's container to/from
 * zero size isn't something ArcGIS's own resize handling reliably preserves
 * center/zoom across, so RightPanel calls these explicitly around every
 * switch as a belt-and-suspenders fix. */
export function captureViewState(): void {
  const view = currentView;
  const { longitude, latitude } = view?.center ?? {};
  if (longitude == null || latitude == null) return;
  savedViewState = { center: [longitude, latitude], zoom: view!.zoom };
}

export function restoreViewState(): void {
  const view = currentView;
  if (!view || !savedViewState) return;
  const { center, zoom } = savedViewState;
  view.goTo({ center, zoom }, { animate: false }).catch(() => {
    // best-effort — if this races with something else, worst case the pan is off by a frame
  });
}
