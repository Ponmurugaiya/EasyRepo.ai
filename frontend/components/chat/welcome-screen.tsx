"use client";

import { Bot, GitBranch, MessageSquare, Zap, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chat-store";
import { useState } from "react";
import { AddRepoButton } from "@/components/sidebar/add-repo-button";

const FEATURES = [
  {
    icon: GitBranch,
    title: "Graph-aware retrieval",
    desc: "Understands call chains, class hierarchies, and import graphs — not just text similarity.",
  },
  {
    icon: MessageSquare,
    title: "Cited answers",
    desc: "Every answer links back to exact file paths and line numbers from your codebase.",
  },
  {
    icon: Zap,
    title: "Relationship expansion",
    desc: "Automatically pulls in callers, callees, and parent classes for complete context.",
  },
  {
    icon: ShieldCheck,
    title: "Hallucination rate",
    desc: "Each answer shows a citation validation score so you know what to trust.",
  },
];

export function WelcomeScreen() {
  const { setSidebarOpen } = useChatStore();

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-12 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-blue-600/20 border border-blue-600/30 mb-6">
        <Bot className="h-10 w-10 text-blue-400" />
      </div>

      <h1 className="text-3xl font-bold text-white mb-2">EasyRepo</h1>
      <p className="text-zinc-400 max-w-md mb-8 leading-relaxed">
        AI-powered codebase intelligence. Ask natural-language questions and get
        cited answers grounded in your actual code structure.
      </p>

      <div className="flex flex-col sm:flex-row gap-3 mb-12">
        <AddRepoButton />
        <Button
          variant="ghost"
          className="text-zinc-400 hover:text-white border border-zinc-700 hover:border-zinc-500"
          onClick={() => setSidebarOpen(true)}
        >
          Browse my repos
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl w-full text-left">
        {FEATURES.map(({ icon: Icon, title, desc }) => (
          <div
            key={title}
            className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"
          >
            <div className="flex items-center gap-2 mb-2">
              <Icon className="h-4 w-4 text-blue-400" />
              <span className="text-sm font-medium text-zinc-200">{title}</span>
            </div>
            <p className="text-xs text-zinc-500 leading-relaxed">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
