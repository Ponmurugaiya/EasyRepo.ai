// ─────────────────────────────────────────────────────────────────────────────
// Graph store — expandable file-level code graph
//
// All graph data is fetched once (show_all=true). Visibility is controlled
// client-side via visibleNodeIds:
//
//   Initial state: entry point + its direct neighbours
//   + badge:       node has hidden neighbours → click to reveal them
//   − badge:       node was expanded by user  → click to hide its neighbours
//                  (nodes shared with other expanded nodes stay visible)
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import type { FileGraphResponse, FileEdge } from "../types/graph";
import { getFileGraph } from "../lib/graph-api";

interface GraphState {
  isOpen: boolean;
  activeRepoId: string | null;

  // Full graph data fetched from backend (all nodes + all edges)
  graphData: FileGraphResponse | null;
  loading: boolean;
  error: string | null;

  // ── Visibility (expandable graph) ──────────────────────────────────────────
  // Set of node IDs currently shown in the canvas
  visibleNodeIds: Set<string>;
  // Set of node IDs the user explicitly expanded (shows − badge)
  userExpandedIds: Set<string>;

  // ── Source-code expand (entity list inside node card) ─────────────────────
  expandedFiles: Set<string>;

  // Hover / highlight
  hoveredNodeId: string | null;
  hoveredEdgeKey: string | null;
  highlightedEntityId: string | null;
  highlightedFileId: string | null;

  // Controls
  selectedRoot: string | null;
  includeImports: boolean;

  // ── Actions ───────────────────────────────────────────────────────────────
  openGraph: (repoId: string, options?: { root?: string }) => Promise<void>;
  closeGraph: () => void;
  refreshGraph: () => Promise<void>;

  // Expand a node: add its direct neighbours to visibleNodeIds
  expandNode: (nodeId: string) => void;
  // Collapse a node: remove neighbours that were only visible because of this node
  collapseNode: (nodeId: string) => void;

  setRoot: (entityId: string) => void;
  setIncludeImports: (v: boolean) => void;

  toggleExpand: (fileId: string) => void;
  expandAll: () => void;
  collapseAll: () => void;

  setHoveredNode: (id: string | null) => void;
  setHoveredEdge: (key: string | null) => void;

