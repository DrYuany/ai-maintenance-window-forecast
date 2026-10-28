import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api/client';
import { EMPTY_SUMMARY, type Conversation, type Turn, type Usage, type Voice } from './api/types';
import { beginRecording, type RecordingHandle } from './audio/recorder';
import { readRms } from './audio/silence-detector';
import { SessionGate } from './features/session/SessionGate';
import { ConversationPanel } from './features/conversations/ConversationPanel';
import { RecordControl } from './features/recording/RecordControl';
import { ResultCard } from './features/results/ResultCard';
import { VoicePanel } from './features/voices/VoicePanel';
import { addLocalConversation, appendTurn, deleteLocalConversation, listLocalConversations } from './storage/conversations';
import { getSetting, saveConversation, saveSetting } from './storage/db';

type Status = 'checking' | 'logged-out' | 'ready';
type Pane = 'record' | 'voices' | 'settings';
export default function App() {
  const [status, setStatus] = useState<Status>('checking');
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [threshold, setThreshold] = useState(3);
  const [speed, setSpeed] = useState(1);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState('');
  const [usage, setUsage] = useState<Usage | null>(null);
  const [voiceError, setVoiceError] = useState('');
  const [cloning, setCloning] = useState(false);
  const [pane, setPane] = useState<Pane>('record');
  const [pendingRequestId, setPendingRequestId] = useState('');
  const [pendingStatus, setPendingStatus] = useState('');
  const [sessionChecked, setSessionChecked] = useState(false);
  const handle = useRef<RecordingHandle | null>(null);
  const blob = useRef<Blob | null>(null);
  const recorderStarted = useRef(0);
  const cancelCountdown = useRef(false);
  const active = conversations.find(item => item.id === activeId);

  const refreshLocal = useCallback(async (preferred?: string) => {
    const items = (await listLocalConversations()).sort((a, b) => b.updatedAt - a.updatedAt);
    setConversations(items);
    setActiveId(current => preferred ?? (items.some(x => x.id === current) ? current : items[0]?.id ?? ''));
    return items;
  }, []);
  const refreshUsage = useCallback(async (id: string) => {
    try {
      const value = await api.usage(id);
      setUsage(value);
      const item = (await listLocalConversations()).find(x => x.id === id);
      if (item) { item.usage = value; await saveConversation(item); }
    } catch { /* Session can expire; the next protected call returns the user to sign in. */ }
  }, []);
  const loadSessionData = useCallback(async () => {
    const remote = await api.conversations();
    const local = await listLocalConversations();
    for (const item of remote) {
      const cached = local.find(x => x.id === item.id);
      if (cached) { item.turns = cached.turns; item.summary = cached.summary; item.usage = cached.usage; }
      else await addLocalConversation(item.id, item.title);
    }
    setConversations(remote);
    setActiveId(remote[0]?.id ?? '');
    if (remote[0]) await refreshUsage(remote[0].id);
    setVoices(await api.voices());
    setStatus('ready');
  }, [refreshUsage]);

  useEffect(() => {
    if (!sessionChecked || status !== 'ready') return;
    const timer = window.setInterval(() => {
      void api.conversations().then(remote => {
        void (async () => {
          const local = await listLocalConversations();
          for (const item of remote) {
            const cached = local.find(entry => entry.id === item.id);
            if (cached) { item.turns = cached.turns; item.summary = cached.summary; item.usage = cached.usage; }
            else await addLocalConversation(item.id, item.title);
          }
          const activeItem = remote.find(item => item.id === activeId);
          if (activeItem) await refreshUsage(activeItem.id);
          setConversations(remote);
        })();
      }).catch(() => undefined);
    }, 12000);
    return () => window.clearInterval(timer);
  }, [sessionChecked, status, activeId, refreshUsage]);
  useEffect(() => {
    void (async () => {
      const [savedThreshold, savedSpeed] = await Promise.all([getSetting('silenceThreshold', 3), getSetting('speechSpeed', 1)]);
      setThreshold(savedThreshold); setSpeed(savedSpeed);
      try { await api.health(); await loadSessionData(); }
      catch { setStatus('logged-out'); }
      finally { setSessionChecked(true); }
    })();
  }, [loadSessionData]);
  useEffect(() => { if (status === 'ready' && activeId) void refreshUsage(activeId); }, [activeId, status, refreshUsage]);
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => {
      const seconds = Math.floor((Date.now() - recorderStarted.current) / 1000);
      setElapsed(seconds);
      if (seconds >= 600) void stopAndTranslate();
    }, 250);
    return () => window.clearInterval(timer);
  }, [recording]);
  useEffect(() => {
    if (!handle.current || !recording) return;
    let raf = 0; let quietAt: number | null = null; let countdownTimer: number | null = null; let countdownValue: number | null = null;
    const tick = () => {
      const rms = readRms(handle.current!.analyser);
      if (rms < 0.018) {
        quietAt ??= Date.now();
        if (Date.now() - quietAt >= threshold * 1000 && countdownValue === null) {
          countdownValue = 3; setCountdown(3); cancelCountdown.current = false;
          countdownTimer = window.setInterval(() => {
            if (cancelCountdown.current) { if (countdownTimer !== null) clearInterval(countdownTimer); countdownTimer = null; countdownValue = null; setCountdown(null); return; }
            countdownValue = (countdownValue ?? 0) - 1; setCountdown(countdownValue);
            if (countdownValue <= 0) { if (countdownTimer !== null) clearInterval(countdownTimer); countdownTimer = null; setCountdown(null); void stopAndTranslate(); }
          }, 1000);
        }
      } else {
        quietAt = null;
        if (countdownValue !== null) { cancelCountdown.current = true; countdownValue = null; setCountdown(null); }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); if (countdownTimer !== null) clearInterval(countdownTimer); };
  }, [recording, threshold]);
  useEffect(() => {
    if (!pendingRequestId) return;
    const timer = window.setInterval(() => {
      void api.translationStatus(pendingRequestId).then(result => {
        setPendingStatus(result.status);
        if (result.status === 'failed') { setPendingRequestId(''); setPendingStatus(''); setError(result.detail ?? '上次云端处理失败，可以重试。'); if (activeId) void refreshUsage(activeId); }
        if (result.status === 'succeeded') { setPendingRequestId(''); setPendingStatus(''); if (result.result) void acceptResult(result.result); }
        if (result.status === 'usage_pending') setPendingStatus('云端用量对账中，暂不可重试');
      }).catch(() => setPendingStatus('正在恢复上次请求状态…'));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pendingRequestId, activeId, refreshUsage]);

  async function onLogin(password: string) {
    setBusy(true); setError('');
    try { await api.session(password); await loadSessionData(); }
    catch (e) { setError(e instanceof Error ? e.message : '登录失败'); }
    finally { setBusy(false); }
  }
  async function createConversation() {
    try {
      const item = await api.createConversation('新对话');
      await addLocalConversation(item.id, item.title);
      await refreshLocal(item.id); setPane('record'); await refreshUsage(item.id); setError('');
    } catch (e) { setError(e instanceof Error ? e.message : '创建对话失败'); }
  }
  async function renameConversation(item: Conversation) {
    const title = window.prompt('给这段对话起个名字', item.title);
    if (!title?.trim()) return;
    try { await api.renameConversation(item.id, title.trim()); const local = (await listLocalConversations()).find(x => x.id === item.id); if (local) { local.title = title.trim(); local.updatedAt = Date.now(); await saveConversation(local); } await refreshLocal(item.id); }
    catch (e) { setError(e instanceof Error ? e.message : '改名失败'); }
  }
  async function removeConversation(item: Conversation) {
    if (!window.confirm(`永久删除“${item.title}”？本机历史和剩余额度都无法恢复。`)) return;
    try { await api.deleteConversation(item.id); const next = await deleteLocalConversation(item.id); void next; const items = await refreshLocal(); if (items[0]) await refreshUsage(items[0].id); setError(''); }
    catch (e) { setError(e instanceof Error ? e.message : '删除失败'); }
  }
  async function startRecording() {
    setError('');
    if (!activeId) { await createConversation(); return; }
    try { handle.current = await beginRecording(); blob.current = null; recorderStarted.current = Date.now(); setElapsed(0); setCountdown(null); setRecording(true); }
    catch (e) { setError(e instanceof Error ? e.message : '无法访问麦克风'); }
  }
  async function stopAndTranslate() {
    const current = handle.current;
    if (!current || busy) return;
    setRecording(false); setCountdown(null); setBusy(true);
    try { blob.current = await current.stop(); current.close(); handle.current = null; }
    catch (e) { current.close(); handle.current = null; setBusy(false); setError(e instanceof Error ? e.message : '录音结束失败'); return; }
    setBusy(false);
    if (!blob.current) return;
    if (!window.confirm(`录音已完成（${Math.max(1, Math.round((Date.now() - recorderStarted.current) / 1000))} 秒）。现在开始识别并翻译吗？\n音频仅在你确认后上传。`)) return;
    await submitRecording();
  }
  async function acceptResult(result: import('./api/types').TranslationResult) {
    if (!activeId) return;
    const turn: Turn = { id: result.id, source: result.transcript, translation: result.translation, createdAt: Date.now(), audioUrl: result.audio_url, tokens: result.tokens, audioSeconds: result.audio_seconds };
    await appendTurn(activeId, turn, result.summary, result.usage); blob.current = null; await refreshLocal(activeId); setUsage(result.usage);
  }
  async function submitRecording() {
    if (!blob.current || !active) return;
    setBusy(true); setError('');
    const requestId = pendingRequestId || crypto.randomUUID();
    try {
      const result = await api.translate(blob.current, active.id, active.summary ?? EMPTY_SUMMARY, active.turns.slice(-6).map(turn => ({ source: turn.source, translation: turn.translation })), speed, voiceId, requestId);
      await acceptResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : '处理失败');
      try {
        const state = await api.translationStatus(requestId);
        if (state.status === 'succeeded' && state.result) { setPendingRequestId(''); setPendingStatus(''); await acceptResult(state.result); }
        else if (state.status === 'failed') { setPendingRequestId(''); setPendingStatus(''); }
        else { setPendingRequestId(requestId); setPendingStatus('正在查询上次请求状态…'); }
      } catch { setPendingRequestId(requestId); setPendingStatus('正在查询上次请求状态…'); }
    } finally { setBusy(false); }
  }
  async function cloneVoice(file: File, consent: boolean) {
    setCloning(true); setVoiceError('');
    try { const voice = await api.cloneVoice(file, consent); setVoices(await api.voices()); setVoiceId(voice.id); setPane('voices'); }
    catch (e) { setVoiceError(e instanceof Error ? e.message : '音色创建失败'); }
    finally { setCloning(false); }
  }
  const continueSpeaking = () => { cancelCountdown.current = true; setCountdown(null); };
  const updateSpeed = (value: number) => { setSpeed(value); void saveSetting('speechSpeed', value); };

  if (status === 'checking') return <div className="loading-screen"><div className="brand-mark">声</div><span>正在唤醒随声译…</span></div>;
  if (status === 'logged-out') return <SessionGate onSubmit={onLogin} busy={busy} error={error}/>;
  return <main className="app-shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark small">声</span><span>随声译<small>VOICE BRIDGE</small></span></a><div className="top-actions"><span className="status-pill"><i/>本地记录</span><button className="avatar" title="退出" onClick={async () => { await api.logout().catch(() => undefined); setStatus('logged-out'); }}>我</button></div></header>
    <div className="work-area"><ConversationPanel items={conversations} selected={activeId} onSelect={id => { setActiveId(id); setPane('record'); }} onCreate={() => void createConversation()} onRename={item => void renameConversation(item)} onDelete={item => void removeConversation(item)} disabled={busy || recording}/>
      <section className="main-column"><div className="mobile-chat-switch"><select value={activeId} onChange={event => setActiveId(event.target.value)}>{conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button onClick={() => void createConversation()}>＋</button></div>
        <header className="section-heading"><div><span className="eyebrow">A QUIETER WAY TO THINK</span><h1>{pane === 'record' ? '说出来，听见另一种表达。' : pane === 'voices' ? '找到你的声音。' : '调整你的节奏。'}</h1><p>{pane === 'record' ? '讲完再翻译，停顿由你掌控。' : '你的设置保存在这台设备。'}</p></div><nav><button className={pane === 'record' ? 'nav-active' : ''} onClick={() => setPane('record')}>翻译</button><button className={pane === 'voices' ? 'nav-active' : ''} onClick={() => setPane('voices')}>声音</button><button className={pane === 'settings' ? 'nav-active' : ''} onClick={() => setPane('settings')}>设置</button></nav></header>
        {error && <div className="alert error-banner"><span>{pendingStatus ? `${error} · ${pendingStatus}` : error}</span>{blob.current && !pendingRequestId && <button onClick={() => void submitRecording()} disabled={busy}>重试翻译</button>}</div>}
        <div className="content-grid"><div className="primary-content">
          {pane === 'record' && <><div className="record-card"><div className="record-card-head"><div><span className="eyebrow">CHINESE → ENGLISH</span><h2>{active?.title ?? '新对话'}</h2></div><span className="mode-tag">讲完再播报</span></div><RecordControl recording={recording} elapsed={elapsed} countdown={countdown} onStart={() => void startRecording()} onStop={() => void stopAndTranslate()} onContinue={continueSpeaking} busy={busy} disabled={!activeId}/><div className="record-footer"><span><i/>音频只会在你确认后上传</span><button onClick={() => setPane('settings')}>静音间隔 {threshold}s <b>调节</b></button></div></div>
            <div className="quota-card"><div><span className="eyebrow">THIS CONVERSATION</span><b>云端用量</b></div><div className="quota-line"><span>音频时长</span><b>{usage ? `${Math.floor(usage.audio_seconds_used / 3600)}h ${Math.floor((usage.audio_seconds_used % 3600) / 60)}m 已使用` : '加载中'}</b></div><div className="quota-line"><span>模型 token</span><b>{usage ? `${usage.llm_tokens_used.toLocaleString()} / 1,500,000` : '加载中'}</b></div><small>设备剩余：{usage ? `${Math.floor(usage.device_audio_seconds_remaining / 3600)} 小时 · ${usage.device_llm_tokens_remaining.toLocaleString()} tokens` : '加载中'}。达到任一上限后将停止云端处理。</small></div>
            <div className="results-head"><div><span className="eyebrow">YOUR TRANSLATION JOURNAL</span><h2>这一段的回声</h2></div><span>{active?.turns.length ?? 0} 段</span></div>
            {active?.turns.length ? <div className="results-list">{[...active.turns].reverse().map((turn, index) => <ResultCard key={turn.id} turn={turn} index={active.turns.length - 1 - index} speed={speed} onSpeed={updateSpeed}/>)}</div> : <div className="empty-state"><span className="empty-wave">≈</span><b>这里还很安静</b><p>开始录音后，原文、译文和英文播报会出现在这里。</p></div>}
          </>}
          {pane === 'voices' && <div className="settings-card"><VoicePanel voices={voices} selected={voiceId} onSelect={setVoiceId} onClone={(file, consent) => void cloneVoice(file, consent)} busy={cloning} error={voiceError}/></div>}
          {pane === 'settings' && <div className="settings-card"><span className="eyebrow">RECORDING & PLAYBACK</span><h2>把节奏调成你的。</h2><div className="setting-row"><div><b>停顿判定</b><small>一段话停多久才算讲完</small></div><strong>{threshold} 秒</strong></div><input className="wide-range" type="range" min="1" max="10" step="1" value={threshold} onChange={event => { const value = Number(event.target.value); setThreshold(value); void saveSetting('silenceThreshold', value); }}/><div className="setting-row speed-row"><div><b>播报语速</b><small>英文语音播放的倍速</small></div><strong>{speed.toFixed(1)}×</strong></div><input className="wide-range" type="range" min="0.5" max="2" step="0.1" value={speed} onChange={event => updateSpeed(Number(event.target.value))}/><p className="settings-note">录音时不会向云端实时发送。录完后先确认，再开始识别、翻译与播报。</p><button className="text-button" onClick={() => setPane('voices')}>管理播报声音 →</button></div>}
        </div><aside className="right-column"><VoicePanel voices={voices} selected={voiceId} onSelect={setVoiceId} onClone={(file, consent) => void cloneVoice(file, consent)} busy={cloning} error={voiceError}/><div className="context-note"><span className="spark">✳</span><div><b>上下文会跟上你的思路</b><p>常用专名、语气和前后指代会留在这段对话里。</p></div></div></aside></div>
      </section></div><footer className="mobile-footer"><button className={pane === 'record' ? 'selected' : ''} onClick={() => setPane('record')}>⌁<small>口述</small></button><button className={pane === 'voices' ? 'selected' : ''} onClick={() => setPane('voices')}>◉<small>声音</small></button><button className={pane === 'settings' ? 'selected' : ''} onClick={() => setPane('settings')}>⚙<small>设置</small></button></footer>
  </main>;
}
