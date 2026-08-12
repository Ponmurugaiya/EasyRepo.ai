// ─────────────────────────────────────────────────────────────────────────────
// Dagre layout utility for React Flow
// Node heights computed from visible source lines.
// ─────────────────────────────────────────────────────────────────────────────

import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "reactflow";

export const NODE_WIDTH = 320;
const NODE_HEADER_HEIGHT = 52;   // file header
const LINE_HEIGHT = 16;          // px per source line in the code view
const MAX_VISIBLE_LINES = 24;    // cap before scrolling kicks in
const NODE_MIN_HEIGHT = 80;      // collapsed / no source
const RANK_SEP = 120;
const NODE_SEP = 60;

export function nodeHeight(lineCount: number, isExpanded: boolean): number {
  if (!isExpanded || lineCount === 0) return NODE_MIN_HEIGHT;
  const visibleLines = Math.min(lineCount, MAX_VISIBLE_LINES);
  return NODE_HEADER_HEIGHT + visibleLines * LINE_HEIGHT + 16; // +16 padding
}

export interface LayoutOptions {
  direction?: "TB" | "LR";
  expandedFiles: Set<string>;
  lineCounts: Record<string, number>; // fileId → total source line count
}

export function applyDagreLayout<T extends { id: string }>(
  nodes: Node<T>[],
  edges: Edge[],
  options: LayoutOptions
): { nodes: Node<T>[]; edges: Edge[] } {
  const { direction = "TB", expandedFiles, lineCounts } = options;

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, ranksep: RANK_SEP, nodesep: NODE_SEP, marginx: 50, marginy: 50 });

  nodes.forEach((node) => {
    const lines = lineCounts[node.id] ?? 0;
    const expanded = expandedFiles.has(node.id);
    const h = nodeHeight(lines, expanded);
    g.setNode(node.id, { width: NODE_WIDTH, height: h });
  });

  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const dn = g.node(node.id);
    const h = dn.height ?? NODE_MIN_HEIGHT;
    return { ...node, position: { x: dn.x - NODE_WIDTH / 2, y: dn.y - h / 2 } };
  });

  return { nodes: layoutedNodes, edges };
}
