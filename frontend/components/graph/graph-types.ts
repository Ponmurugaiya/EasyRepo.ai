// ─────────────────────────────────────────────────────────────────────────────
// React Flow node/edge type registrations.
//
// IMPORTANT: These MUST live in their own file, isolated from any component
// that uses them. If they share a file with a React component, Next.js HMR
// will re-evaluate this module on every hot reload, producing new object
// references that trigger React Flow's error #002 warning.
//
// Rule: this file imports ONLY the component definitions — never stores, hooks,
// or anything that changes at runtime.
// ─────────────────────────────────────────────────────────────────────────────

import { FileNodeComponent_ } from "./file-node";
import { GraphEdge } from "./graph-edge";

export const NODE_TYPES = {
  "file-node": FileNodeComponent_,
} as const;

export const EDGE_TYPES = {
  "graph-edge": GraphEdge,
} as const;
