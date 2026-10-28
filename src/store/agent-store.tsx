import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { apiClient } from "@/lib/api-client";
import type {
  CandidateWindow,
  Conversation,
  ConversationDetail,
  DashboardResponse,
  Message,
  PendingModelChange,
  PageId,
  SortMode,
  WorkingMemory,
  DecisionArtifact,
  SolveEvidence,
  SolveJob,
} from "@/store/types";
import { createOfflineDemo, isStandaloneFileProtocol } from "@/store/offline-demo";

interface AgentStoreValue {
  conversations: Conversation[];
  currentConversationId: string | null;
  currentView: PageId;
  workingMemory: WorkingMemory | null;
  messages: Message[];
  versions: DashboardResponse["versions"];
  candidateWindowPool: CandidateWindow[];
  topRecommendations: CandidateWindow[];
  sortMode: SortMode;
  loading: boolean;
  sending: boolean;
  streamStatus: string | null;
  streamingMessageId: string | null;
  error: string | null;
  decisionArtifact: DecisionArtifact | null;
  solveEvidence: SolveEvidence | null;
  activeJob: SolveJob | null;
  pendingChange: PendingModelChange | null;
  createConversation: () => Promise<void>;
  selectConversation: (conversationId: string) => Promise<void>;
  sendMessage: (
    message: string,
    selectedVersionId?: string | null,
    selectedWindowId?: string | null,
  ) => Promise<void>;
  setView: (view: PageId) => void;
  setSortMode: (sortMode: SortMode) => Promise<void>;
  deleteVersion: (versionId: string) => Promise<void>;
  deleteCandidate: (candidateId: string) => Promise<void>;
  reorderCandidate: (candidateId: string, direction: "up" | "down") => Promise<void>;
  toggleVersionInPool: (
    versionId: string,
    includedInCandidatePool: boolean,
  ) => Promise<void>;
  refreshCurrentConversation: () => Promise<void>;
  controlActiveJob: (action: "wait" | "background" | "use_incumbent" | "stop") => Promise<void>;
}

const AgentStoreContext = createContext<AgentStoreValue | null>(null);

function getEmptyDashboard(
  conversationId: string | null = null,
): DashboardResponse {
  return {
    conversationId: conversationId ?? "",
    sortMode: "latest_first",
    versions: [],
    candidateWindowPool: [],
    topRecommendations: [],
  };
}

