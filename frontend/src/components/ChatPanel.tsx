import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Square, X } from "lucide-react";
import type { ChatMessage, ChatMode, ChatThread } from "../api";
import { Markdown } from "./Markdown";
import { Answer } from "./AskPanel";

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
  onClose?: () => void;
  handle?: (handle: ChatHandle) => void;
}) {
  const { thread, onApplied } = props;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<ChatMode>(savedMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const controller = useRef<AbortController | null>(null);

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

  async function run(action: (signal: AbortSignal) => Promise<{ messages: ChatMessage[]; applied: boolean }>) {
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setError(null);
    setStopped(false);
    try {
      const result = await action(abort.signal);
      setMessages(result.messages);
      if (result.applied) onApplied();
    } catch (e) {
      if (!abort.signal.aborted) setError((e as Error).message);
    } finally {
      if (controller.current === abort) controller.current = null;
      setBusy(false);
    }
  }

  function stop() {
    controller.current?.abort();
    setStopped(true);
  }

  function send(message: string) {
    const clean = message.trim();
    if (!clean || busy) return;
    setText("");
    setMessages((current) => [...current, { id: "pending", role: "user", content: clean, at: new Date().toISOString() }]);
    void run((signal) => thread.send(clean, mode, signal));
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
        {props.onClose && (
          <button className="icon" onClick={props.onClose} aria-label="Close chat" title="Close">
            <X size={16} />
          </button>
        )}
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
          <Message key={m.id + m.at} message={m} busy={busy} onDecide={(id, d) => run(() => thread.decide(id, d))} onAsk={send} />
        ))}
        {busy && <div className="chat-msg assistant thinking">Thinking…</div>}
        {stopped && (
          <p className="muted small chat-stopped">
            Stopped waiting. A change the AI had already started may still finish; check Activity or reopen the chat.
          </p>
        )}
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
          {busy ? (
            <button type="button" className="secondary" onClick={stop} aria-label="Stop waiting for the AI">
              <Square size={14} aria-hidden="true" /> Stop
            </button>
          ) : (
            <button type="submit" disabled={!text.trim()} aria-label="Send">
              <Send size={15} aria-hidden="true" /> Send
            </button>
          )}
        </form>
      )}
    </aside>
  );
}

function Message(props: {
  message: ChatMessage;
  busy: boolean;
  onDecide: (id: string, d: "approve" | "reject") => void;
  onAsk: (question: string) => void;
}) {
  const { message, busy, onDecide } = props;
  const change = message.change;
  if (message.answer) {
    // Answered from the data model: the same view as Ask, with results and "how this was calculated".
    return (
      <div className="chat-answer">
        <p className="muted small chat-answer-note">From the data model: {message.answer.question}</p>
        <Answer response={message.answer} onFollowUp={props.onAsk} busy={busy} />
      </div>
    );
  }
  return (
    <div className={`chat-msg ${message.role}`}>
      {message.content && (message.role === "assistant" ? <Markdown text={message.content} /> : <p>{message.content}</p>)}
      {message.how && (
        <details className="how">
          <summary>How this was calculated</summary>
          <ul>
            {message.how.visuals.map((v) => (
              <li key={v.page + v.title}>
                From <strong>{v.title}</strong> on {v.page}
                {v.split_by.length > 0 && <>, split by {v.split_by.join(" and ")}</>}
              </li>
            ))}
            {message.how.measures.map((m) => (
              <li key={m.name}>
                <strong>{m.name}</strong>: {m.definition}
              </li>
            ))}
            {message.how.as_of && <li>Data as of {message.how.as_of}</li>}
          </ul>
        </details>
      )}
      {message.check && !message.check.verified && (
        <p className="unverified small">
          Check these numbers: {message.check.unmatched.join(", ")} {message.check.unmatched.length === 1 ? "doesn't" : "don't"} match the
          dashboard's data.
        </p>
      )}
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
                  {a.error && <span className="small">: {a.error}</span>}
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
