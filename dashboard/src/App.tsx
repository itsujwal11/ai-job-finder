import { useEffect, useState } from "react";
import { api, useData, useHash } from "./api";
import Guide from "./Guide";
import Overview from "./Overview";
import { Sidebar, TopBar } from "./Shell";
import Sources from "./Sources";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Opp = Record<string, any>;

const REC: Record<string, string> = {
  auto_apply_candidate: "Top match",
  manual_apply: "Apply manually",
  approval_required: "Needs approval",
  ignore: "Ignored",
  blocked: "Blocked",
};
const REVIEW: Record<string, string> = {
  pending_approval: "Awaiting your approval",
  ready_to_apply: "Ready to apply",
  auto_apply_disabled: "Would auto-apply (off)",
  approved: "Approved",
  rejected: "Rejected",
  dismissed: "Dismissed",
  applied: "Applied",
  none: "",
};
const ELIG: Record<string, string> = {
  eligible: "Nepal eligible",
  likely_eligible: "Likely eligible",
  unclear: "Eligibility unclear",
  not_eligible: "Not open to Nepal",
};
const VIEW_HELP: Record<string, string> = {
  ready: "Passed every check and scored well. Open one, then apply on the company's own site.",
  needs_approval: "Scored 70-84. Worth a look, but read the reasoning before you spend time on it.",
  applied: "You marked these as applied. The same company and role is never suggested again.",
  analyzed: "Everything the AI has scored, including the ones that did not make the cut.",
  awaiting: "Queued for AI scoring, highest queue rank first. Usually the daily AI quota ran out — these are picked up on the next run.",
  filtered: "Stopped by the free rules: wrong role, too senior, closed to Nepal, stale or scam-shaped. These never cost an AI call.",
  rejected: "You rejected or dismissed these.",
  duplicates: "The same role already stored from another source.",
  all: "Every posting the system has stored.",
};

const VIEWS: [string, string][] = [
  ["ready", "Ready to apply"],
  ["needs_approval", "Needs approval"],
  ["applied", "Applied"],
  ["analyzed", "Analysed"],
  ["awaiting", "Awaiting AI"],
  ["filtered", "Filtered out"],
  ["rejected", "Rejected"],
  ["duplicates", "Duplicates"],
  ["all", "All"],
];