export function AgentStoreProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const offline = typeof window !== "undefined" && isStandaloneFileProtocol(window.location.protocol);
  const offlineDemo = useMemo(() => createOfflineDemo(), []);
  const [conversations, setConversations] = useState<Conversation[]>(offline ? offlineDemo.conversations : []);
  const [currentConversationId, setCurrentConversationId] = useState<
    string | null
  >(offline ? offlineDemo.conversations[0].id : null);
  const [currentView, setCurrentView] = useState<PageId>("P01");
  const [workingMemory, setWorkingMemory] = useState<WorkingMemory | null>(
    null,
  );
  const [messages, setMessages] = useState<Message[]>(offline ? offlineDemo.messages : []);
  const [dashboard, setDashboard] = useState<DashboardResponse>(
    getEmptyDashboard(),
  );
  const [loading, setLoading] = useState(!offline);
  const [sending, setSending] = useState(false);
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisionArtifact, setDecisionArtifact] = useState<DecisionArtifact | null>(offline ? offlineDemo.decisionArtifact : null);
  const [solveEvidence, setSolveEvidence] = useState<SolveEvidence | null>(offline ? offlineDemo.solveEvidence : null);
  const [activeJob, setActiveJob] = useState<SolveJob | null>(null);
  const sendInFlight = useRef(false);

  const refreshConversationList = useCallback(async () => {
    const next = await apiClient.listConversations();
    setConversations(next);
    return next;
  }, []);

  const syncDashboard = useCallback(
    async (conversationId: string, nextSortMode: SortMode) => {
      const nextDashboard = await apiClient.getDashboard(
        conversationId,
        nextSortMode,
      );
      setDashboard(nextDashboard);
    },
    [],
  );

  const hydrateConversation = useCallback(
    async (conversationId: string, nextSortMode: SortMode) => {
      const [detail, nextDashboard, latestJob] = await Promise.all([
        apiClient.getConversation(conversationId),
        apiClient.getDashboard(conversationId, nextSortMode),
        apiClient.getLatestSolveJob(conversationId),
      ]);

      setCurrentConversationId(conversationId);
      setMessages(detail.messages);
      setWorkingMemory(detail.workingMemory);
      setCurrentView(detail.activeView ?? detail.workingMemory.activeView);
      setDashboard(nextDashboard);
      setActiveJob(latestJob);
      setDecisionArtifact(latestJob?.result?.decisionArtifact ?? null);
      if (latestJob) {
        setSolveEvidence({
          status: latestJob.solverStatus,
          hasIncumbent: latestJob.hasIncumbent,
          proof: latestJob.solverStatus === "OPTIMAL" || latestJob.solverStatus === "INFEASIBLE",
          elapsedSeconds: latestJob.elapsedSeconds,
          phase: latestJob.status.toLowerCase(),
          reason: latestJob.error,
          running: latestJob.status === "QUEUED" || latestJob.status === "RUNNING",
          modelFingerprint: latestJob.modelFingerprint,
        });
      } else {
        setSolveEvidence(null);
      }
    },
    [],
  );

  const selectConversation = useCallback(
    async (conversationId: string) => {
      setLoading(true);
      setError(null);
      try {
        await hydrateConversation(conversationId, dashboard.sortMode);
      } catch (nextError) {
        setError(
          nextError instanceof Error
            ? nextError.message
            : "加载会话失败，请稍后重试。",
        );
      } finally {
        setLoading(false);
      }
    },
    [dashboard.sortMode, hydrateConversation],
  );

  const createConversation = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const created = await apiClient.createConversation("新会话");
      await refreshConversationList();
      await hydrateConversation(created.id, "latest_first");
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "创建会话失败，请检查后端服务。",
      );
      setLoading(false);
    } finally {
      setLoading(false);
    }
  }, [hydrateConversation, refreshConversationList]);

  const refreshCurrentConversation = useCallback(async () => {
    if (!currentConversationId) return;

    setLoading(true);
    setError(null);
    try {
      await hydrateConversation(currentConversationId, dashboard.sortMode);
    } catch (nextError) {
      setError(
        nextError instanceof Error
          ? nextError.message
          : "刷新当前会话失败。",
      );
    } finally {
      setLoading(false);
    }
  }, [currentConversationId, dashboard.sortMode, hydrateConversation]);

  const sendMessage = useCallback(
    async (
      message: string,
      selectedVersionId?: string | null,
      selectedWindowId?: string | null,
    ) => {
      if (!currentConversationId || sendInFlight.current) return;

      sendInFlight.current = true;
      setSending(true);
      setError(null);
      const optimisticMessage: Message = {
        id: `temp_${Date.now()}`,
        conversationId: currentConversationId,
        role: "user",
        content: message,
        createdAt: new Date().toISOString(),
      };
      const assistantPlaceholder: Message = {
        id: `temp_reply_${Date.now()}`,
        conversationId: currentConversationId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
      };
      setMessages((previous) => [...previous, optimisticMessage, assistantPlaceholder]);
      if (offline) {
        setMessages((previous) => previous.map((item) =>
          item.id === assistantPlaceholder.id
            ? {
                ...item,
                content: "这是离线界面演示。通过 npm run dev 并启动 FastAPI 后端后，我会把这条需求编译成模型契约并调用真实求解器。",
              }
            : item,
        ));
        setSending(false);
        return;
      }
      let streamed = false;
      const applyResponse = async (response: AgentResponse) => {
        setDecisionArtifact(response.decisionArtifact ?? null);
        setSolveEvidence(response.solveEvidence ?? null);
        setActiveJob(response.solveJob ?? null);
        setCurrentView(response.recommendedView);
        await refreshConversationList();
        await hydrateConversation(currentConversationId, dashboard.sortMode);
      };
      setStreamingMessageId(assistantPlaceholder.id);
      setStreamStatus("正在读取事实…");
      try {
        const response = await apiClient.sendMessageStream(
          currentConversationId,
          message,
          selectedVersionId,
          selectedWindowId,
          {
            onStatus: (label) => setStreamStatus(label),
            onToken: (text) => {
              streamed = true;
              setStreamStatus(null);
              setMessages((previous) => previous.map((item) =>
                item.id === assistantPlaceholder.id
                  ? { ...item, content: item.content + text }
                  : item,
              ));
            },
            onReplace: (text) => {
              streamed = true;
              setStreamStatus(null);
              setMessages((previous) => previous.map((item) =>
                item.id === assistantPlaceholder.id
                  ? { ...item, content: text }
                  : item,
              ));
            },
          },
        );
        await applyResponse(response);
      } catch (nextError) {
        if (!streamed) {
          try {
            const response = await apiClient.sendMessage(
              currentConversationId,
              message,
              selectedVersionId,
              selectedWindowId,
            );
            await applyResponse(response);
          } catch (fallbackError) {
            setMessages((previous) =>
              previous.filter(
                (item) =>
                  item.id !== optimisticMessage.id
                  && item.id !== assistantPlaceholder.id,
              ),
            );
            setError(
              fallbackError instanceof Error
                ? fallbackError.message
                : "发送消息失败，请稍后再试。",
            );
          }
        } else {
          setError(
            nextError instanceof Error
              ? nextError.message
              : "流式回答中断，已保留本次对话。",
          );
          await hydrateConversation(currentConversationId, dashboard.sortMode);
        }
      } finally {
        sendInFlight.current = false;
        setSending(false);
        setStreamingMessageId(null);
        setStreamStatus(null);
      }
    },
    [currentConversationId, dashboard.sortMode, hydrateConversation, offline, refreshConversationList],
  );

  const controlActiveJob = useCallback(async (action: "wait" | "background" | "use_incumbent" | "stop") => {
    if (!activeJob) return;
    try {
      setActiveJob(await apiClient.controlSolveJob(activeJob.id, action));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "更新求解任务失败");
    }
  }, [activeJob]);

  useEffect(() => {
    if (!activeJob) return;
    // 多个场景时，即使当前任务已结束，只要批次还没跑完就要继续跟下去
    const jobRunning = ["QUEUED", "RUNNING"].includes(activeJob.status);
    if (!jobRunning && typeof activeJob.batchTotal !== "number") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const next = await apiClient.getSolveJob(activeJob.id);
        if (cancelled) return;
        setActiveJob(next);
        setSolveEvidence({
          status: next.solverStatus,
          hasIncumbent: next.hasIncumbent,
          proof: next.solverStatus === "OPTIMAL" || next.solverStatus === "INFEASIBLE",
          elapsedSeconds: next.elapsedSeconds,
          phase: next.status.toLowerCase(),
          reason: next.error,
          running: next.status === "QUEUED" || next.status === "RUNNING",
          modelFingerprint: next.modelFingerprint,
        });
        if (["COMPLETED", "FAILED", "CANCELLED"].includes(next.status) && currentConversationId) {
          if (next.result?.decisionArtifact) setDecisionArtifact(next.result.decisionArtifact);
          await refreshConversationList();
          await hydrateConversation(currentConversationId, dashboard.sortMode);
          // 这句话里还有别的场景没算，切到下一个场景的任务继续跟
          const latest = await apiClient.getLatestSolveJob(currentConversationId);
          if (
            !cancelled
            && latest
            && latest.id !== next.id
            && ["QUEUED", "RUNNING"].includes(latest.status)
          ) {
            setActiveJob(latest);
          }
        }
      } catch (nextError) {
        if (!cancelled) setError(nextError instanceof Error ? nextError.message : "读取求解进度失败");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [
    activeJob?.id,
    activeJob?.status,
    activeJob?.batchTotal,
    currentConversationId,
    dashboard.sortMode,
    hydrateConversation,
    refreshConversationList,
  ]);

  const setSortMode = useCallback(
    async (nextSortMode: SortMode) => {
      if (!currentConversationId) return;

      setError(null);
      try {
        await syncDashboard(currentConversationId, nextSortMode);
      } catch (nextError) {
        setError(
          nextError instanceof Error
            ? nextError.message
            : "更新排序失败。",
        );
      }
    },
    [currentConversationId, syncDashboard],
  );

  const deleteVersion = useCallback(
    async (versionId: string) => {
      if (!currentConversationId) return;
      try {
        await apiClient.deleteVersion(versionId);
        await syncDashboard(currentConversationId, dashboard.sortMode);
      } catch (nextError) {
        setError(
          nextError instanceof Error
            ? nextError.message
            : "删除版本失败。",
        );
      }
    },
    [currentConversationId, dashboard.sortMode, syncDashboard],
  );

  const deleteCandidate = useCallback(
    async (candidateId: string) => {
      if (!currentConversationId) return;
      try {
        await apiClient.deleteCandidate(candidateId);
        await syncDashboard(currentConversationId, dashboard.sortMode);
      } catch (nextError) {
        setError(
          nextError instanceof Error
            ? nextError.message
            : "删除候选窗口失败。",
        );
      }
    },
    [currentConversationId, dashboard.sortMode, syncDashboard],
  );

  const reorderCandidate = useCallback(
    async (candidateId: string, direction: "up" | "down") => {
      if (!currentConversationId || dashboard.sortMode !== "latest_first") {
        return;
      }

      const items = [...dashboard.candidateWindowPool];
      const currentIndex = items.findIndex((item) => item.id === candidateId);
      if (currentIndex < 0) return;

      const targetIndex =
        direction === "up" ? currentIndex - 1 : currentIndex + 1;
      if (targetIndex < 0 || targetIndex >= items.length) return;

      const reordered = [...items];
      const [current] = reordered.splice(currentIndex, 1);
      reordered.splice(targetIndex, 0, current);

      await apiClient.reorderCandidates(
        reordered.map((item, index) => ({
          id: item.id,
          displayOrder: reordered.length - index,
        })),
      );
      await syncDashboard(currentConversationId, dashboard.sortMode);
    },
    [currentConversationId, dashboard.candidateWindowPool, dashboard.sortMode, syncDashboard],
  );

  const toggleVersionInPool = useCallback(
    async (versionId: string, includedInCandidatePool: boolean) => {
      if (!currentConversationId) return;

      try {
        if (includedInCandidatePool) {
          await apiClient.includeVersion(versionId, true);
        } else {
          await apiClient.excludeVersion(versionId, false);
        }
        await syncDashboard(currentConversationId, dashboard.sortMode);
      } catch (nextError) {
        setError(
          nextError instanceof Error
            ? nextError.message
            : "更新版本候选状态失败。",
        );
      }
    },
    [currentConversationId, dashboard.sortMode, syncDashboard],
  );

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      if (offline) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const nextConversations = await refreshConversationList();
        if (cancelled) return;

        if (nextConversations.length === 0) {
          const created = await apiClient.createConversation("新会话");
          if (cancelled) return;
          await refreshConversationList();
          if (cancelled) return;
          await hydrateConversation(created.id, "latest_first");
          if (cancelled) return;
        } else {
          await hydrateConversation(nextConversations[0].id, "latest_first");
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(
            nextError instanceof Error
              ? nextError.message
              : "初始化失败，请确认前后端服务均已启动。",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void bootstrap();

    return () => {
      cancelled = true;
    };
  }, [hydrateConversation, offline, refreshConversationList]);

  const pendingChange = useMemo<PendingModelChange | null>(() => {
    const filters = workingMemory?.activeFilters as Record<string, unknown> | undefined;
    const pending = filters?.pendingChange;
    return pending && typeof pending === "object"
      ? pending as PendingModelChange
      : null;
  }, [workingMemory]);

  const value = useMemo<AgentStoreValue>(
    () => ({
      conversations,
      currentConversationId,
      currentView,
      workingMemory,
      messages,
      versions: dashboard.versions,
      candidateWindowPool: dashboard.candidateWindowPool,
      topRecommendations: dashboard.topRecommendations,
      sortMode: dashboard.sortMode,
      loading,
      sending,
      streamStatus,
      streamingMessageId,
      error,
      decisionArtifact,
      solveEvidence,
      activeJob,
      pendingChange,
      createConversation,
      selectConversation,
      sendMessage,
      setView: setCurrentView,
      setSortMode,
      deleteVersion,
      deleteCandidate,
      reorderCandidate,
      toggleVersionInPool,
      refreshCurrentConversation,
      controlActiveJob,
    }),
    [
      conversations,
      createConversation,
      currentConversationId,
      currentView,
      dashboard.candidateWindowPool,
      dashboard.sortMode,
      dashboard.topRecommendations,
      dashboard.versions,
      deleteCandidate,
      deleteVersion,
      error,
      decisionArtifact,
      solveEvidence,
      activeJob,
      pendingChange,
      loading,
      messages,
      refreshCurrentConversation,
      controlActiveJob,
      reorderCandidate,
      selectConversation,
      sending,
      streamStatus,
      setSortMode,
      streamingMessageId,
      toggleVersionInPool,
      workingMemory,
    ],
  );

  return (
    <AgentStoreContext.Provider value={value}>
      {children}
    </AgentStoreContext.Provider>
  );
}

export function useAgentStore() {
  const context = useContext(AgentStoreContext);
  if (!context) {
    throw new Error("useAgentStore must be used within AgentStoreProvider");
  }
  return context;
}
