"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import type { ResolvedCitation } from "@/lib/citations";

interface MarkdownContentProps {
  content: string;
  className?: string;
  /** Map of citation badge number → resolved citation (from buildCitationMap) */
  citationMap?: Map<number, ResolvedCitation>;
  /** Called when user clicks an inline citation badge */
  onCitationClick?: (index: number, citation: ResolvedCitation) => void;
}

/**
 * Split a text node on [citation:N] tokens so we can render them as badges.
 * Returns an array of plain strings and citation token objects.
 */
function splitCitationTokens(
  text: string
): Array<string | { index: number }> {
  const parts: Array<string | { index: number }> = [];
  const re = /\[citation:(\d+)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = re.exec(text)) !== null) {
    if (match.index > last) {
      parts.push(text.slice(last, match.index));
    }
    parts.push({ index: parseInt(match[1], 10) });
    last = re.lastIndex;
  }

  if (last < text.length) {
    parts.push(text.slice(last));
  }

  return parts;
}

export function MarkdownContent({
  content,
  className,
  citationMap,
  onCitationClick,
}: MarkdownContentProps) {
  /**
   * Custom text renderer: intercepts text nodes and replaces
   * [citation:N] tokens with clickable badge buttons.
   */
  function renderText(text: string) {
    if (!citationMap || !text.includes("[citation:")) {
      return text;
    }

    const parts = splitCitationTokens(text);

    return (
      <>
        {parts.map((part, i) => {
          if (typeof part === "string") {
            return <span key={i}>{part}</span>;
          }

          const citation = citationMap.get(part.index);
          if (!citation) return null;

          const isUnsupported = citation.kind === "unsupported";

          return (
            <button
              key={i}
              onClick={() => onCitationClick?.(part.index, citation)}
              title={`${citation.file_path}:${citation.start_line}${citation.end_line !== citation.start_line ? `-${citation.end_line}` : ""}`}
              className={cn(
                "inline-flex items-center justify-center rounded px-1 py-0.5 text-[10px] font-mono font-semibold",
                "mx-0.5 cursor-pointer transition-colors align-middle",
                isUnsupported
                  ? "bg-amber-900/50 text-amber-300 hover:bg-amber-800/60 border border-amber-700/50"
                  : "bg-blue-900/50 text-blue-300 hover:bg-blue-800/60 border border-blue-700/50"
              )}
            >
              [{part.index}]
            </button>
          );
        })}
      </>
    );
  }

  return (
    <div className={cn("prose prose-invert prose-sm max-w-none", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Text nodes — intercept to render citation badges
          // ReactMarkdown passes children as a mix of strings and elements.
          // We handle the string case inside paragraph/li/etc. renderers
          // by overriding the base text component.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          p({ children, ...props }: any) {
            return (
              <p className="mb-3 last:mb-0 text-zinc-200 leading-relaxed" {...props}>
                {resolveChildren(children, renderText)}
              </p>
            );
          },
          li({ children, ...props }: any) {
            return (
              <li className="leading-relaxed" {...props}>
                {resolveChildren(children, renderText)}
              </li>
            );
          },
          // Code blocks
          code({ className: cls, children, ...props }: any) {
            const isInline = !cls;
            const match = /language-(\w+)/.exec(cls || "");
            const lang = match?.[1] ?? "";

            if (isInline) {
              return (
                <code
                  className="rounded bg-zinc-700/60 px-1 py-0.5 text-[0.8em] font-mono text-blue-300"
                  {...props}
                >
                  {children}
                </code>
              );
            }

            return (
              <div className="relative my-3">
                {lang && (
                  <div className="absolute right-3 top-2 text-xs text-zinc-500 font-mono">
                    {lang}
                  </div>
                )}
                <pre className="overflow-x-auto rounded-lg bg-zinc-950 p-4 text-sm">
                  <code className={cn("font-mono text-zinc-200", cls)} {...props}>
                    {children}
                  </code>
                </pre>
              </div>
            );
          },
          // Headings
          h1({ children }) {
            return <h1 className="text-lg font-semibold text-white mb-2 mt-4">{children}</h1>;
          },
          h2({ children }) {
            return <h2 className="text-base font-semibold text-white mb-2 mt-3">{children}</h2>;
          },
          h3({ children }) {
            return <h3 className="text-sm font-semibold text-zinc-200 mb-1.5 mt-2">{children}</h3>;
          },
          // Lists
          ul({ children }) {
            return <ul className="mb-3 ml-4 space-y-1 list-disc text-zinc-200">{children}</ul>;
          },
          ol({ children }) {
            return <ol className="mb-3 ml-4 space-y-1 list-decimal text-zinc-200">{children}</ol>;
          },
          // Blockquote
          blockquote({ children }) {
            return (
              <blockquote className="border-l-2 border-blue-500 pl-3 text-zinc-400 italic my-2">
                {children}
              </blockquote>
            );
          },
          // Bold / italic
          strong({ children }) {
            return <strong className="font-semibold text-white">{children}</strong>;
          },
          em({ children }) {
            return <em className="italic text-zinc-300">{children}</em>;
          },
          // Horizontal rule
          hr() {
            return <hr className="my-4 border-zinc-700" />;
          },
          // Links
          a({ href, children }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 underline underline-offset-2 hover:text-blue-300"
              >
                {children}
              </a>
            );
          },
          // Table
          table({ children }) {
            return (
              <div className="overflow-x-auto my-3">
                <table className="min-w-full text-sm">{children}</table>
              </div>
            );
          },
          th({ children }) {
            return (
              <th className="border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-left font-medium text-zinc-200">
                {children}
              </th>
            );
          },
          td({ children }) {
            return (
              <td className="border border-zinc-700 px-3 py-1.5 text-zinc-300">
                {children}
              </td>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Walk ReactMarkdown children and apply renderText to plain string nodes.
 * Non-string children (React elements) are passed through unchanged.
 */
function resolveChildren(
  children: React.ReactNode,
  renderText: (text: string) => React.ReactNode
): React.ReactNode {
  if (typeof children === "string") {
    return renderText(children);
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === "string") {
        const rendered = renderText(child);
        // Wrap in a fragment with key only if we transformed it
        return rendered !== child ? (
          <span key={i}>{rendered}</span>
        ) : (
          <span key={i}>{child}</span>
        );
      }
      return child;
    });
  }

  return children;
}
