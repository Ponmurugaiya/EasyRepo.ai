"use client";

import { useState, useRef, useCallback, KeyboardEvent } from "react";
import { cn } from "../../lib/utils";
import { Button } from "../../components/ui/button";
import { ArrowUp, Square } from "lucide-react";

interface ChatInputProps {
  onSend: (message: string) => void;
  onCancel?: () => void;
  disabled?: boolean;
  loading?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  onCancel,
  disabled,
  loading,
  placeholder = "Ask anything about this codebase…",
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled || loading) return;
    onSend(trimmed);
    setValue("");
    // Reset height
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, loading, onSend]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleInput() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }

  const canSend = value.trim().length > 0 && !disabled && !loading;

  return (
    <div className="px-4 py-4">
      <div
        className={cn(
          "flex items-end gap-3 rounded-2xl border bg-zinc-800/80 px-4 py-3 transition-colors",
          "border-zinc-700 focus-within:border-zinc-500"
        )}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={placeholder}
          disabled={disabled || loading}
          rows={1}
          className={cn(
            "flex-1 resize-none bg-transparent text-sm text-zinc-100 outline-none",
            "placeholder:text-zinc-500 disabled:cursor-not-allowed disabled:opacity-50",
            "max-h-[200px] leading-relaxed"
          )}
          aria-label="Message input"
        />
        <Button
          onClick={loading ? onCancel : handleSend}
          disabled={loading ? false : !canSend}
          size="icon"
          className={cn(
            "h-8 w-8 shrink-0 rounded-full transition-colors",
            canSend || loading
              ? "bg-blue-600 hover:bg-blue-500 text-white"
              : "bg-zinc-700 text-zinc-500 cursor-not-allowed"
          )}
          aria-label={loading ? "Cancel request" : "Send message"}
        >
          {loading ? (
            <Square className="h-3.5 w-3.5 fill-current" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </Button>
      </div>
      <p className="mt-1.5 text-center text-xs text-zinc-600">
        Press <kbd className="font-mono">Enter</kbd> to send ·{" "}
        <kbd className="font-mono">Shift+Enter</kbd> for new line
      </p>
    </div>
  );
}
