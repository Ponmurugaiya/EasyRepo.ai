// ─────────────────────────────────────────────────────────────────────────────
// Citation utilities
//
// The LLM embeds citations inline as [file/path.py:12-34].
// This module:
//   1. Scans the answer text for those raw citation tokens
//   2. Cross-references them against the ValidationReport to get full metadata
//   3. Returns the answer text with tokens replaced by numbered badges [1], [2]…
//   4. Returns the citation map: number → CitationMatch | CitationMismatch
// ─────────────────────────────────────────────────────────────────────────────

import type { CitationMatch, CitationMismatch, ValidationReport } from "../types/api";

export type ResolvedCitation =
  | (CitationMatch & { kind: "definition" | "callsite" })
  | (CitationMismatch & { kind: "unsupported" });

export interface CitationMap {
  /** 1-indexed map of badge number → resolved citation */
  citations: Map<number, ResolvedCitation>;
  /** Answer text with [file:line] tokens replaced by [1], [2]… */
  processedContent: string;
}

// Matches [some/path/file.py:12-34] or [some/path/file.py:12]
const CITATION_RE = /\[([^\[\]\s:]+\.[a-zA-Z0-9]+):(\d+)(?:-(\d+))?\]/g;

export function buildCitationMap(
  answer: string,
  report: ValidationReport
): CitationMap {
  // Build a lookup from raw citation string → resolved citation
  const rawToResolved = new Map<string, ResolvedCitation>();

  for (const c of report.definition_citations) {
    rawToResolved.set(c.raw, { ...c, kind: "definition" });
  }
  for (const c of report.call_site_citations) {
    rawToResolved.set(c.raw, { ...c, kind: "callsite" });
  }
  for (const c of report.unsupported_citations) {
    rawToResolved.set(c.raw, { ...c, kind: "unsupported" });
  }

  const citationMap = new Map<number, ResolvedCitation>();
  // Track raw → badge number so duplicate citations get the same number
  const rawToNumber = new Map<string, number>();
  let counter = 0;

  const processedContent = answer.replace(CITATION_RE, (match) => {
    if (rawToNumber.has(match)) {
      return `[citation:${rawToNumber.get(match)}]`;
    }

    counter++;
    rawToNumber.set(match, counter);

    const resolved = rawToResolved.get(match);
    if (resolved) {
      citationMap.set(counter, resolved);
    } else {
      // Citation was in the answer but not in the report — treat as unknown
      // Parse it ourselves so we can still show something useful
      const parts = match.slice(1, -1).split(":");
      const filePath = parts[0];
      const linePart = parts[1] ?? "0";
      const [startStr, endStr] = linePart.split("-");
      const startLine = parseInt(startStr, 10) || 0;
      const endLine = endStr ? parseInt(endStr, 10) : startLine;

      citationMap.set(counter, {
        kind: "unsupported",
        raw: match,
        file_path: filePath,
        start_line: startLine,
        end_line: endLine,
        reason: "Not found in validation report",
        nearest_entity: null,
      });
    }

    return `[citation:${counter}]`;
  });

  return { citations: citationMap, processedContent };
}
