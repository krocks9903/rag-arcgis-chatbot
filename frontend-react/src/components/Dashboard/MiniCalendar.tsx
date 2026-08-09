import { useMemo, useState } from "react";
import type { DayEvent } from "../../types";

interface MiniCalendarProps {
  dayEvents: Map<string, DayEvent[]>;
  viewYear: number;
  viewMonth: number; // 0-indexed, like Date.getMonth()
  selectedDateKey: string | null;
  onNavigate: (year: number, month: number) => void;
  onSelectDate: (dateKey: string) => void;
}

interface DayCell {
  dateKey: string; // YYYY-MM-DD, also used as the React key
  day: number;
  inMonth: boolean;
  isToday: boolean;
  events: DayEvent[];
}

const WEEKDAY_LABELS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MONTH_LABELS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Builds a YYYY-MM-DD key from a (possibly out-of-range) month/day pair by
 * letting the Date constructor normalize month overflow/underflow (e.g.
 * month=-1 rolls back into December of the previous year), then reading the
 * normalized fields back out — avoids hand-rolling year-rollover math. */
function toDateKey(year: number, month: number, day: number): string {
  const d = new Date(year, month, day);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function buildGrid(year: number, month: number, dayEvents: Map<string, DayEvent[]>): DayCell[] {
  const firstWeekday = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrevMonth = new Date(year, month, 0).getDate();
  const today = new Date();
  const todayKey = toDateKey(today.getFullYear(), today.getMonth(), today.getDate());

  const cells: DayCell[] = [];
  const pushCell = (m: number, day: number, inMonth: boolean) => {
    const dateKey = toDateKey(year, m, day);
    cells.push({
      dateKey,
      day: Number(dateKey.slice(8, 10)),
      inMonth,
      isToday: dateKey === todayKey,
      events: dayEvents.get(dateKey) || [],
    });
  };

  for (let i = 0; i < firstWeekday; i++) {
    pushCell(month - 1, daysInPrevMonth - firstWeekday + 1 + i, false);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    pushCell(month, day, true);
  }
  let trailingDay = 1;
  while (cells.length % 7 !== 0) {
    pushCell(month + 1, trailingDay, false);
    trailingDay++;
  }
  return cells;
}

/** Grid is always 7 columns, row-major, and the first cell in the array is
 * always the Sunday column — so index % 7 gives the column (0=Sun..6=Sat)
 * for any cell without extra date math. Used to keep the hover tooltip from
 * overflowing the panel: edge columns anchor the tooltip to that edge
 * instead of centering it. */
function tooltipAlignForColumn(index: number): "left" | "center" | "right" {
  const col = index % 7;
  if (col <= 1) return "left";
  if (col >= 5) return "right";
  return "center";
}

export default function MiniCalendar({
  dayEvents,
  viewYear,
  viewMonth,
  selectedDateKey,
  onNavigate,
  onSelectDate,
}: MiniCalendarProps) {
  const cells = useMemo(() => buildGrid(viewYear, viewMonth, dayEvents), [viewYear, viewMonth, dayEvents]);
  const [hoveredDateKey, setHoveredDateKey] = useState<string | null>(null);

  const goPrev = () => {
    const d = new Date(viewYear, viewMonth - 1, 1);
    onNavigate(d.getFullYear(), d.getMonth());
  };
  const goNext = () => {
    const d = new Date(viewYear, viewMonth + 1, 1);
    onNavigate(d.getFullYear(), d.getMonth());
  };

  return (
    <div className="mini-cal">
      <div className="mini-cal-header">
        <button type="button" className="mini-cal-nav" onClick={goPrev} aria-label="Previous month">
          ‹
        </button>
        <span className="mini-cal-label">
          {MONTH_LABELS[viewMonth]} {viewYear}
        </span>
        <button type="button" className="mini-cal-nav" onClick={goNext} aria-label="Next month">
          ›
        </button>
      </div>

      <div className="mini-cal-grid">
        {WEEKDAY_LABELS.map((label) => (
          <div className="mini-cal-weekday" key={label}>
            {label}
          </div>
        ))}

        {cells.map((cell, index) => {
          const hasVillage = cell.events.some((e) => e.category === "village");
          const hasEngage = cell.events.some((e) => e.category === "engage-estero");
          const hasEvents = cell.events.length > 0;
          const isSelected = cell.dateKey === selectedDateKey;
          const isHovered = cell.dateKey === hoveredDateKey;

          const dayClasses = [
            "mini-cal-day",
            !cell.inMonth && "mini-cal-day-out",
            hasEvents && "mini-cal-day-clickable",
            isSelected && "mini-cal-day-selected",
          ]
            .filter(Boolean)
            .join(" ");

          const circleClasses = [
            "mini-cal-day-circle",
            cell.isToday && "mini-cal-day-circle-today",
            !cell.isToday && hasVillage && hasEngage && "mini-cal-day-circle-split",
            !cell.isToday && hasVillage && !hasEngage && "mini-cal-day-circle-village",
            !cell.isToday && hasEngage && !hasVillage && "mini-cal-day-circle-engage",
          ]
            .filter(Boolean)
            .join(" ");

          const dayDate = new Date(`${cell.dateKey}T00:00:00`);
          const longLabel = dayDate.toLocaleDateString("en-US", { month: "long", day: "numeric" });
          const ariaLabel = hasEvents
            ? `${longLabel}, ${cell.events.length} event${cell.events.length === 1 ? "" : "s"}`
            : longLabel;

          const clearHover = () => setHoveredDateKey((k) => (k === cell.dateKey ? null : k));

          return (
            <div className="mini-cal-day-wrap" key={cell.dateKey}>
              <button
                type="button"
                className={dayClasses}
                disabled={!hasEvents}
                onClick={() => onSelectDate(cell.dateKey)}
                onMouseEnter={() => hasEvents && setHoveredDateKey(cell.dateKey)}
                onMouseLeave={clearHover}
                onFocus={() => hasEvents && setHoveredDateKey(cell.dateKey)}
                onBlur={clearHover}
                aria-label={ariaLabel}
              >
                <span className={circleClasses}>{cell.day}</span>
              </button>
              {isHovered && hasEvents && (
                <div
                  className={`mini-cal-tooltip mini-cal-tooltip-${tooltipAlignForColumn(index)}`}
                  role="tooltip"
                >
                  {cell.events.map((ev) => (
                    <div className="mini-cal-tooltip-item" key={ev.id}>
                      <span className="mini-cal-tooltip-title">{ev.title}</span>
                      <span className="mini-cal-tooltip-time">{ev.time}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mini-cal-legend">
        <span className="mini-cal-legend-item">
          <span className="mini-cal-legend-swatch mini-cal-legend-swatch-village" />
          Village of Estero
        </span>
        <span className="mini-cal-legend-item">
          <span className="mini-cal-legend-swatch mini-cal-legend-swatch-engage" />
          Engage Estero
        </span>
      </div>
    </div>
  );
}
