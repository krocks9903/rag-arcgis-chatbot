import { useMemo, useState } from "react";
import { useEvents } from "../../hooks/useEvents";
import { useMeetings } from "../../hooks/useMeetings";
import { SkeletonRows } from "./Skeleton";
import MiniCalendar from "./MiniCalendar";
import type { CalendarEvent, DayEvent, Meeting } from "../../types";

function formatDateBadge(dateKey: string): { month: string; day: number } {
  const d = new Date(`${dateKey}T00:00:00`);
  return { month: d.toLocaleDateString("en-US", { month: "short" }).toUpperCase(), day: d.getDate() };
}

function formatChipDate(dateKey: string): string {
  const d = new Date(`${dateKey}T00:00:00`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function formatEventTime(ev: CalendarEvent): string {
  if (ev.allDay) return "All day";
  const d = new Date(ev.start);
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** "9:30 AM" -> "09:30", so a meeting's display time can be combined with its
 * date into a string that sorts chronologically alongside CalendarEvent's
 * full ISO-ish `start` strings. */
function meetingTimeToSortable(time: string): string {
  const match = /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i.exec(time.trim());
  if (!match) return "00:00";
  let hour = Number(match[1]) % 12;
  if (/pm/i.test(match[3])) hour += 12;
  return `${String(hour).padStart(2, "0")}:${match[2]}`;
}

function eventToDayEvent(ev: CalendarEvent): DayEvent {
  return {
    source: "esterotoday",
    id: `esterotoday-${ev.id}`,
    dateKey: ev.start.slice(0, 10),
    sortKey: ev.start,
    title: ev.title,
    time: formatEventTime(ev),
    venue: ev.venue,
    url: ev.url,
    category: ev.category,
  };
}

function meetingToDayEvent(m: Meeting): DayEvent {
  return {
    source: "meeting",
    id: `meeting-${m.id}`,
    dateKey: m.date,
    sortKey: `${m.date}T${meetingTimeToSortable(m.time)}:00`,
    title: m.board,
    time: m.time,
    venue: m.venue,
    url: null,
    category: "village",
  };
}

/** Merges EsteroToday's Events Calendar feed (/api/events) with the
 * hand-maintained Village Council / PZDB schedule (public/meetings.json,
 * same source NextMeetings reads) into one per-day view. The calendar must
 * never show a dot NextMeetings doesn't agree with, or vice versa — see the
 * investigation note in this branch's summary for why these are two
 * genuinely independent sources with no shared id to dedupe against. */
function buildDayEvents(events: CalendarEvent[], meetings: Meeting[]): Map<string, DayEvent[]> {
  const map = new Map<string, DayEvent[]>();
  const push = (entry: DayEvent) => {
    const list = map.get(entry.dateKey) || [];
    list.push(entry);
    map.set(entry.dateKey, list);
  };
  events.forEach((ev) => push(eventToDayEvent(ev)));
  meetings.forEach((m) => push(meetingToDayEvent(m)));
  for (const list of map.values()) {
    list.sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  }
  return map;
}

const NEXT_N = 5;

export default function UpcomingEvents() {
  const { events, loading, error, retry } = useEvents();
  // Infinity, not the 3-item limit NextMeetings uses — the calendar needs
  // every meeting in view, not just the next few, so it can be navigated to
  // any month without silently dropping dots.
  const { meetings } = useMeetings(Infinity);

  const today = new Date();
  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());
  const [selectedDateKey, setSelectedDateKey] = useState<string | null>(null);

  const dayEvents = useMemo(() => buildDayEvents(events, meetings), [events, meetings]);
  const defaultList = useMemo(() => events.slice(0, NEXT_N).map(eventToDayEvent), [events]);
  const filteredList = selectedDateKey ? dayEvents.get(selectedDateKey) || [] : null;
  const displayList = filteredList ?? defaultList;

  const handleSelectDate = (dateKey: string) => {
    setSelectedDateKey((prev) => (prev === dateKey ? null : dateKey));
  };
  const clearFilter = () => setSelectedDateKey(null);

  return (
    <section className="pulse-widget pulse-widget-gold">
      <div className="pulse-widget-header">
        <h3>Upcoming Events</h3>
      </div>

      {loading && <SkeletonRows count={3} />}

      {!loading && error && (
        <p className="pulse-empty">
          Couldn't load events —{" "}
          <a href="https://esterotoday.com/events/" target="_blank" rel="noopener noreferrer">
            visit esterotoday.com/events ↗
          </a>
          <button type="button" className="pulse-retry" onClick={retry}>
            Retry
          </button>
        </p>
      )}

      {!loading && !error && (
        <>
          <MiniCalendar
            dayEvents={dayEvents}
            viewYear={viewYear}
            viewMonth={viewMonth}
            selectedDateKey={selectedDateKey}
            onNavigate={(year, month) => {
              setViewYear(year);
              setViewMonth(month);
            }}
            onSelectDate={handleSelectDate}
          />

          {selectedDateKey && (
            <div className="events-filter-chip">
              Showing {formatChipDate(selectedDateKey)} —{" "}
              <button type="button" className="events-filter-clear" onClick={clearFilter}>
                clear
              </button>
            </div>
          )}

          {displayList.length === 0 ? (
            <p className="pulse-empty">
              {selectedDateKey ? "No events that day." : "No upcoming events."}{" "}
              <a href="https://esterotoday.com/events/" target="_blank" rel="noopener noreferrer">
                See the full calendar ↗
              </a>
            </p>
          ) : (
            <ul className="events-list">
              {displayList.map((item) => {
                const { month, day } = formatDateBadge(item.dateKey);
                return (
                  <li key={item.id} className="event-row">
                    <div className="date-leaf">
                      <span className="date-leaf-month">{month}</span>
                      <span className="date-leaf-day">{day}</span>
                    </div>
                    <div className="event-info">
                      <div className="event-title-row">
                        {item.url ? (
                          <a className="event-title" href={item.url} target="_blank" rel="noopener noreferrer">
                            {item.title}
                          </a>
                        ) : (
                          <span className="event-title event-title-plain">{item.title}</span>
                        )}
                        <span
                          className={`event-pill event-pill-${item.category === "village" ? "village" : "engage"}`}
                        >
                          {item.category === "village" ? "Village" : "Engage Estero"}
                        </span>
                      </div>
                      <div className="event-meta">
                        {item.time}
                        {item.venue ? ` · ${item.venue}` : ""}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
