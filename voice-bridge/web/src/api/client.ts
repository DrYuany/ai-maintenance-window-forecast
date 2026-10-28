import type { Conversation, TranslationResult, Usage, Voice } from './types';
const BASE = (import.meta as ImportMeta & { env: { VITE_API_BASE?: string } }).env.VITE_API_BASE ?? '';
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { ...init, credentials: 'include' });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try { const body = await response.json(); const value = body.detail ?? detail; detail = typeof value === 'string' ? value : value.message ?? JSON.stringify(value); } catch { /* use fallback */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const api = {
  health: () => request<{ ok: boolean; configured: Record<string, boolean> }>('/api/health'),
  session: (password: string) => request<{ ok: boolean }>('/api/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) }),
  logout: () => request<{ ok: boolean }>('/api/session/logout', { method: 'POST' }),
  conversations: () => request<Conversation[]>('/api/conversations'),
  createConversation: (title: string) => request<Conversation>('/api/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) }),
  renameConversation: (id: string, title: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) }),
  deleteConversation: (id: string) => request<void>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  usage: (id: string) => request<Usage>(`/api/conversations/${encodeURIComponent(id)}/usage`),
  voices: () => request<Voice[]>('/api/voices'),
  cloneVoice: (audio: File, consent: boolean) => { const form = new FormData(); form.set('audio', audio); form.set('authorized', String(consent)); return request<Voice>('/api/voices/clone', { method: 'POST', body: form }); },
  translate: (audio: Blob, conversationId: string, summary: unknown, recentTurns: unknown[], speed: number, voiceId: string, requestId: string) => { const form = new FormData(); form.set('audio', audio, 'recording.webm'); form.set('conversation_id', conversationId); form.set('summary', JSON.stringify(summary)); form.set('recent_turns', JSON.stringify(recentTurns)); form.set('speed', String(speed)); form.set('voice_id', voiceId); form.set('request_id', requestId); return request<TranslationResult>('/api/translations', { method: 'POST', body: form }); },
  translationStatus: (id: string) => request<{ id: string; status: 'processing' | 'succeeded' | 'failed' | 'usage_pending'; detail?: string; usage?: Usage; result?: TranslationResult }>(`/api/translations/${encodeURIComponent(id)}`),
};
export const audioUrl = (path: string) => `${BASE}${path}`;
