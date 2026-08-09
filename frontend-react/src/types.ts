export type SourceType = "board_record" | "website_article" | "village_council";

export interface NormalizedCard {
  sourceType: SourceType;
  title: string;
  location: string;
  summary: string;
  id: string;
  status: string;
  date: string;
  documentUrl: string;
  pdfUrl: string;
  pdfName: string;
  articleUrl: string;
  publishDate: string;
  category: string;
  lat: number | null;
  lng: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "bot";
  timestamp: number;
  // user messages
  text?: string;
  // bot messages
  prose?: string;
  cards?: NormalizedCard[];
  sources?: string[];
  streaming?: boolean;
  error?: boolean;
}

export interface ChatApiResponse {
  answer?: string;
  response?: string;
  summary?: string;
  projects?: unknown[];
  articles?: unknown[];
  sources?: string[];
}

export interface StreamDonePayload {
  type: "done";
  summary?: string;
  projects?: unknown[];
  articles?: unknown[];
  sources?: string[];
}

// ─────────────────────────────────────────────
// Community Pulse dashboard
// ─────────────────────────────────────────────
export type RightTab = "map" | "pulse";

/** One row of public/meetings.json — manually maintained, see that file's header comment. */
export interface Meeting {
  id: string;
  board: string;
  date: string; // YYYY-MM-DD
  time: string; // e.g. "5:30 PM"
  venue: string;
  /** Village minutes index the pipeline scrapes (estero-fl.gov). */
  url?: string;
}

export interface NewsPost {
  id: number;
  title: string;
  link: string;
  date: string; // ISO datetime from WordPress
}

export interface RecentDecision {
  title: string;
  date: string | null;
  board: string | null;
  status: string | null;
  applicationId: string | null;
}

// Named CalendarEvent (not Event) to avoid shadowing the DOM's global Event type.
export type EventCategory = "engage-estero" | "village";

export interface CalendarEvent {
  id: number;
  title: string;
  start: string; // local wall-clock datetime, e.g. "2026-08-18T16:30:00" (no offset)
  end: string;
  allDay: boolean;
  venue: string | null;
  url: string;
  category: EventCategory;
}

/** Unified view of a single day's activity, merging /api/events (EsteroToday's
 * Events Calendar feed) with public/meetings.json (the hand-maintained Village
 * Council / PZDB schedule NextMeetings reads) — the mini calendar in
 * UpcomingEvents renders dots/circles and the hover/click list from this, not
 * from either source alone, so it never disagrees with Next Meetings. */
export interface DayEvent {
  source: "esterotoday" | "meeting";
  id: string; // unique across both sources
  dateKey: string; // YYYY-MM-DD
  sortKey: string; // chronologically sortable string, for ordering same-day items
  title: string;
  time: string; // display string, e.g. "4:30 PM" or "All day"
  venue: string | null;
  url: string | null; // EsteroToday event page, or Village minutes index for meetings
  category: EventCategory;
}
