"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type EdgeChange,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";

import { X, RefreshCw, Loader2, AlertTriangle, GitBranch, Maximize2, Minimize2 } from "lucide-react";
import { cn } from "../../lib/utils";
import { useGraphStore } from "../../store/graph-store";
import { applyDagreLayout, NODE_WIDTH, nodeHeight } from "../../lib/graph-layout";
import { NODE_TYPES, EDGE_TYPES } from "./graph-types";
import type { FileNode, FileEdge, InlineEntity } from "../../types/graph";
import type { FileNodeData } from "./file-node";
import type { GraphEdgeData } from "./graph-edge";

interface GraphPanelProps {
  repoId: string;
  onEntityClick?: (entityId: string, fileId: string) => void;
}

export function GraphPanel({ repoId, onEntityClick }: GraphPanelProps) {
  const {
    isOpen, closeGraph,
    graphData, loading, error,
    visibleNodeIds, userExpandedIds,
    expandedFiles, toggleExpand,
    highlightedEntityId, highlightedFileId,
    selectedRoot,
    includeImports, setIncludeImports,
    setRoot, expandNode, collapseNode,
    refreshGraph, openGraph,
    expandAll, collapseAll,
  } = useGraphStore();

  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  // Handle node/edge changes (drag, select).
  // Skip "dimensions" — React Flow measures nodes after layout and would
  // truncate our array to just the measured nodes (the "1 node" bug).
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const safe = changes.filter((c) => c.type !== "dimensions" && c.type !== "remove");
    if (safe.length > 0) setNodes((nds) => applyNodeChanges(safe, nds));
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const safe = changes.filter((c) => c.type !== "remove");
    if (safe.length > 0) setEdges((eds) => applyEdgeChanges(safe, eds));
  }, []);

  // fitView ref
  const fitViewRef = useRef<(() => void) | null>(null);
  const prevNodesLen = useRef(0);
  useEffect(() => { prevNodesLen.current = 0; }, []);

  // Stable refs to avoid re-creating buildLayout on every render
  const onEntityClickRef = useRef(onEntityClick);
  useEffect(() => { onEntityClickRef.current = onEntityClick; }, [onEntityClick]);

  const toggleExpandRef = useRef(toggleExpand);
  useEffect(() => { toggleExpandRef.current = toggleExpand; }, [toggleExpand]);

  const expandNodeRef = useRef(expandNode);
  useEffect(() => { expandNodeRef.current = expandNode; }, [expandNode]);

  const collapseNodeRef = useRef(collapseNode);
  useEffect(() => { collapseNodeRef.current = collapseNode; }, [collapseNode]);

  const highlightRef = useRef({ highlightedEntityId, highlightedFileId });
  useEffect(() => {
    highlightRef.current = { highlightedEntityId, highlightedFileId };
  }, [highlightedEntityId, highlightedFileId]);

  // ── Build layout ──────────────────────────────────────────────────────────
  // Deps: graphData, visibleNodeIds, userExpandedIds, expandedFiles
  // Everything else is in stable refs.
  const buildLayout = useCallback(() => {
    if (!graphData) return;

    // Only render nodes currently visible
    const visibleNodes = graphData.nodes.filter((fn) => visibleNodeIds.has(fn.id));
    // Only render edges where both endpoints are visible
    const visibleEdges = graphData.edges.filter(
      (e) => visibleNodeIds.has(e.source_file_id) && visibleNodeIds.has(e.target_file_id)
    );

    const lineCounts: Record<string, number> = {};
    visibleNodes.forEach((fn) => {
      lineCounts[fn.id] = (fn.source ?? "").split("\n").length;
    });

    const hl = highlightRef.current;

    // Compute which nodes have hidden neighbours (for + badge)
    const hiddenNeighboursCount = (nodeId: string): number => {
      let count = 0;
      for (const e of graphData.edges) {
        if (!includeImports && e.dominant_type === "IMPORTS") continue;
        const nb =
          e.source_file_id === nodeId ? e.target_file_id :
          e.target_file_id === nodeId ? e.source_file_id : null;
        if (nb && !visibleNodeIds.has(nb)) count++;
      }
      return count;
    };

    const rfNodes: Node<FileNodeData>[] = visibleNodes.map((fn: FileNode) => {
      const hiddenCount = hiddenNeighboursCount(fn.id);
      const isUserExpanded = userExpandedIds.has(fn.id);

      return {
        id: fn.id,
        type: "file-node",
        position: { x: 0, y: 0 },
        data: {
          ...fn,
          source: fn.source ?? "",
          entities: fn.entities ?? [],
          onEntityClick: (entity: InlineEntity, fileId: string) => {
            onEntityClickRef.current?.(entity.id, fileId);
          },
          onToggleExpand: toggleExpandRef.current,
          isExpanded: expandedFiles.has(fn.id),
          isHighlighted: hl.highlightedFileId === fn.id,
          highlightedEntityId: hl.highlightedEntityId,
          isHovered: false,
          // Expandable graph controls
          hiddenNeighboursCount: hiddenCount,
          isUserExpanded,
          onExpandNode: () => expandNodeRef.current(fn.id),
          onCollapseNode: () => collapseNodeRef.current(fn.id),
        },
        style: {
          width: NODE_WIDTH,
          height: nodeHeight(lineCounts[fn.id] ?? 0, expandedFiles.has(fn.id)),
        },
      };
    });

    const rfEdges: Edge<GraphEdgeData>[] = visibleEdges.map((e: FileEdge) => ({
      id: `${e.source_file_id}::${e.target_file_id}`,
      source: e.source_file_id,
      target: e.target_file_id,
      type: "graph-edge",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#52525b" },
      data: {
        dominant_type: e.dominant_type,
        rel_types: e.rel_types,
        connections: e.connections,
        isHovered: false,
      },
      animated: false,
    }));

    const { nodes: laid, edges: laidEdges } = applyDagreLayout(rfNodes, rfEdges, {
      direction: "TB",
      expandedFiles,
      lineCounts,
    });

    setNodes(laid as Node[]);
    setEdges(laidEdges);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, visibleNodeIds, userExpandedIds, expandedFiles, includeImports]);

  useEffect(() => { buildLayout(); }, [buildLayout]);

  // ── Patch highlight without resetting positions ───────────────────────────
  useEffect(() => {
    setNodes((prev) =>
      prev.map((node) => {
        const nextHl  = highlightedFileId === node.id;
        const nextEnt = highlightedEntityId;
        if (node.data.isHighlighted === nextHl && node.data.highlightedEntityId === nextEnt) {
          return node;
        }
        return { ...node, data: { ...node.data, isHighlighted: nextHl, highlightedEntityId: nextEnt } };
      })
    );
  }, [highlightedEntityId, highlightedFileId]);

  // ── Auto-load ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (isOpen && repoId && !graphData && !loading) openGraph(repoId);
  }, [isOpen, repoId, graphData, loading, openGraph]);

  // ── fitView after first layout ────────────────────────────────────────────
  useEffect(() => {
    if (nodes.length > 0 && prevNodesLen.current === 0) {
      const t = setTimeout(() => fitViewRef.current?.(), 300);
      prevNodesLen.current = nodes.length;
      return () => clearTimeout(t);
    }
    prevNodesLen.current = nodes.length;
  }, [nodes.length]);

  const entryPoints = graphData?.entry_points ?? [];
  const allExpanded = graphData ? expandedFiles.size >= graphData.nodes.length : false;
  const totalFiles  = graphData?.nodes.length ?? 0;
  const shownFiles  = visibleNodeIds.size;

  return (
    <div className="flex flex-col h-full w-full bg-zinc-950 border-l border-zinc-800">

      {/* ── Header ── */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-zinc-800 shrink-0 flex-wrap">
        <GitBranch className="h-4 w-4 text-zinc-400 shrink-0" />
        <span className="text-sm font-semibold text-zinc-200 shrink-0">Code Graph</span>

        {entryPoints.length > 0 && (
          <select
            className="text-xs bg-zinc-800 text-zinc-300 border border-zinc-700 rounded px-2 py-1
                       focus:outline-none focus:border-zinc-500 max-w-[140px]"
            value={selectedRoot ?? ""}
            onChange={(e) => setRoot(e.target.value)}
          >
            <option value="" disabled>Entry point…</option>
            {entryPoints.map((id) => {
              const n = graphData?.nodes.find((x) => x.id === id);
              return <option key={id} value={id}>{n?.name ?? id}</option>;
            })}
          </select>
        )}

        <label className="flex items-center gap-1 text-xs text-zinc-400 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={includeImports}
            onChange={(e) => setIncludeImports(e.target.checked)}
            className="accent-blue-500"
          />
          Imports
        </label>

        <div className="flex-1" />

        {graphData && graphData.nodes.length > 0 && (
          <button
            onClick={allExpanded ? collapseAll : expandAll}
            className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
            title={allExpanded ? "Collapse all source" : "Expand all source"}
          >
            {allExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        )}

        <button
          onClick={() => refreshGraph()}
          disabled={loading}
          className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800
                     transition-colors disabled:opacity-40"
          title="Refresh"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>

        <button
          onClick={closeGraph}
          className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* ── Canvas ── */}
      <div className="flex-1 relative overflow-hidden">

        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-zinc-950/80">
            <div className="flex items-center gap-2 text-zinc-400">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span className="text-sm">Building graph…</span>
            </div>
          </div>
        )}

        {error && !loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center p-8">
            <div className="flex items-center gap-2 text-amber-400 bg-zinc-900
                            border border-zinc-700 rounded-xl px-4 py-3 shadow-xl">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="text-sm">{error}</span>
            </div>
          </div>
        )}

        {!loading && !error && graphData?.nodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-zinc-600 text-sm">
            No graph data found for this repository.
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onInit={(instance: ReactFlowInstance) => {
            fitViewRef.current = () => instance.fitView({ padding: 0.15 });
          }}
          minZoom={0.1}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          className="bg-zinc-950"
          nodesDraggable
          elementsSelectable
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
          <Controls className="!bg-zinc-900 !border-zinc-700" showInteractive={false} />
          <MiniMap
            className="!bg-zinc-900 !border-zinc-700"
            nodeColor={(node) => {
              const lang = (node.data as FileNode | undefined)?.language;
              return lang === "python"     ? "#1e3a8a"
                   : lang === "typescript" ? "#064e3b"
                   : "#27272a";
            }}
            maskColor="rgba(0,0,0,0.65)"
          />
        </ReactFlow>
      </div>

      {/* ── Footer ── */}
      {graphData && !loading && (
        <div className="shrink-0 px-3 py-1.5 border-t border-zinc-800 flex items-center gap-3
                        text-[11px] text-zinc-600">
          <span>
            {shownFiles} / {totalFiles} files
          </span>
          <span>·</span>
          <span>{edges.length} connections</span>
          {highlightedEntityId && (
            <>
              <span>·</span>
              <span className="text-yellow-600">
                ● <span className="font-mono">{highlightedEntityId.split(".").slice(-1)[0]}</span>
              </span>
            </>
          )}
          <span className="ml-auto">Click + to expand neighbours · − to collapse</span>
        </div>
      )}
    </div>
  );
}
