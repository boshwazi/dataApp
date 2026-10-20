import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatMessage, ChatMode, ChatThread } from "../api";

const MODE_KEY = "chatMode";

function savedMode(): ChatMode {
  try {
    return localStorage.getItem(MODE_KEY) === "review" ? "review" : "full";
  } catch {
    return "full";
  }
}

export type ChatHandle = { send: (text: string) => void };

/**
 * A stage's AI chat. The agent answers questions and changes only its own stage.
 * Full AI applies its changes at once; Review shows the checked plan for approval first.
 */
export function ChatPanel(props: {
  title: string;
  thread: ChatThread;
  suggestions: string[];
  disabled?: string | null;
  onApplied: () => void;
  handle?: (handle: ChatHandle) => void;
}) {
  const { thread, onApplied } = props;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ChatMode>(savedMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMessages([]);
    thread.load().then((r) => setMessages(r.messages)).catch((e) => setError(e.message));
  }, [thread]);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [messages, busy]);

  function chooseMode(next: ChatMode) {
    setMode(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      // private mode: the choice lasts until reload
    }
  }

  async function run(action: () => Promise<{ messages: ChatMessage[]; applied: boolean }>) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      setMessages(result.messages);
      if (result.applied) onApplied();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function send(message: string) {
    const clean = message.trim();
    if (!clean || busy) return;
    setText("");
    setMessages((current) => [...current, { id: "pending", role: "user", content: clean, at: new Date().toISOString() }]);
    void run(() => thread.send(clean, mode));
  }

  useEffect(() => {
    props.handle?.({ send });
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    send(text);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send(text);
    }
  }

  return (
    <aside className="chat-panel" aria-label={`${props.title} chat`}>
      <header className="chat-head">
        <div>
          <strong>{props.title}</strong>
          <span className="muted small">Ask or tell the AI what to change</span>
        </div>
        <span className="segmented" role="radiogroup" aria-label="Chat mode">
          <button
            role="radio"
            aria-checked={mode === "full"}
            className={mode === "full" ? "on" : ""}
            onClick={() => chooseMode("full")}
            title="Changes apply at once; you can undo them"
          >
            Full AI
          </button>
          <button
            role="radio"
            aria-checked={mode === "review"}
            className={mode === "review" ? "on" : ""}
            onClick={() => chooseMode("review")}
            title="Changes wait for your approval"
          >
            Review
          </button>
        </span>
      </header>

      <div className="chat-messages" ref={list}>
        {messages.length === 0 && !busy && (
          <div className="chat-empty">
            <p className="muted small">Try:</p>
            {props.suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)} disabled={!!props.disabled}>
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <Message key={m.id + m.at} message={m} busy={busy} onDecide={(id, d) => run(() => thread.decide(id, d))} />
        ))}
        {busy && <div className="chat-msg assistant thinking">Thinking…</div>}
      </div>

      {error && <p className="bad small chat-error">{error}</p>}
      {props.disabled ? (
        <p className="muted small chat-disabled">{props.disabled}</p>
      ) : (
        <form className="chat-input" onSubmit={submit}>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            placeholder="Ask a question or request a change…"
            rows={2}
            aria-label="Message"
          />
          <button type="submit" disabled={busy || !text.trim()}>
            Send
          </button>
        </form>
      )}
    </aside>
  );
}

function Message({ message, busy, onDecide }: { message: ChatMessage; busy: boolean; onDecide: (id: string, d: "approve" | "reject") => void }) {
  const change = message.change;
  return (
    <div className={`chat-msg ${message.role}`}>
      {message.content && <p>{message.content}</p>}
      {change && (
        <div className={`chat-change ${change.status}`}>
          <div className="chat-change-head">
            {change.status === "pending" ? "Waiting for your approval" : change.status === "applied" ? "Applied" : "Rejected"}
          </div>
          <ul>
            {change.actions.map((a, i) => (
              <li key={i} className={a.ok ? "ok-line" : "bad-line"}>
                <span aria-hidden="true">{a.ok ? "✓" : "✗"}</span>
                <span>
                  {a.label}
                  {a.error && <span className="small"> — {a.error}</span>}
                </span>
              </li>
            ))}
          </ul>
          {change.status === "pending" && (
            <div className="actions">
              <button onClick={() => onDecide(change.id, "approve")} disabled={busy || !change.actions.some((a) => a.ok)}>
                Approve
              </button>
              <button className="secondary" onClick={() => onDecide(change.id, "reject")} disabled={busy}>
                Reject
              </button>
            </div>
          )}
          {change.status === "applied" && <p className="muted small">Undo it with "Undo last change".</p>}
        </div>
      )}
    </div>
  );
}
