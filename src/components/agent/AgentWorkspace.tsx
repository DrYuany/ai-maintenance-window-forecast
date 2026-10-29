import { useCallback, useEffect, useState } from "react";
import { useAgentStore } from "../../store/agent-store";
import { ConversationCanvas } from "./ConversationCanvas";
import { ConversationRail } from "./ConversationRail";
import { PlanDetailDrawer } from "./PlanDetailDrawer";
import { PlanHistoryPanel } from "./PlanHistoryPanel";
import { SolveWaitDialog } from "./SolveWaitDialog";
import { sortVersionsChronologically } from "./plan-format";

export function AgentWorkspace() {
  const {
    conversations,
    currentConversationId,
    workingMemory,
    messages,
    loading,
    sending,
    streamStatus,
    streamingMessageId,
    error,
    decisionArtifact,
    activeJob,
    pendingChange,
    stages,
    versions,
    createConversation,
    selectConversation,
    sendMessage,
    controlActiveJob,
  } = useAgentStore();
  const [contextVersionId, setContextVersionId] = useState<string | null>(null);
  const [detailVersionId, setDetailVersionId] = useState<string | null>(null);
  const chronologicalVersions = sortVersionsChronologically(versions);
  const contextVersion =
    versions.find((item) => item.id === contextVersionId) ?? versions[0] ?? null;
  const detailVersion = versions.find((item) => item.id === detailVersionId) ?? null;
  const detailPlanNumber = detailVersion
    ? chronologicalVersions.findIndex((item) => item.id === detailVersion.id) + 1
    : null;
  const openVersion = useCallback((versionId: string) => {
    setContextVersionId(versionId);
    setDetailVersionId(versionId);
  }, []);
  const closeVersion = useCallback(() => {
    setDetailVersionId(null);
  }, []);

  useEffect(() => {
    setContextVersionId(null);
    setDetailVersionId(null);
  }, [currentConversationId]);

  const persistedContextVersionId = workingMemory?.selectedVersionIds?.[0] ?? null;
  useEffect(() => {
    if (
      persistedContextVersionId
      && versions.some((item) => item.id === persistedContextVersionId)
    ) {
      setContextVersionId(persistedContextVersionId);
    }
  }, [persistedContextVersionId, versions]);

  return (
    <div className="agent-workspace">
      <ConversationRail
        conversations={conversations}
        activeId={currentConversationId}
        onCreate={() => void createConversation()}
        onSelect={(id) => void selectConversation(id)}
      />
      {loading ? (
        <main className="agent-loading" aria-label="Agent 对话">正在恢复会话…</main>
      ) : (
        <ConversationCanvas
          messages={messages}
          versions={versions}
          sending={sending}
          streamStatus={streamStatus}
          streamingMessageId={streamingMessageId}
          activeJob={activeJob}
          pendingChange={pendingChange}
          stages={stages}
          contextVersion={contextVersion}
          onClearContext={() => setContextVersionId(null)}
          onSend={(message) =>
            sendMessage(
              message,
              message === "确认生成沙盘" && pendingChange?.baseVersionId
                ? pendingChange.baseVersionId
                : contextVersion?.id ?? null,
            )
          }
          onOpenVersion={openVersion}
          onControlJob={(action) => controlActiveJob(action)}
        />
      )}
      {error ? <div className="agent-error">{error}</div> : null}
      <PlanHistoryPanel
        versions={versions}
        activeJob={activeJob}
        selectedVersionId={contextVersion?.id ?? null}
        onOpenVersion={openVersion}
      />
      <PlanDetailDrawer
        version={detailVersion}
        planNumber={detailPlanNumber}
        decisionArtifact={decisionArtifact}
        job={activeJob}
        onClose={closeVersion}
      />
      <SolveWaitDialog
        open={Boolean(activeJob?.needsUserDecision)}
        elapsedSeconds={activeJob?.elapsedSeconds ?? 0}
        hasIncumbent={activeJob?.hasIncumbent ?? false}
        onAction={(action) => void controlActiveJob(action)}
      />
    </div>
  );
}
