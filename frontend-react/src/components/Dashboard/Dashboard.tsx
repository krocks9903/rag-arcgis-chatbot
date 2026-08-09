import type { Meeting } from "../../types";
import NextMeetings from "./NextMeetings";
import LatestNews from "./LatestNews";
// RecentDecisions replaced by UpcomingEvents below — component kept in case
// we bring it back, just not rendered.
import UpcomingEvents from "./UpcomingEvents";

interface DashboardProps {
  meetings: Meeting[];
  meetingsLoading: boolean;
  meetingsError: string | null;
  onSend: (text: string) => void;
}

export default function Dashboard({ meetings, meetingsLoading, meetingsError, onSend: _onSend }: DashboardProps) {
  return (
    <div id="pulse-dashboard">
      <NextMeetings meetings={meetings} loading={meetingsLoading} error={meetingsError} />
      <LatestNews />
      <UpcomingEvents />
    </div>
  );
}
