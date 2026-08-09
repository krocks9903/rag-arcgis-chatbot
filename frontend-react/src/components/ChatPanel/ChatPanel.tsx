import type { ChatMessage } from "../../types";
import Hero from "./Hero";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";
import type { ReportPrefill } from "../ReportDialog/ReportDialog";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  disabled: boolean;
  onReport?: (prefill: ReportPrefill) => void;
}

export default function ChatPanel({ messages, onSend, disabled, onReport }: ChatPanelProps) {
  const started = messages.length > 0;

  return (
    <section id="chat-panel">
      {started ? <MessageList messages={messages} onReport={onReport} /> : <Hero />}
      {/* DatasetBar (Load CSV) intentionally hidden — the dataset already loads
          at backend startup. Component and POST /load left in place; see
          DatasetBar.tsx to re-enable. */}
      <ChatInput onSend={onSend} disabled={disabled} />
    </section>
  );
}
