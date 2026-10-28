import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  Database,
  Layers3,
  Paperclip,
  Play,
  Send,
  ShieldCheck,
  Sparkles,
  User,
  X,
} from "lucide-react";
import type {
  Message,
  PendingModelChange,
  SolveJob,
  WindowVersion,
} from "../../store/types";
import { ScheduleResultCard } from "./ScheduleResultCard";
import { MessageBody } from "./MessageBody";
import { sortVersionsChronologically } from "./plan-format";

interface Props {
  messages: Message[];
  versions: WindowVersion[];
  sending: boolean;
  streamStatus?: string | null;
  streamingMessageId?: string | null;
  activeJob?: SolveJob | null;
  pendingChange?: PendingModelChange | null;
  contextVersion?: WindowVersion | null;
  onSend: (message: string) => Promise<void>;
  onOpenVersion: (versionId: string) => void;
  onClearContext?: () => void;
}

const suggestions = [
  "按高风险优先重新排程",
  "船租上涨 20%，比较成本变化",
  "每天最多维修 8 小时",
];

function buildMessageVersionLinks(messages: Message[], versions: WindowVersion[]) {
  const links = new Map<string, WindowVersion[]>();
  const claimed = new Set<string>();
  const byId = new Map(versions.map((version) => [version.id, version]));

  for (const message of messages) {
    if (message.role !== "assistant" || !message.versionIds?.length) continue;
    const attached = message.versionIds
      .map((versionId) => byId.get(versionId))
      .filter((version): version is WindowVersion => Boolean(version));
    if (!attached.length) continue;
    links.set(message.id, attached);
    attached.forEach((version) => claimed.add(version.id));
  }

  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const orphans: WindowVersion[] = [];

  for (const version of versions) {
    if (claimed.has(version.id)) continue;
    const generatedAt = Date.parse(version.generatedAt);
    let closest: Message | null = null;
    let closestDistance = Number.POSITIVE_INFINITY;

    if (Number.isFinite(generatedAt)) {
      for (const message of assistantMessages) {
        const messageTime = Date.parse(message.createdAt);
        if (!Number.isFinite(messageTime)) continue;
        const distance = Math.abs(messageTime - generatedAt);
        if (distance < closestDistance) {
          closest = message;
          closestDistance = distance;
        }
      }
    }

    if (closest && closestDistance <= 2 * 60 * 1000) {
      links.set(closest.id, [...(links.get(closest.id) ?? []), version]);
      claimed.add(version.id);
    } else {
      orphans.push(version);
    }
  }

  return { links, orphans };
}

