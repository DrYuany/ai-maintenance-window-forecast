import { EMPTY_SUMMARY, type Conversation, type Summary, type Turn } from '../api/types';
import { getConversation, getConversations, removeConversation, saveConversation } from './db';
export async function listLocalConversations() { return getConversations(); }
export async function addLocalConversation(id: string, title: string) { const now = Date.now(); const item: Conversation = { id, title, createdAt: now, updatedAt: now, turns: [], summary: EMPTY_SUMMARY }; await saveConversation(item); return item; }
export async function appendTurn(id: string, turn: Turn, summary: Summary, usage?: Conversation['usage']) { const item = await getConversation(id); if (!item) throw new Error('找不到本地对话'); item.turns.push(turn); item.summary = summary; item.updatedAt = Date.now(); if (!item.title || item.title === '新对话') item.title = turn.source.slice(0, 18) || '新对话'; item.usage = usage ?? item.usage; await saveConversation(item); return item; }
export async function deleteLocalConversation(id: string) { return removeConversation(id); }
