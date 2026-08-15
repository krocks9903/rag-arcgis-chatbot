import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../types";
import { isArticle } from "../../lib/parseAnswer";
import ProjectCard from "./ProjectCard";
import ArticleCard from "./ArticleCard";
import VillageCouncilCard from "./VillageCouncilCard";
import SourcesList from "./SourcesList";
import TypingIndicator from "./TypingIndicator";
import type { ReportPrefill } from "../ReportDialog/ReportDialog";

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const VISIBLE_CARDS = 4;

interface MessageProps {
  message: ChatMessage;
  onReport?: (prefill: ReportPrefill) => void;
}

export default function Message({ message, onReport }: MessageProps) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  if (message.role === "user") {
    return (
      <div className="msg-row">
        <div className="msg-user">
          <div className="bubble">{message.text}</div>
          <div className="msg-time">{formatTime(message.timestamp)}</div>
        </div>
      </div>
    );
  }

  const cards = message.cards || [];
  const visibleCards = expanded ? cards : cards.slice(0, VISIBLE_CARDS);
  const hiddenCount = cards.length - visibleCards.length;
  const showTyping = !!message.streaming && !message.prose && cards.length === 0;
  const hasContent = !!message.prose || cards.length > 0;
  const finished = !message.streaming;

  const copyAnswer = async () => {
    if (!message.prose) return;
    try {
      await navigator.clipboard.writeText(message.prose);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable — silently ignore
    }
  };

  return (
    <div className="msg-row">
      <div className="msg-bot">
        <div className="bot-avatar">🏛</div>
        <div className="bubble">
          {showTyping && <TypingIndicator />}
          {message.prose && (
            <div className="prose">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.prose}</ReactMarkdown>
            </div>
          )}
          {visibleCards.map((c, i) =>
            isArticle(c) ? (
              <ArticleCard key={i} card={c} />
            ) : c.sourceType === "village_council" ? (
              <VillageCouncilCard key={i} card={c} onReport={onReport} />
            ) : (
              <ProjectCard key={i} card={c} onReport={onReport} />
            ),
          )}
          {finished && hiddenCount > 0 && (
            <button type="button" className="btn-showmore" onClick={() => setExpanded(true)}>
              Show {hiddenCount} more source{hiddenCount === 1 ? "" : "s"}
            </button>
          )}
          {finished && !hasContent && !message.error && <div>Sorry, I couldn't find an answer.</div>}
          {finished && <SourcesList sources={message.sources || []} />}
          {finished && message.prose && (
            <div className="msg-actions">
              <button type="button" className="btn-copy" onClick={copyAnswer}>
                {copied ? "✓ Copied" : "⧉ Copy"}
              </button>
              <span className="msg-time">{formatTime(message.timestamp)}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