export function ConversationCanvas({
  messages,
  versions = [],
  sending,
  streamStatus = null,
  streamingMessageId = null,
  activeJob,
  pendingChange,
  contextVersion,
  onSend,
  onOpenVersion,
  onClearContext = () => undefined,
}: Props) {
  const [value, setValue] = useState("");
  const submitting = useRef(false);
  const threadEnd = useRef<HTMLDivElement>(null);
  const solving = activeJob?.status === "QUEUED" || activeJob?.status === "RUNNING";
  const chronologicalVersions = useMemo(
    () => sortVersionsChronologically(versions),
    [versions],
  );
  const planNumbers = useMemo(
    () => new Map(chronologicalVersions.map((version, index) => [version.id, index + 1])),
    [chronologicalVersions],
  );
  const { links, orphans } = useMemo(
    () => buildMessageVersionLinks(messages, chronologicalVersions),
    [messages, chronologicalVersions],
  );
  const latestVersionId = chronologicalVersions.at(-1)?.id;
  const lastMessageLength = messages.at(-1)?.content.length ?? 0;

  useEffect(() => {
    if (typeof threadEnd.current?.scrollIntoView === "function") {
      threadEnd.current.scrollIntoView({
        behavior: streamingMessageId ? "auto" : "smooth",
        block: "end",
        inline: "nearest",
      });
    }
  }, [messages.length, solving, latestVersionId, lastMessageLength, streamingMessageId]);

  const submit = async (next = value) => {
    const text = next.trim();
    if (!text || sending || submitting.current) return;
    submitting.current = true;
    setValue("");
    try {
      await onSend(text);
    } finally {
      submitting.current = false;
    }
  };

  return (
    <main className="agent-canvas" aria-label="Agent 对话">
      <header className="agent-topbar">
        <div>
          <strong>检修排程 Agent</strong>
          <span><i />规划数据已锁定 · 东海一号风场</span>
        </div>
        <div className="topbar-mode-group">
          <span className="safe-mode"><ShieldCheck size={13} />审计模式</span>
          <button type="button">本轮解释 <ChevronDown size={14} /></button>
        </div>
      </header>

      <div className="agent-thread">
        {messages.length === 0 ? (
          <div className="agent-empty">
            <div className="empty-agent-mark"><Bot size={25} /></div>
            <h2>从一句检修目标开始</h2>
            <p>
              告诉我优先目标、预算或时间约束。我会先锁定模型契约，再调用真实
              CP-SAT 求解，并把每次方案保留在对话里。
            </p>
            <div className="empty-capabilities">
              <span><Database size={14} />数据快照可追溯</span>
              <span><ShieldCheck size={14} />可行性单独复核</span>
              <span><Sparkles size={14} />每轮方案不覆盖</span>
            </div>
          </div>
        ) : null}

        {messages.map((message) => {
          const attachedVersions = links.get(message.id) ?? [];
          return (
            <article key={message.id} className={`thread-message ${message.role}`}>
              <div className="message-avatar">
                {message.role === "assistant" ? <Bot size={17} /> : <User size={17} />}
              </div>
              <div className="message-stack">
                <div className="message-author">
                  {message.role === "assistant" ? "检修排程 Agent" : "你"}
                </div>
                <div className="message-content">
                  <MessageBody
                    content={message.content.replace(
                      "详细任务和审计证据已写入右侧结果区。",
                      "点击下方方案卡查看完整排程和审计证据。",
                    )}
                    streaming={message.id === streamingMessageId}
                  />
                </div>
                {attachedVersions.length ? (
                  <div className="message-result-stack">
                    {attachedVersions.map((version) => (
                      <ScheduleResultCard
                        key={version.id}
                        version={version}
                        planNumber={planNumbers.get(version.id) ?? 1}
                        onOpen={onOpenVersion}
                      />
                    ))}
                  </div>
                ) : null}
              </div>
            </article>
          );
        })}

        {orphans.length ? (
          <article className="thread-message assistant">
            <div className="message-avatar"><Bot size={17} /></div>
            <div className="message-stack">
              <div className="message-author">检修排程 Agent</div>
              <div className="message-content">
                以下方案已保存在本次会话中，但原对话记录没有留下关联标记。
              </div>
              <div className="message-result-stack">
                {orphans.map((version) => (
                  <ScheduleResultCard
                    key={version.id}
                    version={version}
                    planNumber={planNumbers.get(version.id) ?? 1}
                    onOpen={onOpenVersion}
                  />
                ))}
              </div>
            </div>
          </article>
        ) : null}

        {(sending && !streamingMessageId) || solving ? (
          <div className="agent-thinking" aria-live="polite">
            <div className="thinking-line">
              <span /><span /><span />
              {sending
                ? streamStatus || "正在读取事实并组织回答"
                : (activeJob?.statusMessage || `真实求解进行中 · 已运行 ${activeJob?.elapsedSeconds ?? 0} 秒`)}
            </div>
            {solving ? (
              <div className="solve-progress">
                <i style={{ width: `${activeJob?.progressPercent ?? 15}%` }} />
                <small>{activeJob?.progressPercent ?? 15}% · {activeJob?.elapsedSeconds ?? 0}s</small>
              </div>
            ) : null}
          </div>
        ) : null}
        <div ref={threadEnd} />
      </div>

      <footer className="agent-composer-wrap">
        {contextVersion ? (
          <div className="agent-context-bar">
            <span><Layers3 size={13} />当前模型上下文</span>
            <strong>
              方案 {String(planNumbers.get(contextVersion.id) ?? 1).padStart(2, "0")}
            </strong>
            <small>
              {contextVersion.windows[0]
                ? `${contextVersion.windows[0].windowId} · ${contextVersion.windows[0].metrics.startTime.slice(0, 10)}`
                : contextVersion.focusTag}
            </small>
            <button type="button" onClick={onClearContext}>跟随最新</button>
          </div>
        ) : null}
        {pendingChange ? (
          <div className="pending-change-card" aria-live="polite">
            <div className="pending-change-head">
              <span><Sparkles size={14} />待确认模型变更</span>
              <strong>{pendingChange.operations.length} 项</strong>
            </div>
            <div className="pending-change-list">
              {pendingChange.operations.slice(0, 4).map((operation) => (
                <span key={`${operation.kind}-${operation.key}`}>
                  <small>{operation.key}</small>
                  <strong>{String(operation.after ?? "移除")}</strong>
                </span>
              ))}
            </div>
            <div className="pending-change-actions">
              <button
                type="button"
                className="confirm"
                disabled={sending}
                onClick={() => void submit("确认生成沙盘")}
              >
                <Play size={13} />确认生成沙盘
              </button>
              <button
                type="button"
                disabled={sending}
                onClick={() => void submit("取消变更")}
              >
                <X size={13} />取消
              </button>
            </div>
          </div>
        ) : null}
        <div className="suggestion-row">
          {suggestions.map((item) => (
            <button
              type="button"
              key={item}
              disabled={sending}
              onClick={() => void submit(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="agent-composer">
          <textarea
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="描述目标、约束或要调整的方案…"
          />
          <div className="composer-actions">
            <button type="button" aria-label="添加附件"><Paperclip size={17} /></button>
            <span>Enter 发送 · Shift+Enter 换行</span>
            <button
              type="button"
              className="send-agent-message"
              aria-label="发送"
              disabled={!value.trim() || sending}
              onClick={() => void submit()}
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      </footer>
    </main>
  );
}
