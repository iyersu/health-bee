import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Screen = "today" | "history" | "settings";
type Entry = { id: number; occurred_at: string; raw_text: string; mood: string | null; energy: number | null; sleep_hours: number | null; observations: unknown[] };

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init.headers ?? {}) } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "The local journal is unavailable.");
  }
  return response.json() as Promise<T>;
}

const dayLabel = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function Nav({ active, label, icon, onClick }: { active: boolean; label: string; icon: string; onClick: () => void }) {
  return <button className={`nav ${active ? "active" : ""}`} onClick={onClick} aria-current={active ? "page" : undefined}><span>{icon}</span>{label}</button>;
}

function Today({ onSaved }: { onSaved: () => void }) {
  const [note, setNote] = useState("");
  const [mood, setMood] = useState("steady");
  const [energy, setEnergy] = useState("6");
  const [sleep, setSleep] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState("");
  const dirty = () => { if (status !== "saving") setStatus("idle"); };

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (note.trim() && status !== "saved") { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [note, status]);

  async function save() {
    if (!note.trim() || status === "saving" || status === "saved") return;
    setStatus("saving"); setMessage("");
    try {
      await api<{ id: number }>("/api/entries", { method: "POST", body: JSON.stringify({ raw_text: note, mood, energy: Number(energy), sleep_hours: sleep ? Number(sleep) : null }) });
      setStatus("saved"); setMessage("Saved to your journal."); onSaved();
    } catch (error) {
      setStatus("error"); setMessage(error instanceof Error ? error.message : "Could not save the note.");
    }
  }

  const date = new Date();
  return <>
    <section className="hero"><div><p className="kicker">Today</p><h1>How are you feeling?</h1><p>A few words are enough. This is your space to be honest and unhurried.</p></div><div className="date"><small>{date.toLocaleString(undefined, { month: "short" }).toUpperCase()}</small><b>{date.getDate()}</b><small>{date.getFullYear()}</small></div></section>
    <section className="grid"><article className="card note"><header><div><h2>Daily note</h2><p>Start a fresh entry for today.</p></div><span className="preview">Journal</span></header><label className="sr" htmlFor="note">Daily journal note</label><textarea id="note" value={note} onChange={event => { setNote(event.target.value); dirty(); }} placeholder="What would you like to remember about today?"/><footer><span className={`save-state ${status}`} aria-live="polite">{status === "saved" && "✓ "}{message || "Your words are saved only when you choose Save."}</span><button className="primary" disabled={!note.trim() || status === "saving" || status === "saved"} onClick={save}>{status === "saving" ? "Saving…" : status === "saved" ? "Saved" : "Save note"}</button></footer></article>
      <aside className="card check"><header><div><h2>Gentle check-in</h2><p>Optional, no right answers.</p></div><span>✦</span></header><label htmlFor="mood">Mood</label><select id="mood" value={mood} onChange={event => { setMood(event.target.value); dirty(); }}><option value="steady">Steady</option><option value="tender">Tender</option><option value="energized">Energized</option><option value="low">Low</option></select><label htmlFor="energy">Energy <em>0–10</em></label><input id="energy" type="range" min="0" max="10" value={energy} onChange={event => { setEnergy(event.target.value); dirty(); }}/><div className="range"><span>Low</span><span>{energy}</span><span>High</span></div><label htmlFor="sleep">Sleep</label><div className="hours"><input id="sleep" type="number" min="0" max="24" step=".5" value={sleep} onChange={event => { setSleep(event.target.value); dirty(); }} placeholder="—"/><span>hours</span></div><p className="future">◌ Menstrual Period tracking will be added later.</p></aside></section>
  </>;
}

function History({ refresh }: { refresh: number }) {
  const [entries, setEntries] = useState<Entry[]>([]); const [message, setMessage] = useState("");
  useEffect(() => { api<Entry[]>("/api/entries").then(all => { setEntries(all.slice(-5).reverse()); setMessage(""); }).catch(error => setMessage(error instanceof Error ? error.message : "Could not load entries.")); }, [refresh]);
  return <><section className="heading"><p className="kicker">History</p><h1>Recent journal entries</h1><p>Five most recent notes, with the check-ins you chose to record.</p></section>{message ? <p className="notice error">{message}</p> : entries.length ? <div className="history-table-wrap"><table><caption className="sr">Five most recent journal entries</caption><thead><tr><th>Date</th><th>Mood</th><th>Energy</th><th>Sleep</th><th>Note</th></tr></thead><tbody>{entries.map(entry => <tr key={entry.id}><td>{shortDate.format(new Date(entry.occurred_at))}</td><td>{entry.mood ?? "—"}</td><td>{entry.energy ?? "—"}</td><td>{entry.sleep_hours == null ? "—" : `${entry.sleep_hours}h`}</td><td>{entry.raw_text}</td></tr>)}</tbody></table></div> : <p className="notice">No journal entries yet. Your saved notes will appear here.</p>}</>;
}

function Settings() { return <><section className="heading"><p className="kicker">Settings</p><h1>Your journal, your boundaries.</h1><p>The local launcher connects this browser to the journal automatically.</p></section><section className="list"><article className="card setting"><b>⌑</b><div><h2>Private by design</h2><p>Your journal stays on this Mac. The browser has no account or remote sync.</p></div><span>Connected</span></article><article className="card setting"><b>✦</b><div><h2>Local AI</h2><p>Optional analysis will use a model running on this device.</p></div><span>Coming later</span></article><article className="card setting"><b>↗</b><div><h2>Backups</h2><p>Encrypted local backups and export controls are planned.</p></div><span>Coming later</span></article></section></>; }

function App() { const [screen, setScreen] = useState<Screen>("today"); const [refresh, setRefresh] = useState(0); return <div className="shell"><aside><div className="brand"><b>✦</b>health-bee</div><p className="aside-copy">A quiet place to notice what your body is telling you.</p><nav><Nav active={screen === "today"} label="Today" icon="○" onClick={() => setScreen("today")}/><Nav active={screen === "history"} label="History" icon="◷" onClick={() => setScreen("history")}/><Nav active={screen === "settings"} label="Settings" icon="⚙" onClick={() => setScreen("settings")}/></nav><div className="private">⌑ <span><strong>Private by design</strong>Stored on this Mac</span></div></aside><main><header className="top">{dayLabel.format(new Date())}<span>Journal ready</span></header>{screen === "today" ? <Today onSaved={() => setRefresh(value => value + 1)}/> : screen === "history" ? <History refresh={refresh}/> : <Settings/>}</main></div>; }

createRoot(document.getElementById("root")!).render(<StrictMode><App/></StrictMode>);
