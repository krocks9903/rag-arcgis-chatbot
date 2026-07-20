import type { ChatMessage } from "../../types";
import { useAutoScroll } from "../../hooks/useAutoScroll";
import Message from "./Message";

export default function MessageList({ messages }: { messages: ChatMessage[] }) {
  const { ref, onScroll } = useAutoScroll<HTMLDivElement>([messages]);

  return (
    <div
      id="messages"
      className="visible"
      ref={ref}
      onScroll={onScroll}
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      aria-label="Conversation"
    >
      {messages.map((m) => (
        <Message key={m.id} message={m} />
      ))}
    </div>
  );
}
