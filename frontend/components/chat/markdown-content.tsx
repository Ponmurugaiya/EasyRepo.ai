"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface MarkdownContentProps {
  content: string;
  className?: string;
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={cn("prose prose-invert prose-sm max-w-none", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Code blocks
          code({ className, children, ...props }) {
            const isInline = !className;
            const match = /language-(\w+)/.exec(className || "");
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
                  <code className={cn("font-mono text-zinc-200", className)} {...props}>
                    {children}
                  </code>
                </pre>
              </div>
            );
          },
          // Paragraphs
          p({ children }) {
            return (
              <p className="mb-3 last:mb-0 text-zinc-200 leading-relaxed">
                {children}
              </p>
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
          li({ children }) {
            return <li className="leading-relaxed">{children}</li>;
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
