import { useEffect, useRef, useState } from "react";

/** Custom instructions for the AI: like a system prompt you write, for a workspace, dataset or dashboard. */
export function InstructionsDialog(props: {
  title: string;
  scope: string;
  load: () => Promise<string>;
  save: (text: string) => Promise<unknown>;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    ref.current?.showModal();
    props.load().then(setText).catch((e) => setError((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    if (text === null) return;
    setSaving(true);
    setError(null);
    try {
      await props.save(text);
      props.onClose();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="dialog instructions-dialog"
      aria-label={props.title}
      onCancel={(e) => {
        e.preventDefault();
        props.onClose();
      }}
    >
      <h2>{props.title}</h2>
      <p className="muted small">
        Every AI that works on {props.scope} reads these, after its own rules: the data's facts and the checks still come first.
        For example: "Amounts are in RM", "Our financial year starts in April", "Call customers clients".
      </p>
      <textarea
        className="instructions-text"
        rows={8}
        value={text ?? ""}
        disabled={text === null}
        onChange={(e) => setText(e.target.value)}
        placeholder={text === null ? "Loading…" : "Write in plain words; one instruction per line works well."}
        aria-label="Instructions"
        maxLength={2000}
      />
      <div className="instructions-foot">
        <span className="muted small">{(text ?? "").length.toLocaleString()} / 2,000</span>
        {error && <span className="bad small">{error}</span>}
        <span className="grow" />
        <button className="secondary" onClick={props.onClose}>
          Cancel
        </button>
        <button onClick={save} disabled={saving || text === null}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </dialog>
  );
}
