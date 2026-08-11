import { useState } from "react";
import { API_BASE } from "../../lib/config";
import { apiHeaders, getDeviceId } from "../../lib/deviceId";
import type { ChatMessage } from "../../types";

type Rating = "up" | "down";

export default function FeedbackBar({ message }: { message: ChatMessage }) {
  const [rating, setRating] = useState<Rating | null>(message.feedbackRating ?? null);
  const [thanks, setThanks] = useState(!!message.feedbackRating);

  const submit = async (next: Rating) => {
    if (rating) return;
    setRating(next);
    try {
      await fetch(`${API_BASE}/feedback`, {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify({
          session_id: getDeviceId(),
          question: message.question || "",
          rating: next,
          route: message.route || "",
          summary: (message.prose || "").slice(0, 2000),
          project_ids: (message.cards || []).map((c) => c.id).filter(Boolean),
          meta: message.feedbackMeta || {},
        }),
      });
    } catch (e) {
      console.warn("feedback failed", e);
    }
    setThanks(true);
  };

  return (
    <div className="feedback-bar">
      <span className="feedback-label">Was this helpful?</span>
      <button
        type="button"
        className={`feedback-btn${rating === "up" ? " selected" : ""}`}
        data-rating="up"
        aria-label="Helpful"
        disabled={!!rating}
        onClick={() => void submit("up")}
      >
        👍
      </button>
      <button
        type="button"
        className={`feedback-btn${rating === "down" ? " selected" : ""}`}
        data-rating="down"
        aria-label="Not helpful"
        disabled={!!rating}
        onClick={() => void submit("down")}
      >
        👎
      </button>
      {thanks && <span className="feedback-thanks">Thanks for the feedback.</span>}
    </div>
  );
}
