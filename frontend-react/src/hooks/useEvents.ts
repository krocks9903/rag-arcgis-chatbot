import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "../lib/config";
import type { CalendarEvent } from "../types";

interface EventsApiResponse {
  events: CalendarEvent[];
  error: boolean;
}

interface UseEventsResult {
  events: CalendarEvent[];
  loading: boolean;
  error: boolean;
  retry: () => void;
}

/** Fetches /api/events (backend proxy for EsteroToday's Events Calendar —
 * see backend/events.py for why this isn't called directly from the
 * browser). The backend already merges, dedupes, and future-filters, and
 * degrades to { events: [], error: true } on total failure — this hook just
 * mirrors that contract instead of re-deriving it. */
export function useEvents(): UseEventsResult {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);

    fetch(`${API_BASE}/api/events`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<EventsApiResponse>;
      })
      .then((data) => {
        if (cancelled) return;
        setEvents(data.events || []);
        setError(!!data.error);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setEvents([]);
        setError(true);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [attempt]);

  return { events, loading, error, retry };
}
