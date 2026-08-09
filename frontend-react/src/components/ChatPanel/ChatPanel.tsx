import type { ChatMessage } from "../../types";
import Hero from "./Hero";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";

interface ChatPanelProps {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  disabled: boolean;
}

export default function ChatPanel({ messages, onSend, disabled }: ChatPanelProps) {
  const started = messages.length > 0;

  return (
    <section id="chat-panel">
      {started ? <MessageList messages={messages} /> : <Hero />}
      {/* DatasetBar (Load CSV) intentionally hidden — the dataset already loads
          at backend startup. Component and POST /load left in place; see
          DatasetBar.tsx to re-enable. */}
      <ChatInput onSend={onSend} disabled={disabled} />
    </section>
  );
}
