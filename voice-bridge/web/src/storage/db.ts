import { openDB, type DBSchema } from 'idb';
import type { Conversation } from '../api/types';
interface VoiceBridgeDB extends DBSchema { conversations: { key: string; value: Conversation; indexes: { 'by-updated': number } }; settings: { key: string; value: unknown }; }
const dbPromise = openDB<VoiceBridgeDB>('voice-bridge', 1, { upgrade(db) { const chats = db.createObjectStore('conversations', { keyPath: 'id' }); chats.createIndex('by-updated', 'updatedAt'); db.createObjectStore('settings'); } });
export async function getConversations() { const db = await dbPromise; return db.getAllFromIndex('conversations', 'by-updated'); }
export async function getConversation(id: string) { return (await dbPromise).get('conversations', id); }
export async function saveConversation(item: Conversation) { return (await dbPromise).put('conversations', item); }
export async function removeConversation(id: string) { return (await dbPromise).delete('conversations', id); }
export async function getSetting<T>(key: string, fallback: T): Promise<T> { const value = await (await dbPromise).get('settings', key); return (value as T | undefined) ?? fallback; }
export async function saveSetting(key: string, value: unknown) { return (await dbPromise).put('settings', value, key); }
