import { useState } from "react";
import { api, useData, useHash } from "./api";

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

  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">🔎 Job Discovery</div>
        {[["", "Overview"], ["opportunities", "Opportunities"], ["applications", "Applications"], ["runs", "Runs & logs"]].map(([key, label]) => (
          <a key={key} href={`#/${key}`} className={section === key ? "active" : ""}>{label}</a>
        ))}
      </nav>
      <main className="main">
        {detail ? <Detail id={Number(detail[1])} /> :
         run ? <RunDetail id={Number(run[1])} /> :
         section === "opportunities" ? <Opportunities view={params.get("view") || "ready"} /> :
         section === "applications" ? <Applications /> :
         section === "runs" ? <Runs /> : <Overview />}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
function Overview() {
  const { data, error } = useData<any>("/overview");
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const { kpis, status, ai_spend } = data;
  return (
    <>
      <header className="page-head">
        <h1>Overview</h1>
        <p className="sub">Remote & Nepal-eligible opportunities matched against your CV.</p>
      </header>
      {status.warnings.length > 0 && (
        <div className="notice warn">
          <strong>Setup to finish</strong>
          <ul>{status.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <div className="notice info">Automatic applications are <strong>off</strong> (phase 1). Nothing is ever sent — you review and apply yourself.</div>

      <section className="tiles">
        <Tile label="Ready to apply" value={kpis.ready_to_apply} href="#/opportunities?view=ready" />
        <Tile label="Needs your approval" value={kpis.needs_approval} href="#/opportunities?view=needs_approval" />
        <Tile label="Discovered (7 days)" value={kpis.discovered_7d} href="#/opportunities?view=all" />
        <Tile label="Applied" value={kpis.applied} href="#/applications" />
        <Tile label="AI spend today" value={`$${Number(ai_spend.today).toFixed(2)}`} note={`budget $${status.ai.daily_budget_usd.toFixed(2)}`} />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Discovered per day</h2>
          <p className="sub">Last 14 days · new postings found</p>
          <BarChart rows={data.daily} />
        </section>
        <section className="card">
          <h2>Last 7 days</h2>
          <p className="sub">From discovery to a match worth your time</p>
          <Funnel funnel={data.funnel} />
        </section>
      </div>

      <section className="card">
        <div className="card-head"><h2>Top matches</h2><a href="#/opportunities?view=ready">View all →</a></div>
        {data.top_matches.length === 0 ? <Empty text="No matches yet. They appear after the first run with AI analysis." /> :
          <div className="match-list">{data.top_matches.map((o: Opp) => <MatchRow key={o.id} o={o} />)}</div>}
      </section>

      <div className="grid-2">
        <section className="card">
          <div className="card-head"><h2>Recent runs</h2><a href="#/runs">All runs →</a></div>
          <RunsTable rows={data.recent_runs} />
        </section>
        <section className="card">
          <h2>Source health (7 days)</h2>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Source</th><th className="num">OK</th><th className="num">Failed</th><th className="num">Blocked</th><th className="num">Found</th><th className="num">New</th></tr></thead>
              <tbody>{data.sources.map((s: any) => (
                <tr key={s.name}><td>{s.name}</td><td className="num">{s.ok}</td><td className="num">{s.failed}</td><td className="num">{s.blocked}</td><td className="num">{s.found}</td><td className="num">{s.new}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      </div>
    </>
  );
}

function Tile({ label, value, note, href }: { label: string; value: any; note?: string; href?: string }) {
  const body = <><span className="tile-label">{label}</span><span className="tile-value">{value}</span>{note && <span className="tile-note">{note}</span>}</>;
  return href ? <a className="tile" href={href}>{body}</a> : <div className="tile">{body}</div>;
}

function BarChart({ rows }: { rows: { day: string; discovered: number; matches: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...rows.map((r) => r.discovered));
  const niceMax = Math.ceil(max / 5) * 5 || 5;
  const w = 560, h = 180, pad = { l: 32, b: 22, t: 8 }, band = (w - pad.l) / rows.length, bar = Math.min(24, band * 0.6);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / niceMax);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Postings discovered per day">
        {[0, niceMax / 2, niceMax].map((t) => (
          <g key={t}><line x1={pad.l} x2={w} y1={y(t)} y2={y(t)} className="grid" /><text x={pad.l - 6} y={y(t) + 4} className="axis" textAnchor="end">{t}</text></g>
        ))}
        {rows.map((r, i) => {
          const x = pad.l + i * band + (band - bar) / 2, top = y(r.discovered), base = y(0), height = base - top;
          const rr = Math.min(4, height);
          const d = height <= 0 ? "" : `M${x},${base} V${top + rr} Q${x},${top} ${x + rr},${top} H${x + bar - rr} Q${x + bar},${top} ${x + bar},${top + rr} V${base} Z`;
          return (
            <g key={r.day} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={pad.l + i * band} y={pad.t} width={band} height={h - pad.t - pad.b} fill="transparent" />
              {d && <path d={d} className={`bar ${hover === i ? "hot" : ""}`} />}
              {(i % 2 === 1 || rows.length < 8) && <text x={x + bar / 2} y={h - 6} className="axis" textAnchor="middle">{r.day.slice(8)}</text>}
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div className="tooltip" style={{ left: `${((pad.l + hover * band + band / 2) / w) * 100}%` }}>
          <strong>{rows[hover].discovered}</strong> discovered<br /><strong>{rows[hover].matches}</strong> matches<br /><span>{fmtDate(rows[hover].day)}</span>
        </div>
      )}
    </div>
  );
}

function Funnel({ funnel }: { funnel: any }) {
  const steps: [string, number][] = [
    ["Discovered", funnel.discovered], ["Unique postings", funnel.unique_postings], ["Passed filters", funnel.passed_filters],
    ["Analysed by AI", funnel.analyzed], ["Matches (70+)", funnel.matches], ["Strong matches (85+)", funnel.strong_matches],
  ];
  const max = Math.max(1, steps[0][1]);
  return (
    <div className="funnel">
      {steps.map(([label, n]) => (
        <div key={label} className="funnel-row">
          <span className="funnel-label">{label}</span>
          <span className="funnel-track"><span className="funnel-bar" style={{ width: `${Math.max(n ? 2 : 0, (n / max) * 100)}%` }} /></span>
          <span className="funnel-value">{n}</span>
        </div>
      ))}
    </div>
  );
}

function MatchRow({ o }: { o: Opp }) {
  return (
    <a className="match" href={`#/opportunities/${o.id}`}>
      <Score value={o.match_score} />
      <span className="match-main">
        <span className="match-title">{o.title}</span>
        <span className="match-meta">{o.company_name || "Unknown company"} · {o.remote_type} · {o.employment_type?.replace("_", " ")} {salary(o) && `· ${salary(o)}`}</span>
      </span>
      <span className="match-chips">
        <Chip tone={eligTone(o.nepal_eligibility)}>{ELIG[o.nepal_eligibility] ?? ""}</Chip>
        <Chip tone={o.recommendation === "blocked" ? "bad" : "neutral"}>{REVIEW[o.review_status] || REC[o.recommendation] || o.pipeline_status}</Chip>
      </span>
    </a>
  );
}

// ---------------------------------------------------------------------------
function Opportunities({ view }: { view: string }) {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("score");
  const [page, setPage] = useState(1);
  const { data, error } = useData<any>(`/opportunities?view=${view}&sort=${sort}&page=${page}&page_size=25${search ? `&q=${encodeURIComponent(search)}` : ""}`);
  return (
    <>
      <header className="page-head">
        <h1>Opportunities</h1>
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
          <option value="score">Best match</option><option value="newest">Newest found</option><option value="posted">Recently posted</option>
        </select>
        <button className="btn">Search</button>
      </form>
      {error && <ErrorBox message={error} />}
      {!data ? <Loading /> : data.items.length === 0 ? <Empty text="Nothing here yet." /> : (
        <section className="card flush">
          <div className="table-wrap">
            <table className="clickable">
              <thead><tr><th>Score</th><th>Role</th><th>Status</th><th>Nepal</th><th>Pay</th><th>Source</th><th>Found</th></tr></thead>
              <tbody>
                {data.items.map((o: Opp) => (
                  <tr key={o.id} onClick={() => (window.location.hash = `#/opportunities/${o.id}`)}>
                    <td><Score value={o.match_score} /></td>
                    <td><div className="cell-title">{o.title}</div><div className="cell-sub">{o.company_name || "Unknown company"} · {o.remote_type}{o.filter_reasons?.length ? ` · ${o.filter_reasons[0]}` : ""}</div></td>
                    <td><Chip tone={o.recommendation === "blocked" ? "bad" : "neutral"}>{REVIEW[o.review_status] || REC[o.recommendation] || o.pipeline_status.replace("_", " ")}</Chip></td>
                    <td><Chip tone={eligTone(o.nepal_eligibility)}>{ELIG[o.nepal_eligibility] ?? ""}</Chip></td>
                    <td className="nowrap">{salary(o)}</td>
                    <td>{o.source}</td>
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
            {o.apply_email ? <a className="btn ghost" href={`mailto:${o.apply_email}`}>Email: {o.apply_email}</a> :
             o.apply_url && o.apply_url !== o.source_url ? <a className="btn ghost" href={o.apply_url} target="_blank" rel="noreferrer">Apply page ↗</a> : null}
            <span className="sub">Apply method: {o.apply_method.replace("_", " ")}</span>
          </div>
        </div>
      </header>

      <section className="card actions">
        <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional note (saved with your decision)" />
        <div className="btn-row">
          <button className="btn good" disabled={!!busy} onClick={() => decide("approve")}>Approve</button>
          <button className="btn" disabled={!!busy || !!data.application} onClick={() => decide("mark_applied")}>I applied</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("reject")}>Reject</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("dismiss")}>Dismiss</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => decide("reopen")}>Reopen</button>
          <button className="btn ghost" disabled={!!busy || !a} onClick={() => act("materials", `/opportunities/${id}/materials`)}>
            {busy === "materials" ? "Generating… (1–3 min)" : m ? "Regenerate CV & letter" : "Generate CV & letter"}
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
              <div key={i} className="req"><strong>{r.requirement}</strong> <Chip tone={r.strength === "strong" ? "good" : "warn"}>{r.strength}</Chip><p className="quote">“{r.cv_evidence}”</p></div>
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
function Applications() {
  const { data, error, reload } = useData<any>("/applications");
  const update = async (id: number, status: string) => {
    await api(`/applications/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
    reload();
  };
  return (
    <>
      <header className="page-head"><h1>Applications</h1><p className="sub">Every application you recorded. Duplicate applications are blocked.</p></header>
      {error && <ErrorBox message={error} />}
      {!data ? <Loading /> : data.items.length === 0 ? <Empty text="No applications yet. Open an opportunity and click “I applied” after applying." /> : (
        <section className="card flush"><div className="table-wrap"><table>
          <thead><tr><th>Applied</th><th>Role</th><th>Score</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((a: any) => (
            <tr key={a.id}>
              <td className="nowrap">{fmtDate(a.applied_at)}</td>
              <td><a href={`#/opportunities/${a.opportunity_id}`} className="cell-title">{a.title}</a><div className="cell-sub">{a.company_name}</div></td>
              <td><Score value={a.match_score} /></td>
              <td><select value={a.status} onChange={(e) => update(a.id, e.target.value)}>
                {["applied", "interviewing", "offer", "rejected", "no_response", "withdrawn"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
              </select></td>
            </tr>
          ))}</tbody>
        </table></div></section>
      )}
    </>
  );
}

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
