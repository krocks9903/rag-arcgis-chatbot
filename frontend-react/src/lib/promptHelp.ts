/** Turn a place, project, or topic name into ready-to-send civic Q&A prompts. */

export function normalizeTopic(raw: string): string {
  return raw.trim().replace(/\s+/g, " ");
}

/** Sample prompts tuned to what this chatbot actually covers. */
export function buildSamplePrompts(topic: string): string[] {
  const t = normalizeTopic(topic);
  if (!t) return [];

  return [
    `What is the history and current status of ${t}?`,
    `Where is ${t} located in Estero, and what land-use or zoning decisions apply?`,
    `What has the Village Council or Planning, Zoning & Design Board decided about ${t}?`,
    `Summarize the most recent news and updates about ${t}.`,
    `Are there any pending applications, permits, or zoning changes related to ${t}?`,
    `What did the board discuss about ${t} at the most recent meeting?`,
  ];
}
