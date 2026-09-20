import { useEffect, useRef, useState } from "react";
import { api, useData } from "./api";
import RunMonitor from "./RunMonitor";
import ThemeToggle from "./Theme";

/* eslint-disable @typescript-eslint/no-explicit-any */

const NAV: [string, string, string][] = [
  ["", "Overview", "▤"],
  ["opportunities", "Opportunities", "⚡"],
  ["applications", "Application Tracker", "➤"],
  ["sources", "Sources", "⛓"],
  ["runs", "Runs & logs", "▸"],
  ["guide", "How it works", "?"],
];

/** Live run state, polled. Shared by the top bar pill and the Run now button. */
export function useRunState(fast = false) {
  const [state, setState] = useState<any>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    const poll = () => api<any>("/runs/active").then((d) => live && setState(d)).catch(() => {});
    poll();
    // A live run is worth watching closely; an idle one is not.
    const timer = setInterval(poll, fast ? 2000 : 6000);
    return () => { live = false; clearInterval(timer); };
  }, [tick, fast]);
  return { state, run: state?.run ?? null, refresh: () => setTick((t) => t + 1) };
}

export function Sidebar({ section }: { section: string }) {
  const { data } = useData<any>("/opportunities?view=ready&page_size=1");
  const { data: srcs } = useData<any>("/sources");
  const { run } = useRunState();
  const counts = data?.counts;
  const liveSources = srcs?.sources?.filter((s: any) => s.enabled).length;

  const badge = (key: string) => {
    if (key === "opportunities" && counts) return { text: String(counts.ready + counts.needs_approval), live: false };
    if (key === "applications" && counts?.applied) return { text: String(counts.applied), live: false };
    if (key === "sources" && liveSources) return { text: `${liveSources} live`, live: true };
    if (key === "runs" && run) return { text: "active", live: true };
    return null;
  };

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">◎</span>
        <span>
          <span className="brand-name">Job Discovery</span>
          <span className="brand-sub">Nepal-eligible CV matching</span>
        </span>
      </div>
      <nav className="nav">
        {NAV.map(([key, label, icon]) => {
          const b = badge(key);
          return (
            <a key={key} href={`#/${key}`} className={section === key ? "active" : ""}>
              <span className="nav-icon">{icon}</span>
              {label}
              {b && <span className={`nav-badge${b.live ? " live" : ""}`}>{b.text}</span>}
            </a>
          );
        })}
      </nav>
      <div className="side-card">
        <span className="side-card-label">Applications</span>
        <span className="side-card-main">Never sent automatically</span>
        <span className="side-card-sub">You review and submit every one yourself.</span>
      </div>
      <div className="sidebar-footer">
        <span className="sidebar-footer-label">Theme</span>
        <ThemeToggle />
      </div>
    </aside>
  );
}

/** Search box, live run pill and the Run now trigger. */
export function TopBar({ onRunFinished }: { onRunFinished?: () => void }) {
  const [running, setRunning] = useState(false);
  const { state, run, refresh } = useRunState(running);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [justStarted, setJustStarted] = useState(0);
  const wasRunning = useRef(false);

  useEffect(() => {
    setRunning(Boolean(run));
    if (run) wasRunning.current = true;
    else if (wasRunning.current) { wasRunning.current = false; onRunFinished?.(); }
  }, [run, onRunFinished]);

  const start = async () => {
    setStarting(true); setError(null);
    try {
      await api<any>("/runs", { method: "POST" });
      setJustStarted((n) => n + 1);
      setRunning(true);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const busy = Boolean(run) || starting;

  return (
    <>
      <div className="topbar">
        <form
          className="topbar-search"
          onSubmit={(e) => {
            e.preventDefault();
            window.location.hash = `#/opportunities?view=all&q=${encodeURIComponent(q)}`;
          }}
        >
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search roles and companies…" aria-label="Search opportunities" />
        </form>

        <span className={`run-pill${run ? " is-live" : ""}`}>
          <span className={`pulse${run ? " on" : " good"}`} />
          {run ? `Run #${run.id} · ${run.stage}` : "Idle"}
        </span>

        <button className="btn" onClick={start} disabled={busy}>
          {busy ? "Scanning…" : "▸ Run scan now"}
        </button>
        {error && <span className="run-now-error">{error}</span>}
      </div>

      <RunMonitor state={state} justStarted={justStarted > 0} onDismiss={() => setJustStarted(0)} />
    </>
  );
}
