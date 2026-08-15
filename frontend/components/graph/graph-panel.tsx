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
import type { FileNode, InlineEntity } from "../../types/graph";
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
    expandedFiles, toggleExpand,
    highlightedEntityId, highlightedFileId,
    selectedRoot, depth, includeImports,
    setRoot, setDepth, setIncludeImports,
    refreshGraph, openGraph,
    expandAll, collapseAll,
  } = useGraphStore();

  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  // Local state for the depth input — avoids firing an API call on every keystroke.
  // We commit to the store (which triggers a refresh) only after the user stops typing.
  const [depthInput, setDepthInput] = useState<string>(String(depth));
  const depthDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep local input in sync if the store depth changes externally
  useEffect(() => { setDepthInput(String(depth)); }, [depth]);

  // Handle node/edge changes (drag, select).
  // We intentionally SKIP "dimensions" changes — those come from React Flow
  // measuring individual nodes and would truncate our full node array to
  // just the nodes that have been measured so far (causing the "only 1 node"
  // bug after tab switch). We own the node dimensions via our layout.
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const safe = changes.filter((c) => c.type !== "dimensions" && c.type !== "remove");
    if (safe.length > 0) {
      setNodes((nds) => applyNodeChanges(safe, nds));
    }
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const safe = changes.filter((c) => c.type !== "remove");
    if (safe.length > 0) {
      setEdges((eds) => applyEdgeChanges(safe, eds));
    }
  }, []);

  // fitView ref — set by onInit, called after nodes are fully rendered
  const fitViewRef = useRef<(() => void) | null>(null);
  const prevNodesLen = useRef(0);
  // Reset on every mount so the 0→N fitView fires correctly after tab switch
  useEffect(() => {
    prevNodesLen.current = 0;
    return () => {};
  }, []);

  // Stable refs — keep callbacks out of buildLayout deps to prevent position resets
  const onEntityClickRef = useRef(onEntityClick);
  useEffect(() => { onEntityClickRef.current = onEntityClick; }, [onEntityClick]);

  const toggleExpandRef = useRef(toggleExpand);
  useEffect(() => { toggleExpandRef.current = toggleExpand; }, [toggleExpand]);

  const highlightRef = useRef({ highlightedEntityId, highlightedFileId });
  useEffect(() => {
    highlightRef.current = { highlightedEntityId, highlightedFileId };
  }, [highlightedEntityId, highlightedFileId]);

  // ── Build layout ─────────────────────────────────────────────────────────
  // Only deps: graphData and expandedFiles.
  // Hover state, highlight state, and callbacks are kept in refs so they
  // never cause this to re-run and reset node positions.
  const buildLayout = useCallback(() => {
    if (!graphData) return;

    const lineCounts: Record<string, number> = {};
    graphData.nodes.forEach((fn) => {
      lineCounts[fn.id] = (fn.source ?? "").split("\n").length;
    });

    const hl = highlightRef.current;

    const rfNodes: Node<FileNodeData>[] = graphData.nodes.map((fn: FileNode) => ({
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
      },
      style: {
        width: NODE_WIDTH,
        height: nodeHeight(lineCounts[fn.id] ?? 0, expandedFiles.has(fn.id)),
      },
    }));

    const rfEdges: Edge<GraphEdgeData>[] = graphData.edges.map((e) => ({
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
  }, [graphData, expandedFiles]);

  useEffect(() => { buildLayout(); }, [buildLayout]);

  // ── Patch highlight without touching positions ────────────────────────────
  useEffect(() => {
    setNodes((prev: Node[]) =>
      prev.map((node) => {
        const nextHl  = highlightedFileId === node.id;
        const nextEnt = highlightedEntityId;
        if (node.data.isHighlighted === nextHl && node.data.highlightedEntityId === nextEnt) {
          return node;
        }
        return { ...node, data: { ...node.data, isHighlighted: nextHl, highlightedEntityId: nextEnt } };
      })
    );
  }, [highlightedEntityId, highlightedFileId, setNodes]);

  // ── Auto-load ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (isOpen && repoId && !graphData && !loading) openGraph(repoId);
  }, [isOpen, repoId, graphData, loading, openGraph]);

  // ── fitView after layout ──────────────────────────────────────────────────
  // Simple: whenever nodes go from 0 → N, wait 300ms and fit.
  // The 300ms gives React Flow time to measure all node sizes.
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

        <label className="flex items-center gap-1 text-xs text-zinc-400">
          Depth
          <input type="number" min={1} max={10} value={depthInput}
            onChange={(e) => {
              setDepthInput(e.target.value);
              const n = Number(e.target.value);
              if (!Number.isFinite(n) || n < 1) return;
              if (depthDebounceRef.current) clearTimeout(depthDebounceRef.current);
              depthDebounceRef.current = setTimeout(() => setDepth(Math.min(10, Math.max(1, n))), 600);
            }}
            className="w-9 bg-zinc-800 text-zinc-300 border border-zinc-700 rounded
                       px-1.5 py-0.5 text-xs focus:outline-none focus:border-zinc-500"
          />
        </label>

        <label className="flex items-center gap-1 text-xs text-zinc-400 cursor-pointer select-none">
          <input type="checkbox" checked={includeImports}
            onChange={(e) => setIncludeImports(e.target.checked)}
            className="accent-blue-500" />
          Imports
        </label>

        <div className="flex-1" />

        {graphData && graphData.nodes.length > 0 && (
          <button
            onClick={allExpanded ? collapseAll : expandAll}
            className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
            title={allExpanded ? "Collapse all" : "Expand all"}
          >
            {allExpanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        )}

        <button onClick={() => refreshGraph()} disabled={loading}
          className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800
                     transition-colors disabled:opacity-40" title="Refresh">
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>

        <button onClick={closeGraph}
          className="p-1.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors">
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
          minZoom={0.15}
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
          <span>{graphData.nodes.length} files</span>
          <span>·</span>
          <span>{graphData.edges.length} connections</span>
          <span>·</span>
          <span>{graphData.nodes.reduce((s, n) => s + (n.source ?? "").split("\n").length, 0)} lines</span>
          {highlightedEntityId && (
            <>
              <span>·</span>
              <span className="text-yellow-600">
                ● <span className="font-mono">{highlightedEntityId.split(".").slice(-1)[0]}</span>
              </span>
            </>
          )}
          <span className="ml-auto">Click header to expand · hover edge for details</span>
        </div>
      )}
    </div>
  );
}
