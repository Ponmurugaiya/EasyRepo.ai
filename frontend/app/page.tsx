"use client";

import { useChatStore } from "@/store/chat-store";
import { Sidebar } from "@/components/sidebar/sidebar";
import { ChatWindow } from "@/components/chat/chat-window";
import { WelcomeScreen } from "@/components/chat/welcome-screen";
import { Button } from "@/components/ui/button";
import { PanelLeft } from "lucide-react";

export default function Home() {
  const { activeRepoId, repoSessions, sidebarOpen, setSidebarOpen } = useChatStore();
  const activeRepo = activeRepoId ? repoSessions[activeRepoId] : null;

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950">
      <Sidebar />

      {/* Main area */}
      <main className="flex flex-1 flex-col min-w-0 h-full relative">
        {/* Mobile sidebar toggle (shown when sidebar closed) */}
        {!sidebarOpen && (
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-3 left-3 z-10 h-8 w-8 text-zinc-400 hover:text-white md:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            <PanelLeft className="h-4 w-4" />
          </Button>
        )}

        {activeRepo ? (
          <ChatWindow repo={activeRepo} />
        ) : (
          <WelcomeScreen />
        )}
      </main>
    </div>
  );
}
