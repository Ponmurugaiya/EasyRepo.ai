// ─────────────────────────────────────────────────────────────────────────────
// Graph store — file-level code graph panel state
// Entities are embedded in graphData.nodes — no separate expand/collapse needed
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import type { FileGraphResponse } from "@/types/graph";
import { getFileGraph } from "@/lib/graph-api";

interface GraphState {
  isOpen: boolean;
  activeRepoId: string | null;

  graphData: FileGraphResponse | null;
  loading: boolean;
  error: string | null;

  // Which file nodes are "expanded" (showing full entity list vs. condensed)
  expandedFiles: Set<string>;

  // Hover state
  hoveredNodeId: string | null;
  hoveredEdgeKey: string | null; // "sourceId::targetId"

  // Highlighted entity — set from chat citation "show in graph"
  highlightedEntityId: string | null;
  highlightedFileId: string | null;

  // Controls
  selectedRoot: string | null;   // what backend returned as root (for display)
  userRoot: string | null;       // what the user explicitly picked from the dropdown
  depth: number;
  includeImports: boolean;

  // Actions
  openGraph: (repoId: string, options?: { root?: string }) => Promise<void>;
  closeGraph: () => void;
  refreshGraph: (root?: string) => Promise<void>;
  setRoot: (entityId: string) => void;
  setDepth: (depth: number) => void;
  setIncludeImports: (v: boolean) => void;

  toggleExpand: (fileId: string) => void;
  expandAll: () => void;
  collapseAll: () => void;

  setHoveredNode: (id: string | null) => void;
  setHoveredEdge: (key: string | null) => void;

  highlightEntity: (entityId: string, repoId: string) => Promise<void>;
  clearHighlight: () => void;
}

export const useGraphStore = create<GraphState>((set, get) => ({
  isOpen: false,
  activeRepoId: null,
  graphData: null,
  loading: false,
  error: null,
  expandedFiles: new Set(),
  hoveredNodeId: null,
  hoveredEdgeKey: null,
  highlightedEntityId: null,
  highlightedFileId: null,
  selectedRoot: null,
  userRoot: null,
  depth: 4,
  includeImports: true,

  openGraph: async (repoId, options = {}) => {
    const { depth, includeImports } = get();
    const root = options.root ?? undefined;
    set({ isOpen: true, activeRepoId: repoId, loading: true, error: null, graphData: null, selectedRoot: null, userRoot: null });
    try {
      const data = await getFileGraph(repoId, { root, depth, includeImports });
      set({ graphData: data, selectedRoot: data.root ?? null, loading: false, expandedFiles: new Set() });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  closeGraph: () => set({ isOpen: false, hoveredNodeId: null, hoveredEdgeKey: null }),

  refreshGraph: async (root) => {
    const { activeRepoId, depth, includeImports, userRoot } = get();
    if (!activeRepoId) return;
    set({ loading: true, error: null, graphData: null });
    try {
      const data = await getFileGraph(activeRepoId, {
        // Only use userRoot if user explicitly picked one from the dropdown.
        // Never fall back to the backend-returned selectedRoot — it may be
        // a leaf node that returns only 1 result when used as BFS root.
        root: root ?? userRoot ?? undefined,
        depth,
        includeImports,
      });
      set({ graphData: data, selectedRoot: data.root ?? null, loading: false, expandedFiles: new Set() });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  setRoot: (entityId) => {
    set({ selectedRoot: entityId, userRoot: entityId }); // user explicitly picked
    get().refreshGraph(entityId);
  },
  setDepth: (depth) => { set({ depth }); get().refreshGraph(); },
  setIncludeImports: (v) => { set({ includeImports: v }); get().refreshGraph(); },

  toggleExpand: (fileId) => {
    const next = new Set(get().expandedFiles);
    if (next.has(fileId)) next.delete(fileId); else next.add(fileId);
    set({ expandedFiles: next });
  },

  expandAll: () => {
    const allIds = new Set(get().graphData?.nodes.map((n) => n.id) ?? []);
    set({ expandedFiles: allIds });
  },

  collapseAll: () => set({ expandedFiles: new Set() }),

  setHoveredNode: (id) => set({ hoveredNodeId: id }),
  setHoveredEdge: (key) => set({ hoveredEdgeKey: key }),

  highlightEntity: async (entityId, repoId) => {
    const { graphData } = get();
    let fileId: string | null = null;

    const findFile = (data: FileGraphResponse) => {
      // First try: direct entity ID match inside node entities (most reliable)
      for (const node of data.nodes) {
        if ((node.entities ?? []).some((e) => e.id === entityId)) return node.id;
      }
      // Fallback: prefix-match on node IDs (e.g. py.api.main.lifespan → py.api.main)
      const candidates = data.nodes
        .map((n) => n.id)
        .filter((nid) => entityId === nid || entityId.startsWith(nid + "."));
      return candidates.length > 0
        ? candidates.reduce((a, b) => (a.length > b.length ? a : b))
        : null;
    };

    if (graphData) fileId = findFile(graphData);

    set({ isOpen: true, highlightedEntityId: entityId, highlightedFileId: fileId });

    // Load graph if not yet loaded for this repo
    if (!get().activeRepoId || get().activeRepoId !== repoId) {
      await get().openGraph(repoId);
    }

    // Re-derive fileId from freshly loaded graph data (fixes stale-closure bug)
    const latestData = get().graphData;
    if (latestData) {
      fileId = findFile(latestData);
      set({ highlightedFileId: fileId });
    }

    // Ensure the file is expanded so the entity row is visible
    if (fileId) {
      const next = new Set(get().expandedFiles);
      next.add(fileId);
      set({ expandedFiles: next });
    }
  },

  clearHighlight: () => set({ highlightedEntityId: null, highlightedFileId: null }),
}));
