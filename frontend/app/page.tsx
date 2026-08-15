"use client";

import { useChatStore } from "../store/chat-store";
import { useGraphStore } from "../store/graph-store";
import { Sidebar } from "../components/sidebar/sidebar";
import { ChatWindow } from "../components/chat/chat-window";
import { WelcomeScreen } from "../components/chat/welcome-screen";
import { GraphPanel } from "../components/graph/graph-panel";
import { CitationCodeViewer } from "../components/chat/citation-code-viewer";
import { WelcomeModal } from "../components/auth/welcome-modal";
import { Button } from "../components/ui/button";
import { PanelLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ResolvedCitation } from "../lib/citations";

interface GraphEntityView {
  entityId: string;
  fileId: string;
  repoId: string;
}

export default function Home() {
  const { activeRepoId, activeConversationId, repoSessions, sidebarOpen, setSidebarOpen } = useChatStore();
  const { isOpen: graphOpen, activeRepoId: graphRepoId, graphData } = useGraphStore();

  // Derive active repo from either the active conversation or activeRepoId
  const activeRepo = activeConversationId
    ? repoSessions[activeRepoId ?? ""] ?? null
    : activeRepoId
    ? repoSessions[activeRepoId] ?? null
    : null;
  const graphRepo  = graphRepoId  ? repoSessions[graphRepoId]  : null;

  const [graphEntityView, setGraphEntityView] = useState<GraphEntityView | null>(null);

  // Increment this every time the graph panel opens — forces GraphPanel to
  // fully remount so its internal state (nodes, prevNodesLen, fitView) resets.
  const [graphMountKey, setGraphMountKey] = useState(0);
  const prevGraphOpen = useRef(false);
  useEffect(() => {
    if (graphOpen && !prevGraphOpen.current) {
      setGraphMountKey((k) => k + 1);
    }
    prevGraphOpen.current = graphOpen;
  }, [graphOpen]);

  const handleGraphEntityClick = (entityId: string, fileId: string) => {
    if (!graphRepoId) return;
    setGraphEntityView({ entityId, fileId, repoId: graphRepoId });
  };

  const graphCitation: ResolvedCitation | null = (() => {
    if (!graphEntityView || !graphData) return null;
    const fileNode = graphData.nodes.find((n) => n.id === graphEntityView.fileId);
    const entity   = fileNode?.entities?.find((e) => e.id === graphEntityView.entityId);
    return {
      raw: graphEntityView.entityId,
      file_path: fileNode?.file_path ?? "",
      start_line: entity?.start_line ?? 1,
      end_line:   entity?.end_line   ?? 1,
      matched_entity_id:   graphEntityView.entityId,
      matched_entity_name: entity?.name ?? graphEntityView.entityId.split(".").slice(-1)[0],
      citation_type: "definition",
      kind: "definition",
      caller_entity_name: null,
      callee_entity_name: null,
    } as ResolvedCitation;
  })();

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950">
      <WelcomeModal />
      <Sidebar />

      <main className="flex flex-1 min-w-0 h-full overflow-hidden">
        {!sidebarOpen && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-3 left-3 z-10 h-8 w-8 text-zinc-400 hover:text-white"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
            title="Open sidebar"
          >
            <PanelLeft className="h-4 w-4" />
          </Button>
        )}

        {graphOpen && graphRepo ? (
          <div className="w-full h-full flex flex-col overflow-hidden">
            {/* key changes every time graph opens → forces full remount →
                node state, prevNodesLen, and fitView all reset cleanly */}
            <GraphPanel
              key={`graph-${graphRepo.repoId}-${graphMountKey}`}
              repoId={graphRepo.repoId}
              onEntityClick={handleGraphEntityClick}
            />
          </div>
        ) : (
          <div className="w-full flex flex-col min-w-0 h-full">
            {activeRepo ? (
              <ChatWindow repo={activeRepo} />
            ) : (
              <WelcomeScreen />
            )}
          </div>
        )}
      </main>

      {graphEntityView && graphCitation && (
        <CitationCodeViewer
          repoId={graphEntityView.repoId}
          citation={graphCitation}
          citationIndex={null}
          onClose={() => setGraphEntityView(null)}
        />
      )}
    </div>
  );
}
