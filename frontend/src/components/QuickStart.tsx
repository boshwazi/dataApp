import { useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Upload } from "lucide-react";
import { api } from "../api";
import { useToast } from "./ui";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm";

/** Home's quick start: drop a file, and Autopilot takes it from there. */
export function QuickStart() {
  const [busy, setBusy] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const toast = useToast();

  async function begin(files: File[]) {
    if (!files.length || busy) return;
    const name = files[0].name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || "My data";
    try {
      setBusy("Creating the dataset…");
      const dataset = await api.createDataset(name);
      setBusy("Uploading…");
      const added = await api.uploadFiles(dataset.id, files);
      if (!added.added.length) {
        toast(added.errors[0] ?? "None of those files could be read.", "error");
        navigate(`/datasets/${dataset.id}/get-data`);
        return;
      }
      await api.startAutopilot(dataset.id);
      navigate(`/datasets/${dataset.id}/autopilot`);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  function dropped(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    void begin([...e.dataTransfer.files]);
  }

  return (
    <section
      className={`quick-start ${over ? "over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={dropped}
    >
      <Upload size={20} aria-hidden="true" />
      <div>
        <h2>Drop a spreadsheet and get a dashboard</h2>
        <p className="muted small">
          CSV or Excel. Autopilot cleans it, models it and designs a draft dashboard; you can change any step later.
        </p>
      </div>
      <button className="secondary" onClick={() => input.current?.click()} disabled={!!busy}>
        {busy ?? "Choose files"}
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(e) => void begin([...(e.target.files ?? [])])}
      />
    </section>
  );
}