const fmtDate = (v?: string | null) => (v ? new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—");
const fmtTime = (v?: string | null) => (v ? new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const salary = (o: Opp) => {
  const lo = o.salary_npr_monthly_min, hi = o.salary_npr_monthly_max;
  if (lo == null && hi == null) return "";
  const f = (n: number) => `NPR ${Math.round(Number(n)).toLocaleString()}`;
  return lo != null && hi != null && lo !== hi ? `${f(lo)}–${Math.round(Number(hi)).toLocaleString()}/mo` : `${f(lo ?? hi)}/mo`;
};

function Score({ value, large }: { value: number | null; large?: boolean }) {
  if (value == null) return <span className={`score muted ${large ? "large" : ""}`}>—</span>;
  const tone = value >= 85 ? "good" : value >= 70 ? "accent" : "muted";
  return <span className={`score ${tone} ${large ? "large" : ""}`}>{value}</span>;
}

function Chip({ children, tone = "neutral" }: { children: React.ReactNode; tone?: string }) {
  return children ? <span className={`chip ${tone}`}>{children}</span> : null;
}

const eligTone = (e?: string) => (e === "eligible" ? "good" : e === "likely_eligible" ? "accent" : e === "not_eligible" ? "bad" : "warn");

// ---------------------------------------------------------------------------
export default function App() {
  const hash = useHash();
  const [path, query = ""] = hash.slice(1).split("?");
  const params = new URLSearchParams(query);
  const detail = path.match(/^\/opportunities\/(\d+)/);
  const run = path.match(/^\/runs\/(\d+)/);
  const section = path.split("/")[1] || "";
  const [runKey, setRunKey] = useState(0);

  return (
    <div className="shell">
      <Sidebar section={section} />
      <main className="main">
        <TopBar onRunFinished={() => setRunKey((k) => k + 1)} />
        {detail ? <Detail id={Number(detail[1])} /> :
         run ? <RunDetail id={Number(run[1])} /> :
         section === "opportunities" ? <Opportunities key={runKey} view={params.get("view") || "ready"} q0={params.get("q") || ""} /> :
         section === "applications" ? <ApplicationTracker /> :
         section === "sources" ? <Sources /> :
         section === "runs" ? <Runs key={runKey} /> :
         section === "guide" ? <Guide /> : <Overview key={runKey} />}
      </main>
    </div>
  );
}


// ---------------------------------------------------------------------------
function Opportunities({ view, q0 = "" }: { view: string; q0?: string }) {
  const [q, setQ] = useState(q0);
  const [search, setSearch] = useState(q0);
  const [sort, setSort] = useState("score");
  const [page, setPage] = useState(1);
  const { data, error } = useData<any>(`/opportunities?view=${view}&sort=${sort}&page=${page}&page_size=25${search ? `&q=${encodeURIComponent(search)}` : ""}`);
  return (
    <>
      <header className="page-head">
        <div>
          <h1>Opportunities</h1>
          <p className="sub">{VIEW_HELP[view] ?? "Every posting the system has stored."}</p>
        </div>
        <a className="btn ghost" href="/api/ui/export/opportunities.csv">Export CSV</a>
      </header>
      <div className="tabs">
        {VIEWS.map(([key, label]) => (
          <a key={key} href={`#/opportunities?view=${key}`} className={view === key ? "active" : ""} onClick={() => setPage(1)}>
            {label} <span className="count">{data?.counts?.[key] ?? ""}</span>
          </a>
        ))}
      </div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); setSearch(q); setPage(1); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title or company" />
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="score">Best match</option>
          <option value="prescore">Queue order (next for AI)</option>
          <option value="newest">Newest found</option>
          <option value="posted">Recently posted</option>
        </select>
        <button className="btn">Search</button>
      </form>
      {error && <ErrorBox message={error} />}
      {!data ? <Loading /> : data.items.length === 0 ? <Empty text="Nothing here yet." /> : (
        <section className="card flush">
          <div className="table-wrap">
            <table className="clickable">
              <thead><tr><th title="0-100 AI match score">Score</th><th>Role</th><th>Status</th><th>Nepal</th><th>Pay</th><th>Email</th><th>Source</th><th title="Rule-based queue rank used before AI scoring">Queue</th><th>Found</th></tr></thead>
              <tbody>
                {data.items.map((o: Opp) => (
                  <tr key={o.id} onClick={() => (window.location.hash = `#/opportunities/${o.id}`)}>
                    <td><Score value={o.match_score} /></td>
                    <td><div className="cell-title">{o.title}</div><div className="cell-sub">{[o.company_name || "Company not stated", o.remote_type !== "unknown" ? o.remote_type : null, o.filter_reasons?.[0]].filter(Boolean).join(" · ")}</div></td>
                    <td><Chip tone={o.recommendation === "blocked" ? "bad" : "neutral"}>{REVIEW[o.review_status] || REC[o.recommendation] || o.pipeline_status.replace("_", " ")}</Chip></td>
                    <td><Chip tone={eligTone(o.nepal_eligibility)}>{ELIG[o.nepal_eligibility] ?? ""}</Chip></td>
                    <td className="nowrap">{salary(o)}</td>
                    <td>{o.apply_email ? <a href={`mailto:${o.apply_email}`} onClick={(e) => e.stopPropagation()} title={o.apply_email}>✉</a> : "—"}</td>
                    <td className="src">{o.source}</td>
                    <td className="num dim">{o.match_score == null ? (o.prescore ?? "—") : "—"}</td>
                    <td className="nowrap">{fmtDate(o.first_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
            <span>Page {page} of {Math.max(1, Math.ceil(data.total / 25))} · {data.total} total</span>
            <button className="btn ghost" disabled={page * 25 >= data.total} onClick={() => setPage(page + 1)}>Next →</button>
          </div>
        </section>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
function Detail({ id }: { id: number }) {
  const { data, error, setData } = useData<any>(`/opportunities/${id}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [tab, setTab] = useState<"cv" | "cover">("cv");
  const [notes, setNotes] = useState("");

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const o = data.opportunity;
  const a = o.analysis;
  const m = data.materials[0];

  const act = async (label: string, path: string, body?: object) => {
    setBusy(label); setActionError(null);
    try {
      setData(await api<any>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const decide = (action: string) => act(action, `/opportunities/${id}/decision`, { action, notes: notes || null });

  return (
    <>
      <a href="#/opportunities" className="back">← Opportunities</a>
      <header className="detail-head card">
        <Score value={o.match_score} large />
        <div className="detail-title">
          <h1>{o.title}</h1>
          <p className="sub">{o.company_name || "Unknown company"} · {o.location_text || "Location not stated"} · {o.employment_type?.replace("_", " ")} {salary(o) && `· ${salary(o)}`}</p>
          <div className="chips">
            <Chip tone={o.recommendation === "blocked" ? "bad" : "accent"}>{REC[o.recommendation] ?? o.pipeline_status}</Chip>
            <Chip>{REVIEW[o.review_status]}</Chip>
            <Chip tone={eligTone(o.nepal_eligibility)}>{ELIG[o.nepal_eligibility] ?? ""}</Chip>
            <Chip tone={o.legitimacy_verdict === "legitimate" ? "good" : o.legitimacy_verdict === "suspicious" ? "bad" : "warn"}>{o.legitimacy_verdict && `Company: ${o.legitimacy_verdict}`}</Chip>
            <Chip>{o.remote_type}</Chip>
          </div>
          <div className="links">
            <a className="btn" href={o.source_url} target="_blank" rel="noreferrer">Open posting ↗</a>
            {o.apply_url && o.apply_url !== o.source_url ? <a className="btn ghost" href={o.apply_url} target="_blank" rel="noreferrer">Apply page ↗</a> : null}
            <span className="sub">Apply method: {o.apply_method.replace("_", " ")}</span>
          </div>
        </div>
      </header>

      {/* --- Send your CV section --- */}
      {(o.apply_email || a?.application_email) && (
        <section className="card" style={{ borderLeft: "3px solid var(--accent)" }}>
          <h2>📧 Send your CV here</h2>
          <p style={{ fontSize: "1.1em", margin: "0.5em 0" }}>
            <a className="btn good" href={`mailto:${o.apply_email || a.application_email}?subject=Application for ${encodeURIComponent(o.title)}${o.company_name ? ` at ${encodeURIComponent(o.company_name)}` : ""}`}>
              ✉ {o.apply_email || a.application_email}
            </a>
          </p>
          {a?.application_instructions && (
            <p className="sub"><strong>How to apply:</strong> {a.application_instructions}</p>
          )}
          <p className="sub">
            {m ? "Download your tailored CV and cover letter below, then attach them to this email."
              : "Generate your tailored CV and cover letter first (button below), then send them to this email."}
          </p>
        </section>
      )}

      <section className="card actions">
        <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional note (saved with your decision)" />
        <div className="btn-row">
          <button className="btn good" disabled={!!busy} onClick={() => decide("approve")}>Approve</button>
          <button className="btn" disabled={!!busy || !!data.application} onClick={() => decide("mark_applied")}>I applied</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("reject")}>Reject</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("dismiss")}>Dismiss</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("reopen")}>Reopen</button>
          <button className="btn ghost" disabled={!!busy || !a} onClick={() => act("materials", `/opportunities/${id}/materials`)}>
            {busy === "materials" ? "Generating…" : m ? "Regenerate CV & cover letter" : "Generate CV & cover letter"}
          </button>
          <button 
            className="btn ghost" 
            style={{ color: "var(--bad)", borderColor: "transparent", marginLeft: "auto" }} 
            disabled={!!busy} 
            onClick={async () => {
              if (window.confirm("Permanently delete this opportunity from the database? This cannot be undone.")) {
                await api(`/opportunities/${id}`, { method: "DELETE" });
                window.location.hash = "#/opportunities";
              }
            }}
          >
            🗑 Delete from DB
          </button>
        </div>
        {actionError && <div className="notice bad">{actionError}</div>}
        {o.user_notes && <p className="sub">Note: {o.user_notes}</p>}
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Why this decision</h2>
          <ul className="reasons">{[...(o.recommendation_reasons || []), ...(o.filter_reasons || [])].map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          {a && <p>{a.summary_for_candidate}</p>}
          <h3>Nepal eligibility</h3>
          <p className="quote">{o.nepal_evidence || "Not assessed yet"}</p>
          {(o.legitimacy_flags || []).length > 0 && (<><h3>Legitimacy signals</h3><ul className="reasons small">{o.legitimacy_flags.map((f: any, i: number) => <li key={i} className={f.severity}>{f.message}</li>)}</ul></>)}
          <h3>Why it won't be auto-applied</h3>
          <ul className="reasons small">{data.auto_apply_blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>
        </section>
        <section className="card">
          <h2>Match breakdown</h2>
          {o.score_breakdown ? Object.entries(o.score_breakdown).map(([k, v]: [string, any]) => (
            <div className="metric" key={k}>
              <div className="metric-head"><span>{k.replace("_", " ")}</span><span className="sub">{v.score}/100 · weight {v.weight}</span></div>
              <div className="funnel-track"><span className="funnel-bar" style={{ width: `${v.score}%` }} /></div>
              {v.reasoning && <p className="sub small">{v.reasoning}</p>}
            </div>
          )) : <Empty text="Scores appear after AI analysis." />}
        </section>
      </div>

      {a && (
        <div className="grid-2">
          <section className="card">
            <h2>Requirements you meet</h2>
            {a.matched_requirements.map((r: any, i: number) => (
              <div key={i} className="req"><strong>{r.requirement}</strong> <Chip tone={r.strength === "strong" ? "good" : "warn"}>{r.strength}</Chip><p className="quote">"{r.cv_evidence}"</p></div>
            ))}
          </section>
          <section className="card">
            <h2>Gaps</h2>
            {a.missing_requirements.length ? <ul className="reasons">{a.missing_requirements.map((r: string) => <li key={r}>{r}</li>)}</ul> : <Empty text="No gaps found." />}
          </section>
        </div>
      )}

      <section className="card">
        <div className="card-head">
          <h2>Application materials</h2>
          {m && <Chip tone={m.verified ? "good" : "bad"}>{m.verified ? "Fact-checked against your CV" : "Unsupported claims — edit before sending"}</Chip>}
        </div>
        {!m ? <Empty text={o.materials_status === "failed" ? "Generation failed — try again." : "No tailored CV or cover letter yet."} /> : (
          <>
            <div className="tabs small">
              <button className={tab === "cv" ? "active" : ""} onClick={() => setTab("cv")}>Tailored CV</button>
              <button className={tab === "cover" ? "active" : ""} onClick={() => setTab("cover")}>Cover letter</button>
            </div>
            <pre className="doc">{tab === "cv" ? m.cv_markdown : m.cover_letter}</pre>
            <div className="btn-row">
              <button className="btn ghost" onClick={() => navigator.clipboard.writeText(tab === "cv" ? m.cv_markdown : m.cover_letter)}>Copy</button>
              <a className="btn ghost" href={`/api/ui/materials/${m.id}/${tab === "cv" ? "cv" : "cover_letter"}.docx`}>Download .docx</a>
              <a className="btn ghost" href={`/api/ui/materials/${m.id}/${tab === "cv" ? "cv" : "cover_letter"}.md`}>Download .md</a>
            </div>
            {m.verification?.unsupported_claims?.length > 0 && (
              <ul className="reasons small">{m.verification.unsupported_claims.map((c: any, i: number) => <li key={i} className="high">{c.claim} — {c.reason}</li>)}</ul>
            )}
          </>
        )}
      </section>

      <section className="card">
        <details>
          <summary>Full job description</summary>
          <pre className="doc">{o.description}</pre>
        </details>
      </section>

      <section className="card">
        <h2>Timeline</h2>
        <ul className="timeline">
          {data.events.map((e: any) => <li key={e.id} className={e.level}><span className="sub">{fmtTime(e.occurred_at)}</span> {e.message}</li>)}
          {data.sightings.map((s: any, i: number) => <li key={`s${i}`}><span className="sub">{fmtTime(s.seen_at)}</span> Seen on {s.source}</li>)}
        </ul>
      </section>
    </>
  );
}

// ---------------------------------------------------------------------------
// APPLICATION TRACKER — Premium kanban-style UI
// ---------------------------------------------------------------------------
const STATUS_COLUMNS: { key: string; label: string; color: string; icon: string; emptyText: string }[] = [
  { key: "applied", label: "Applied", color: "var(--accent)", icon: "📨", emptyText: "No applications yet" },
  { key: "interviewing", label: "Interviewing", color: "var(--warn)", icon: "💬", emptyText: "No interviews yet" },
  { key: "offer", label: "Offers", color: "var(--good)", icon: "🎉", emptyText: "No offers yet" },
  { key: "rejected", label: "Rejected", color: "var(--bad)", icon: "✗", emptyText: "None rejected" },
  { key: "no_response", label: "No Response", color: "var(--muted)", icon: "⏳", emptyText: "All responded" },
  { key: "withdrawn", label: "Withdrawn", color: "var(--border)", icon: "↩", emptyText: "None withdrawn" },
];

function ApplicationTracker() {
  const { data, error, reload } = useData<any>("/applications");
  const [page, setPage] = useState(1);

  const update = async (id: number, status: string) => {
    await api(`/applications/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    reload();
  };

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;

  const items = data.items || [];
  const grouped: Record<string, any[]> = {};
  STATUS_COLUMNS.forEach((c) => { grouped[c.key] = []; });
  items.forEach((a: any) => {
    if (grouped[a.status]) grouped[a.status].push(a);
    else grouped["applied"].push(a);
  });

  // Stats
  const total = items.length;
  const interviewing = grouped["interviewing"].length;
  const offers = grouped["offer"].length;
  const responseRate = total > 0 ? Math.round(((interviewing + offers + grouped["rejected"].length) / total) * 100) : 0;

  // Pagination
  const PAGE_SIZE = 12;
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const start = (page - 1) * PAGE_SIZE;
  const paginatedItems = items.slice(start, start + PAGE_SIZE);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Application Tracker</h1>
          <p className="sub">Track every application from submission to outcome. Mark a job as "I applied" to add it here.</p>
        </div>
      </header>

      {total === 0 ? (
        <div className="tracker-empty-state">
          <div className="tracker-empty-icon">📋</div>
          <div className="tracker-empty-title">No applications yet</div>
          <div className="tracker-empty-desc">
            When you find a great opportunity, open it and click <strong>"I applied"</strong> after submitting your application. It will appear here so you can track its progress.
          </div>
          <a className="btn" href="#/opportunities?view=ready">Browse opportunities →</a>
        </div>
      ) : (
        <>
          <div className="tracker-stats">
            <div className="tracker-stat">
              <div className="tracker-stat-value">{total}</div>
              <div className="tracker-stat-label">Total Applied</div>
            </div>
            <div className="tracker-stat">
              <div className="tracker-stat-value warn">{interviewing}</div>
              <div className="tracker-stat-label">Interviewing</div>
            </div>
            <div className="tracker-stat">
              <div className="tracker-stat-value good">{offers}</div>
              <div className="tracker-stat-label">Offers</div>
            </div>
            <div className="tracker-stat">
              <div className="tracker-stat-value">{responseRate}%</div>
              <div className="tracker-stat-label">Response Rate</div>
            </div>
            <div className="tracker-stat">
              <div className="tracker-stat-value">{grouped["no_response"].length}</div>
              <div className="tracker-stat-label">Awaiting Reply</div>
            </div>
          </div>

          <section className="card flush">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Applied</th>
                    <th>Role & Company</th>
                    <th>Score</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedItems.map((a: any, i: number) => (
                    <tr key={a.id} style={{ animationDelay: `${i * 0.02}s` }} className="animate-fade-up">
                      <td className="nowrap">{fmtDate(a.applied_at)}</td>
                      <td>
                        <a href={`#/opportunities/${a.opportunity_id}`} className="cell-title">{a.title}</a>
                        <div className="cell-sub">{a.company_name}</div>
                      </td>
                      <td><Score value={a.match_score} /></td>
                      <td style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                        <select className="status-select" value={a.status} onChange={(e) => update(a.id, e.target.value)}>
                          {STATUS_COLUMNS.map((s) => (
                            <option key={s.key} value={s.key}>{s.label}</option>
                          ))}
                        </select>
                        <button 
                          className="btn ghost small" 
                          style={{ padding: "6px", color: "var(--bad)", borderColor: "transparent" }}
                          onClick={async () => {
                            if (window.confirm("Delete this opportunity completely from the database?")) {
                              await api(`/opportunities/${a.opportunity_id}`, { method: "DELETE" });
                              reload();
                            }
                          }}
                          title="Delete from database"
                        >
                          🗑️
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            
            {totalPages > 1 && (
              <div className="pagination">
                <button className="btn ghost small" disabled={page === 1} onClick={() => setPage(page - 1)}>← Previous</button>
                <span className="page-info">Page {page} of {totalPages}</span>
                <button className="btn ghost small" disabled={page === totalPages} onClick={() => setPage(page + 1)}>Next →</button>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
function Runs() {
  const { data, error } = useData<any>("/runs");
  const events = useData<any>("/events?level=error&limit=20");
  return (
    <>
      <header className="page-head"><h1>Runs & logs</h1><p className="sub">Daily runs are started by n8n at 06:00 Kathmandu time.</p></header>
      {error && <ErrorBox message={error} />}
      <section className="card">{!data ? <Loading /> : <RunsTable rows={data.items} />}</section>
      <section className="card">
        <h2>Recent errors</h2>
        {events.data?.items?.length ? <ul className="timeline">{events.data.items.map((e: any) => <li key={e.id} className="error"><span className="sub">{fmtTime(e.occurred_at)}</span> {e.message}</li>)}</ul> : <Empty text="No errors." />}
      </section>
    </>
  );
}

function RunsTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <Empty text="No runs yet. Run the workflow in n8n." />;
  return (
    <div className="table-wrap"><table className="clickable">
      <thead><tr><th>Run</th><th>Started</th><th>Status</th><th className="num">New</th><th className="num">Analysed</th><th className="num">AI $</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.id} onClick={() => (window.location.hash = `#/runs/${r.id}`)}>
          <td>#{r.id} <span className="sub">{r.trigger}</span></td>
          <td className="nowrap">{fmtTime(r.started_at)}</td>
          <td><Chip tone={r.status === "completed" ? "good" : r.status === "failed" ? "bad" : "accent"}>{r.status}{r.status === "running" ? ` · ${r.stage}` : ""}</Chip></td>
          <td className="num">{r.stats?.discovered_new ?? "—"}</td>
          <td className="num">{r.stats?.analyzed ?? "—"}</td>
          <td className="num">{Number(r.ai_cost_usd ?? 0).toFixed(2)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function RunDetail({ id }: { id: number }) {
  const { data, error } = useData<any>(`/runs/${id}`);
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <a href="#/runs" className="back">← Runs</a>
      <header className="page-head"><h1>Run #{id}</h1><p className="sub">{fmtTime(data.run.started_at)} · {data.run.status}{data.run.error ? ` · ${data.run.error}` : ""}</p></header>
      <section className="card flush"><div className="table-wrap"><table>
        <thead><tr><th>Task</th><th>Status</th><th className="num">Found</th><th className="num">New</th><th>Note</th></tr></thead>
        <tbody>{data.tasks.map((t: any) => (
          <tr key={t.id}><td>{t.label}</td><td><Chip tone={t.status === "ok" ? "good" : t.status === "failed" ? "bad" : t.status === "blocked" ? "warn" : "neutral"}>{t.status}</Chip></td>
            <td className="num">{t.items_found}</td><td className="num">{t.items_new}</td><td className="cell-sub">{t.error}</td></tr>
        ))}</tbody>
      </table></div></section>
      <section className="card"><h2>Log</h2><ul className="timeline">{data.events.map((e: any) => <li key={e.id} className={e.level}><span className="sub">{fmtTime(e.occurred_at)}</span> {e.message}</li>)}</ul></section>
    </>
  );
}

const Loading = () => <div className="sub pad">Loading…</div>;
const Empty = ({ text }: { text: string }) => <div className="empty">{text}</div>;
const ErrorBox = ({ message }: { message: string }) => <div className="notice bad">{message}</div>;
