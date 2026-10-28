export type Turn = { id: string; source: string; translation: string; createdAt: number; audioUrl?: string; tokens?: number; audioSeconds?: number };
export type Conversation = { id: string; title: string; createdAt: number; updatedAt: number; deleted?: boolean; turns: Turn[]; summary: Summary; usage?: Usage };
export type Summary = { topic: string; entities_and_glossary: string[]; translation_preferences: string[]; unresolved_references: string[] };
export type Usage = { audio_seconds_used: number; audio_seconds_remaining: number; llm_tokens_used: number; llm_tokens_remaining: number; device_audio_seconds_remaining: number; device_llm_tokens_remaining: number };
export type Voice = { id: string; name: string; description: string; kind: 'preset' | 'cloned'; enabled: boolean };
export type TranslationResult = { id: string; status: string; transcript: string; translation: string; summary: Summary; tokens: number; audio_seconds: number; audio_url?: string; usage: Usage };
export const EMPTY_SUMMARY: Summary = { topic: '', entities_and_glossary: [], translation_preferences: [], unresolved_references: [] };
