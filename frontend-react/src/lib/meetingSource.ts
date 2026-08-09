import type { Meeting } from "../types";

/** Official Village pages that pipeline/discover.py scrapes for new minutes PDFs. */
const COUNCIL_MINUTES_URL = "https://estero-fl.gov/villagecouncilminutes/";
const PZDB_MINUTES_URL = "https://estero-fl.gov/pzdbminutes/";
const AGENDAS_FALLBACK_URL = "https://estero-fl.gov/agendas-minutes/";

/** Resolve the public page a meeting was (or will be) scraped from. */
export function meetingSourceUrl(meeting: Pick<Meeting, "board" | "url">): string {
  if (meeting.url && /^https?:\/\//i.test(meeting.url)) return meeting.url;
  const board = (meeting.board || "").toLowerCase();
  if (board.includes("zoning") || board.includes("pzdb") || board.includes("design board")) {
    return PZDB_MINUTES_URL;
  }
  if (board.includes("council")) {
    return COUNCIL_MINUTES_URL;
  }
  return AGENDAS_FALLBACK_URL;
}
