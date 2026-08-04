// ─────────────────────────────────────────────────────────────────────────────
// Graph types — mirrors the FastAPI graph schemas exactly
// ─────────────────────────────────────────────────────────────────────────────

export type RelType =
  | "CALLS"
  | "IMPORTS"
  | "INHERITS"
  | "IMPLEMENTS"
  | "INSTANTIATES";

export type EntityType =
  | "module"
  | "class"
  | "interface"
  | "function"
  | "method"
  | "doc_block"
  | "variable";

export type Language = "python" | "typescript" | "markdown";

// Lightweight entity embedded inside a file node
export interface InlineEntity {
  id: string;
  name: string;
  type: EntityType;
  start_line: number;
  end_line: number;
  has_docstring: boolean;
}

export interface EntityConnection {
  from_entity_id: string;
  from_entity_name: string;
  to_entity_id: string;
  to_entity_name: string;
  rel_type: RelType;
  line: number;
}

export interface FileEdge {
  source_file_id: string;
  target_file_id: string;
  rel_types: string[];
  dominant_type: string;
  connections: EntityConnection[];
}

// File node now carries entities inline + full source text
export interface FileNode {
  id: string;
  file_path: string;
  name: string;
  language: Language;
  is_entry: boolean;
  entry_score: number;
  depth: number;
  is_root: boolean;
  source: string;             // full file source text
  entities: InlineEntity[];   // always populated from the graph endpoint
}

export interface FileGraphResponse {
  root: string | null;
  entry_points: string[];
  nodes: FileNode[];
  edges: FileEdge[];
}

// Detailed expand response — used for source code viewer only
export interface ExpandedEntity {
  id: string;
  name: string;
  type: EntityType;
  start_line: number;
  end_line: number;
  language: Language;
  has_docstring: boolean;
}

export interface FileExpandResponse {
  file_id: string;
  file_path: string;
  entities: ExpandedEntity[];
  outgoing_edges: EntityConnection[];
  incoming_edges: EntityConnection[];
}