  highlightEntity: (entityId: string, repoId: string) => Promise<void>;
  clearHighlight: () => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/** All neighbour IDs (outgoing + incoming) for a given node across all edges. */
function neighboursOf(nodeId: string, edges: FileEdge[], includeImports: boolean): Set<string> {
  const result = new Set<string>();
  for (const e of edges) {
    if (!includeImports && e.dominant_type === "IMPORTS") continue;
    if (e.source_file_id === nodeId) result.add(e.target_file_id);
    if (e.target_file_id === nodeId) result.add(e.source_file_id);
  }
  return result;
}

/** Compute initial visible set: entry point + all its direct neighbours. */
function initialVisible(data: FileGraphResponse, includeImports: boolean): Set<string> {
  const visible = new Set<string>();

  // Pick the best entry point (first in list = highest scored), or first node
  const rootId = data.entry_points[0] ?? data.nodes[0]?.id;
  if (!rootId) return visible;

  visible.add(rootId);
  for (const nb of neighboursOf(rootId, data.edges, includeImports)) {
    visible.add(nb);
  }
  return visible;
}

/**
 * BFS shortest path between two node IDs using all edges.
 * Returns the list of node IDs from start to end (inclusive), or null if
 * no path exists within maxHops.
 */
function shortestPath(
  startId: string,
  endId: string,
  edges: FileEdge[],
  maxHops = 20,
): string[] | null {
  if (startId === endId) return [startId];

  // Build undirected adjacency from all edges (ignore import filter here —
  // we want to find any path that exists in the graph)
  const adj = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adj.has(e.source_file_id)) adj.set(e.source_file_id, new Set());
    if (!adj.has(e.target_file_id)) adj.set(e.target_file_id, new Set());
    adj.get(e.source_file_id)!.add(e.target_file_id);
    adj.get(e.target_file_id)!.add(e.source_file_id);
  }

  const visited = new Map<string, string | null>(); // nodeId → parent
  visited.set(startId, null);
  const queue: Array<[string, number]> = [[startId, 0]];

  while (queue.length > 0) {
    const [current, depth] = queue.shift()!;
    if (depth >= maxHops) continue;
    for (const nb of adj.get(current) ?? []) {
      if (visited.has(nb)) continue;
      visited.set(nb, current);
      if (nb === endId) {
        // Reconstruct path
        const path: string[] = [];
        let cursor: string | null = endId;
        while (cursor !== null) {
          path.unshift(cursor);
          cursor = visited.get(cursor) ?? null;
        }
        return path;
      }
      queue.push([nb, depth + 1]);
    }
  }
  return null;
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useGraphStore = create<GraphState>((set, get) => ({
  isOpen: false,
  activeRepoId: null,
  graphData: null,
  loading: false,
  error: null,
  visibleNodeIds: new Set(),
  userExpandedIds: new Set(),
  expandedFiles: new Set(),
  hoveredNodeId: null,
  hoveredEdgeKey: null,
  highlightedEntityId: null,
  highlightedFileId: null,
  selectedRoot: null,
  includeImports: true,

  openGraph: async (repoId, options = {}) => {
    const { includeImports } = get();
    set({
      isOpen: true,
      activeRepoId: repoId,
      loading: true,
      error: null,
      graphData: null,
      visibleNodeIds: new Set(),
      userExpandedIds: new Set(),
      selectedRoot: null,
    });
    try {
      // Always fetch everything; visibility is client-side
      const data = await getFileGraph(repoId, { showAll: true });
      const visible = initialVisible(data, includeImports);
      const rootId = options.root ?? data.entry_points[0] ?? data.nodes[0]?.id ?? null;
      set({
        graphData: data,
        visibleNodeIds: visible,
        userExpandedIds: new Set(),
        selectedRoot: rootId,
        loading: false,
        expandedFiles: new Set(),
      });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  closeGraph: () => set({ isOpen: false, hoveredNodeId: null, hoveredEdgeKey: null }),

  refreshGraph: async () => {
    const { activeRepoId, includeImports } = get();
    if (!activeRepoId) return;
    set({ loading: true, error: null, graphData: null, visibleNodeIds: new Set(), userExpandedIds: new Set() });
    try {
      const data = await getFileGraph(activeRepoId, { showAll: true });
      const visible = initialVisible(data, includeImports);
      const rootId = data.entry_points[0] ?? data.nodes[0]?.id ?? null;
      set({
        graphData: data,
        visibleNodeIds: visible,
        userExpandedIds: new Set(),
        selectedRoot: rootId,
        loading: false,
        expandedFiles: new Set(),
      });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  expandNode: (nodeId) => {
    const { graphData, visibleNodeIds, userExpandedIds, includeImports } = get();
    if (!graphData) return;
    const neighbours = neighboursOf(nodeId, graphData.edges, includeImports);
    const next = new Set(visibleNodeIds);
    for (const nb of neighbours) next.add(nb);
    const nextExpanded = new Set(userExpandedIds);
    nextExpanded.add(nodeId);
    set({ visibleNodeIds: next, userExpandedIds: nextExpanded });
  },

  collapseNode: (nodeId) => {
    const { graphData, visibleNodeIds, userExpandedIds, includeImports, selectedRoot } = get();
    if (!graphData) return;

    // Remove this node from userExpandedIds
    const nextExpanded = new Set(userExpandedIds);
    nextExpanded.delete(nodeId);

    // Re-compute which nodes should remain visible:
    // - The initial visible set (entry + its neighbours)
    // - Plus neighbours of all still-expanded nodes
    const recomputed = initialVisible(graphData, includeImports);
    for (const expandedId of nextExpanded) {
      recomputed.add(expandedId);
      for (const nb of neighboursOf(expandedId, graphData.edges, includeImports)) {
        recomputed.add(nb);
      }
    }

    set({ visibleNodeIds: recomputed, userExpandedIds: nextExpanded });
  },

  setRoot: (entityId) => {
    const { graphData, includeImports } = get();
    if (!graphData) return;
    // Re-seed visibility from the new root
    const visible = new Set<string>();
    visible.add(entityId);
    for (const nb of neighboursOf(entityId, graphData.edges, includeImports)) {
      visible.add(nb);
    }
    set({ selectedRoot: entityId, visibleNodeIds: visible, userExpandedIds: new Set() });
  },

  setIncludeImports: (v) => {
    set({ includeImports: v });
    // Re-seed from current root with updated edge filter
    const { graphData, selectedRoot, userExpandedIds } = get();
    if (!graphData) return;
    const rootId = selectedRoot ?? graphData.entry_points[0] ?? graphData.nodes[0]?.id;
    if (!rootId) return;
    const visible = new Set<string>();
    visible.add(rootId);
    for (const nb of neighboursOf(rootId, graphData.edges, v)) visible.add(nb);
    for (const expandedId of userExpandedIds) {
      visible.add(expandedId);
      for (const nb of neighboursOf(expandedId, graphData.edges, v)) visible.add(nb);
    }
    set({ visibleNodeIds: visible });
  },

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
    const { includeImports } = get();

    // ── Find file node containing this entity ────────────────────────────────
    const findFileId = (data: FileGraphResponse): string | null => {
      for (const node of data.nodes) {
        if ((node.entities ?? []).some((e) => e.id === entityId)) return node.id;
      }
      const candidates = data.nodes
        .map((n) => n.id)
        .filter((nid) => entityId === nid || entityId.startsWith(nid + "."));
      return candidates.length > 0
        ? candidates.reduce((a, b) => (a.length > b.length ? a : b))
        : null;
    };

    set({ isOpen: true, highlightedEntityId: entityId, highlightedFileId: null });

    // Load graph data if not already loaded for this repo
    if (!get().activeRepoId || get().activeRepoId !== repoId) {
      await get().openGraph(repoId);
    }

    const data = get().graphData;
    if (!data) return;

    const fileId = findFileId(data);
    set({ highlightedFileId: fileId });
    if (!fileId) return;

    // ── Build path-based visibility ──────────────────────────────────────────
    // Show: entry → cited file via shortest path, each node + depth-1 neighbours.
    const entryId = data.entry_points[0] ?? data.nodes[0]?.id;
    const path = entryId ? shortestPath(entryId, fileId, data.edges) : null;

    const visible = new Set<string>();
    const userExpanded = new Set<string>();

    if (path && path.length > 0) {
      for (const pathNode of path) {
        visible.add(pathNode);
        for (const nb of neighboursOf(pathNode, data.edges, includeImports)) {
          visible.add(nb);
        }
        // Mark each non-root path node as user-expanded so − badge appears
        if (pathNode !== entryId) {
          userExpanded.add(pathNode);
        }
      }
    } else {
      // Disconnected — fall back to initial view + target node
      for (const id of initialVisible(data, includeImports)) visible.add(id);
      visible.add(fileId);
      for (const nb of neighboursOf(fileId, data.edges, includeImports)) {
        visible.add(nb);
      }
      userExpanded.add(fileId);
    }

    set({
      visibleNodeIds: visible,
      userExpandedIds: userExpanded,
      selectedRoot: entryId ?? null,
      expandedFiles: new Set([fileId]),  // open source view for cited file
    });
  },

  clearHighlight: () => set({ highlightedEntityId: null, highlightedFileId: null }),
}));
