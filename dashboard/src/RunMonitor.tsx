import { useEffect, useRef, useState } from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */

const ago = (iso?: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${Math.round(s / 3600)}h`;
};

const STAGES: [string, string][] = [
  ["fetching", "Reading sources"],
  ["processing", "Filtering & scoring"],
  ["notifying", "Sending digest"],
  ["done", "Finished"],
];

const elapsed = (from?: string | null) => {
  if (!from) return "";
  const s = Math.max(0, (Date.now() - new Date(from).getTime()) / 1000);
  const m = Math.floor(s / 60);
  return m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`;
};

/**
 * Floating card that follows a run from start to finish.
 *
 * `state` is the polled /runs/active payload. The card opens itself when a run starts, stays
 * up with a summary when it ends, and can be dismissed. It reports only what the API measured -
 * no invented progress.
 */
export default function RunMonitor({ state, justStarted, onDismiss }: {
  state: any; justStarted: boolean; onDismiss: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [minimised, setMinimised] = useState(false);
  const [finishedRun, setFinishedRun] = useState<any>(null);
  const wasRunning = useRef(false);
  const [, force] = useState(0);

  const run = state?.run;
  const progress = state?.progress;

  // Keep elapsed time ticking while a run is live.
  useEffect(() => {
    if (!run) return;
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [run]);

  useEffect(() => {
    if (justStarted) { setOpen(true); setMinimised(false); setFinishedRun(null); }
  }, [justStarted]);

  useEffect(() => {
    if (run) { wasRunning.current = true; setFinishedRun(null); setOpen(true); }
    else if (wasRunning.current) { wasRunning.current = false; setFinishedRun(state?.last ?? null); }
  }, [run, state]);

  if (!open) return null;
  const live = Boolean(run);
  const shown = run ?? finishedRun;
  if (!shown && !justStarted) return null;

  const stageIndex = Math.max(0, STAGES.findIndex(([k]) => k === (progress?.stage ?? "fetching")));
  const pct = Math.round((progress?.fraction ?? 0) * 100);
  const close = () => { setOpen(false); setFinishedRun(null); onDismiss(); };

  if (minimised) {
    return (
      <button className="monitor-mini" onClick={() => setMinimised(false)}>
        <span className={`pulse ${live ? "on" : "good"}`} />
        {live ? `Run #${shown?.id} · ${pct}%` : `Run #${shown?.id} finished`}
      </button>
    );
  }

  return (
    <aside className="monitor" role="status" aria-live="polite">
      <header className="monitor-head">
        <span className={`pulse ${live ? "on" : "good"}`} />
        <strong>{live ? "Scan running" : "Scan finished"}</strong>
        {shown && <span className="chip muted">Run #{shown.id}</span>}
        <span className="monitor-actions">
          {live && (
            <button
              onClick={async () => {
                if (window.confirm("Stop the current scan?")) {
                  await fetch("/api/ui/runs/active", { method: "DELETE" });
                }
              }}
              title="Stop Scan"
              aria-label="Stop Scan"
              style={{ color: "var(--bad)", fontWeight: "bold" }}
            >
              Stop
            </button>
          )}
          <button onClick={() => setMinimised(true)} title="Minimise" aria-label="Minimise">—</button>
          <button onClick={close} title="Close" aria-label="Close">✕</button>
        </span>
      </header>

      {!shown ? (
        <div className="monitor-body"><p className="monitor-note">Starting the agent…</p></div>
      ) : (
        <div className="monitor-body">
          <ol className="monitor-stages">
            {STAGES.map(([key, label], i) => (
              <li key={key} className={i < stageIndex || !live ? "done" : i === stageIndex ? "now" : ""}>
                <span className="stage-dot" />{label}
              </li>
            ))}
          </ol>

          <div className="monitor-bar" aria-label={`${pct}% of fetch tasks done`}>
            <span style={{ width: `${live ? pct : 100}%` }} />
          </div>
          <p className="monitor-note">
            {live
              ? <>{progress?.tasks_done ?? 0} of {progress?.tasks_total ?? 0} sources read · {elapsed(shown.started_at)} elapsed</>
              : <>{progress?.tasks_done ?? 0} sources read in {elapsed0(shown)} · {progress?.tasks_failed ?? 0} failed, {progress?.tasks_blocked ?? 0} blocked</>}
          </p>
          {live && progress?.current && <p className="monitor-current" title={progress.current}>▸ {progress.current}</p>}

          <div className="monitor-stats">
            <Stat label="Found" value={progress?.discovered ?? 0} />
            <Stat label="Filtered" value={(progress?.processed ?? 0) - (progress?.analyzed ?? 0)} />
            <Stat label="Scored" value={progress?.analyzed ?? 0} />
            <Stat label="Matches" value={progress?.matches ?? 0} accent />
          </div>

          {state?.recent?.length > 0 && (
            <div className="monitor-feed">
              {state.recent.slice(0, 4).map((r: any, i: number) => (
                <div key={i}>
                  <span className={`feed-dot ${r.status === "ok" ? "good" : r.status === "failed" ? "bad" : r.status === "blocked" ? "warn" : "muted"}`} />
                  <span className="monitor-feed-text" title={r.label}>
                    {r.label}{r.items_found ? ` · ${r.items_found} found` : ""}
                  </span>
                  <span className="feed-time">{ago(r.finished_at)}</span>
                </div>
              ))}
            </div>
          )}

          {!live && (
            <div className="monitor-foot">
              {(progress?.matches ?? 0) > 0
                ? <a className="btn small" href="#/opportunities?view=needs_approval">Review {progress.matches} match{progress.matches > 1 ? "es" : ""} →</a>
                : <a className="btn ghost small" href={`#/runs/${shown.id}`}>See what happened →</a>}
              {(progress?.analyzed ?? 0) === 0 && (
                <span className="monitor-note">
                  Nothing was AI-scored. Usually the daily AI quota is spent — the queue is kept for the next run.
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function elapsed0(run: any) {
  if (!run?.started_at || !run?.finished_at) return "—";
  const s = Math.max(0, (new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000);
  const m = Math.floor(s / 60);
  return m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`;
}

function Stat({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="monitor-stat">
      <span className={accent ? "accent" : ""}>{value}</span>
      {label}
    </div>
  );
}
