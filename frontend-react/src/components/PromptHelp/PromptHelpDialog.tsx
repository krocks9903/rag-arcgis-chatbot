import { useEffect, useState } from "react";
import { buildSamplePrompts, normalizeTopic } from "../../lib/promptHelp";

interface PromptHelpDialogProps {
  open: boolean;
  onClose: () => void;
  onPickPrompt: (prompt: string) => void;
}

type Step = "topic" | "prompts";

export default function PromptHelpDialog({ open, onClose, onPickPrompt }: PromptHelpDialogProps) {
  const [step, setStep] = useState<Step>("topic");
  const [topic, setTopic] = useState("");
  const [prompts, setPrompts] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    setStep("topic");
    setTopic("");
    setPrompts([]);
  }, [open]);

  if (!open) return null;

  const showPrompts = () => {
    const cleaned = normalizeTopic(topic);
    if (!cleaned) return;
    setPrompts(buildSamplePrompts(cleaned));
    setStep("prompts");
  };

  const pick = (prompt: string) => {
    onPickPrompt(prompt);
    onClose();
  };

  const backToTopic = () => {
    setStep("topic");
    setPrompts([]);
  };

  return (
    <div className="prompt-help-backdrop" role="presentation" onClick={onClose}>
      <div
        className="prompt-help-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-help-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="prompt-help-head">
          <h2 id="prompt-help-title">Help me ask a question</h2>
          <button type="button" className="prompt-help-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {step === "topic" ? (
          <div className="prompt-help-body">
            <p className="prompt-help-intro">
              Ask Engage Estero works best with a specific place, project, road, or development name.
              Tell us what you are curious about and we will suggest questions you can send.
            </p>
            <label className="prompt-help-label" htmlFor="prompt-help-topic">
              What topic would you like more clarity about?
            </label>
            <input
              id="prompt-help-topic"
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g. ALDI, Corkscrew Road, Bonita Estero Rail Trail"
              maxLength={120}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  showPrompts();
                }
              }}
            />
            <div className="prompt-help-actions">
              <button type="button" className="btn-prompt-secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-prompt-primary"
                onClick={showPrompts}
                disabled={!normalizeTopic(topic)}
              >
                Show sample prompts
              </button>
            </div>
          </div>
        ) : (
          <div className="prompt-help-body">
            <p className="prompt-help-intro">
              Tap a prompt to send it. Each question is written for Estero planning records, board
              minutes, and Engage Estero news coverage about{" "}
              <strong>{normalizeTopic(topic)}</strong>.
            </p>
            <ul className="prompt-help-list">
              {prompts.map((prompt) => (
                <li key={prompt}>
                  <button type="button" className="prompt-help-chip" onClick={() => pick(prompt)}>
                    {prompt}
                  </button>
                </li>
              ))}
            </ul>
            <div className="prompt-help-actions">
              <button type="button" className="btn-prompt-secondary" onClick={backToTopic}>
                Choose another topic
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
