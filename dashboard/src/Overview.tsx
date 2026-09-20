import { useData } from "./api";
import { useRunState } from "./Shell";

/* eslint-disable @typescript-eslint/no-explicit-any */

const REC: Record<string, string> = {
  auto_apply_candidate: "Top match",
  manual_apply: "Ready",
  approval_required: "Needs judgement",
  ignore: "Below threshold",
  blocked: "Blocked",
};
const ELIG: Record<string, string> = {
  eligible: "Open to Nepal",
  likely_eligible: "Likely open to Nepal",
  unclear: "Eligibility unclear",
  not_eligible: "Closed to Nepal",
};

const npr = (o: any) => {
  const lo = o.salary_npr_monthly_min, hi = o.salary_npr_monthly_max;
  if (lo == null && hi == null) return null;
  const f = (n: number) => Math.round(Number(n)).toLocaleString();
  return lo != null && hi != null && lo !== hi ? `NPR ${f(lo)}–${f(hi)}/mo` : `NPR ${f(lo ?? hi)}/mo`;
};

const ago = (iso?: string | null) => {
  if (!iso) return "";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${Math.round(secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
};

export default function Overview() {
  const { data, error, reload } = useData<any>("/overview");
  const { data: act } = useData<any>("/activity?limit=14");
  const { run: liveRun } = useRunState();

  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="loading">Loading…</div>;

  const { kpis, status, ai_spend, funnel } = data;
  const budget = Number(status.ai.daily_budget_usd) || 0;
  const spent = Number(ai_spend.today) || 0;
  const noChannel = status.warnings.find((w: string) => w.includes("notification"));
  const otherWarnings = status.warnings.filter((w: string) => !w.includes("notification"));

  return (
    <>
      <header className="page-head">
        <div>
          <div className="title-row">
            <h1>Overview</h1>
            <span className={`badge-live${liveRun ? "" : " idle"}`}>
              {liveRun ? `● Scanning · ${liveRun.stage}` : "● Idle"}
            </span>
          </div>
          <p className="sub">Remote and Nepal-eligible openings, scored against <code>profile/cv.md</code>.</p>
        </div>
        <div className="head-stats">
          <div className="head-stat">
            <span className="head-stat-label">AI model</span>
            <span className="head-stat-value">{status.ai.model ?? "not configured"}</span>
          </div>
          <div className="head-stat">
            <span className="head-stat-label">Last run</span>
            <span className="head-stat-value">{act?.run ? `#${act.run.id} · ${ago(act.run.finished_at ?? act.run.started_at)}` : "never"}</span>
          </div>
        </div>
      </header>

      <div className="grid-2">
        {noChannel ? (
          <div className="notice warn notice-card">
            <span className="notice-icon">🔔</span>
            <div className="notice-body">
              <span className="notice-tag">Action needed</span>
              <strong>No notification channel</strong>
              You will only see new matches by opening this dashboard. Set <code>TELEGRAM_BOT_TOKEN</code> and{" "}
              <code>TELEGRAM_CHAT_ID</code>, or the <code>SMTP_*</code> settings, in <code>.env</code> to get a digest after each run.
            </div>
          </div>
        ) : (
          <div className="notice info notice-card">
            <span className="notice-icon">✓</span>
            <div className="notice-body">
              <span className="notice-tag calm">Ready</span>
              <strong>Setup complete</strong>
              Every source is configured and a notification channel is connected.
            </div>
          </div>
        )}

        <div className="notice info notice-card">
          <span className="notice-icon">🛡</span>
          <div className="notice-body">
            <span className="notice-tag calm">Phase 1</span>
            <strong>Applications are never sent</strong>
            Nothing is ever submitted for you. The system finds, filters and scores; you read the reasoning, then
            apply yourself on the company's own site.
            <div className="notice-foot">
              <a href="#/guide">How the scoring works →</a>
            </div>
          </div>
        </div>
      </div>

      {(() => {
        const criticalWarnings = otherWarnings.filter((w: string) => {
          const l = w.toLowerCase();
          return l.includes("budget") || l.includes("quota") || l.includes("api key") || l.includes("429");
        });
        const regularWarnings = otherWarnings.filter((w: string) => !criticalWarnings.includes(w));
        
        return (
          <>
            {criticalWarnings.length > 0 && (
              <div className="notice bad notice-card">
                <span className="notice-icon">🛑</span>
                <div className="notice-body">
                  <span className="notice-tag bad">System Blocked</span>
                  <strong>AI Limits Reached</strong>
                  <ul>{criticalWarnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
                </div>
              </div>
            )}
            
            {regularWarnings.length > 0 && (
              <div className="notice warn">
                <strong>Worth fixing</strong>
                <ul>{regularWarnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
              </div>
            )}
          </>
        );
      })()}

      <section className="tiles">
        <Tile
          label="Ready to apply" value={kpis.ready_to_apply} href="#/opportunities?view=ready"
          note="Passed every check. Open one and apply." foot="Go apply →"
        />
        <Tile
          label="Needs approval" value={kpis.needs_approval} href="#/opportunities?view=needs_approval"
          badge="Score 70–84" note="Strong fit that wants your judgement." foot="Review →"
        />
        <Tile
          label="Waiting for AI" value={kpis.awaiting_analysis} href="#/opportunities?view=awaiting"
          badge="Queued" note="Ordered best-first. Nothing is ever lost."
          meter={kpis.total ? 1 - kpis.awaiting_analysis / kpis.total : 1}
          meterNote={`${Math.round(kpis.total ? (1 - kpis.awaiting_analysis / kpis.total) * 100 : 100)}% of stored postings processed`}
        />
        <Tile
          label="Discovered (7 days)" value={kpis.discovered_7d} href="#/opportunities?view=all"
          note={`${Number(kpis.total).toLocaleString()} stored in total.`} foot={`${kpis.ats_boards} company boards tracked`}
        />
        <Tile
          label="Applied" value={kpis.applied} href="#/applications"
          note="Never suggested to you again." foot="Tracker →"
        />
        <Tile
          label="AI spend today" value={`$${spent.toFixed(2)}`} plain
          badge={budget ? `cap $${budget.toFixed(2)}` : undefined}
          note={status.ai.model ?? "no model configured"}
          meter={budget ? Math.min(1, spent / budget) : undefined}
          foot={budget ? undefined : "no daily cap set"}
        />
      </section>

      <div className="grid-main">
        <div className="grid">
          <section className="card flush">
            <div className="card-head" style={{ padding: "16px 18px 0" }}>
              <div>
                <h2>Top matches</h2>
                <p className="sub">Best scores you have not decided on yet.</p>
              </div>
              <a href="#/opportunities?view=ready">View all →</a>
            </div>
            <div style={{ padding: "12px 18px 18px" }}>
              {data.top_matches.length === 0 ? (
                <div className="empty">
                  No scored matches yet. Press <strong>Run scan now</strong>, or check{" "}
                  <a href="#/opportunities?view=awaiting">Waiting for AI</a> if the daily AI quota ran out.
                </div>
              ) : (
                <div className="match-list">
                  {data.top_matches.map((o: any) => <MatchCard key={o.id} o={o} />)}
                </div>
              )}
            </div>
          </section>

          <div className="grid-2">
            <section className="card">
              <h2>Discovered per day</h2>
              <p className="sub">Last 14 days</p>
              <BarChart rows={data.daily} />
            </section>
            <section className="card">
              <h2>Last 7 days</h2>
              <p className="sub">From everything found to something worth your time</p>
              <Funnel funnel={funnel} />
            </section>
          </div>
        </div>

        <div className="grid">
          <section className="card">
            <div className="card-head">
              <h2>Live activity</h2>
              {act?.run && <span className="chip accent">Run #{act.run.id}</span>}
            </div>
            {!act ? <div className="loading">Loading…</div> : (
              <div className="feed">
                {act.feed.map((f: any, i: number) => (
                  <div className="feed-row" key={i}>
                    <span className={`feed-dot ${f.tone}`} />
                    <span className="feed-body">
                      <span className="feed-headline">{f.headline}</span>
                      <span className="feed-detail" title={f.detail}>{f.detail}</span>
                    </span>
                    <span className="feed-time">{ago(f.at)}</span>
                  </div>
                ))}
              </div>
            )}
            {act?.eligibility && <Compat e={act.eligibility} />}
            <div style={{ marginTop: 12 }}>
              <a className="btn ghost small" href="#/runs">Open full run logs</a>
            </div>
          </section>

          <section className="card">
            <h2>Where these came from</h2>
            <p className="sub">Sources that produced postings in the last 7 days.</p>
            <table className="legend">
              <tbody>
                {data.sources.slice(0, 7).map((s: any) => (
                  <tr key={s.name}>
                    <td><strong>{s.name}</strong></td>
                    <td>
                      {s.found} found{s.new ? `, ${s.new} new` : ""}
                      {s.blocked ? <> · <span className="chip bad">{s.blocked} blocked</span></> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 12 }}>
              <a className="btn ghost small" href="#/sources">All sources and health →</a>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

function Tile({ label, value, note, foot, href, badge, plain, meter, meterNote }: {
  label: string; value: any; note?: string; foot?: string; href?: string;
  badge?: string; plain?: boolean; meter?: number; meterNote?: string;
}) {
  const body = (
    <>
      <div className="tile-top">
        <span className="tile-label">{label}</span>
        {badge && <span className="chip muted">{badge}</span>}
      </div>
      <span className={`tile-value${plain ? " plain" : ""}`}>{value}</span>
      {note && <span className="tile-note">{note}</span>}
      {meter != null && (
        <>
          <span className="meter"><span style={{ width: `${Math.round(Math.max(0, Math.min(1, meter)) * 100)}%` }} /></span>
          {meterNote && <span className="tile-note" style={{ marginTop: 6 }}>{meterNote}</span>}
        </>
      )}
      {foot && <span className="tile-foot">{foot}</span>}
    </>
  );
  return href ? <a className="tile" href={href}>{body}</a> : <div className="tile">{body}</div>;
}

function MatchCard({ o }: { o: any }) {
  const score = o.match_score;
  const tone = score >= 85 ? "" : score >= 70 ? " accent" : " muted";
  const initial = (o.company_name || o.title || "?").trim().charAt(0).toUpperCase();
  const pay = npr(o);
  const reason = o.filter_reasons?.[0];

  return (
    <a className="match-card" href={`#/opportunities/${o.id}`}>
      <div className="match-top">
        <span className="avatar">{initial}</span>
        <span className="match-head">
          <span className="match-title">{o.title}</span>
          <span className="match-meta">
            <span>{o.company_name || "Company not stated"}</span>
            {o.remote_type !== "unknown" && <><span>·</span><span>{o.remote_type}</span></>}
            {o.location_text && <><span>·</span><span>{o.location_text}</span></>}
            {pay && <span className="salary">{pay}</span>}
          </span>
          <span className="chips" style={{ marginTop: 8 }}>
            <span className="chip accent">{REC[o.recommendation] ?? o.recommendation}</span>
            <span className={`chip ${o.nepal_eligibility === "eligible" ? "good" : o.nepal_eligibility === "not_eligible" ? "bad" : "warn"}`}>
              {ELIG[o.nepal_eligibility] ?? "Eligibility unknown"}
            </span>
            {o.materials_status === "ready" && <span className="chip good">CV drafted</span>}
          </span>
        </span>
        <span className="match-fit">
          <span className={`fit-badge${tone}`}>{score ?? "—"}<span style={{ fontSize: 11, fontWeight: 600 }}>/100</span></span>
          <span className="fit-note">match score</span>
        </span>
      </div>

      {reason && (
        <div className="match-why">
          <div className="match-why-head">
            <strong style={{ fontSize: 12.5 }}>Note</strong>
          </div>
          {reason}
        </div>
      )}

      <div className="match-foot">
        <span>{o.apply_method === "email" ? "✉ Apply by email" : o.apply_method === "ats_form" ? "▤ ATS form" : o.apply_method === "login_required" ? "🔒 Login required" : "↗ Company site"}</span>
        <span>· from {o.source}</span>
        <span className="btn small">Review & apply →</span>
      </div>
    </a>
  );
}

function Compat({ e }: { e: any }) {
  const total = (e.eligible || 0) + (e.likely || 0) + (e.unclear || 0) + (e.not_eligible || 0);
  if (!total) return null;
  const pct = (n: number) => `${((n || 0) / total) * 100}%`;
  const open = Math.round((((e.eligible || 0) + (e.likely || 0)) / total) * 100);
  return (
    <div className="compat">
      <div className="match-why-head">
        <strong style={{ fontSize: 12.5 }}>Nepal eligibility of everything scored</strong>
        <span className="chip good">{open}% open</span>
      </div>
      <div className="compat-bar">
        <span style={{ width: pct(e.eligible), background: "var(--good)" }} />
        <span style={{ width: pct(e.likely), background: "var(--accent)" }} />
        <span style={{ width: pct(e.unclear), background: "var(--warn)" }} />
        <span style={{ width: pct(e.not_eligible), background: "var(--bad)" }} />
      </div>
      <div className="compat-key">
        <span><i className="key-dot" style={{ background: "var(--good)" }} />Open ({e.eligible})</span>
        <span><i className="key-dot" style={{ background: "var(--accent)" }} />Likely ({e.likely})</span>
        <span><i className="key-dot" style={{ background: "var(--warn)" }} />Unclear ({e.unclear})</span>
        <span><i className="key-dot" style={{ background: "var(--bad)" }} />Closed ({e.not_eligible})</span>
      </div>
    </div>
  );
}

function BarChart({ rows }: { rows: { day: string; discovered: number; matches: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.discovered));
  const niceMax = Math.ceil(max / 5) * 5 || 5;
  const w = 560, h = 170, pad = { l: 34, b: 22, t: 8 };
  const band = (w - pad.l) / Math.max(1, rows.length), bar = Math.min(26, band * 0.62);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / niceMax);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Postings discovered per day over the last 14 days">
        {[0, niceMax / 2, niceMax].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w} y1={y(t)} y2={y(t)} className="chart-grid" />
            <text x={pad.l - 6} y={y(t) + 4} className="axis" textAnchor="end">{t}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const x = pad.l + i * band + (band - bar) / 2, top = y(r.discovered), base = y(0);
          const height = Math.max(0, base - top), rr = Math.min(4, height);
          if (height <= 0) return null;
          return (
            <g key={r.day}>
              <path d={`M${x},${base} V${top + rr} Q${x},${top} ${x + rr},${top} H${x + bar - rr} Q${x + bar},${top} ${x + bar},${top + rr} V${base} Z`} className="bar" />
              <title>{`${r.day}: ${r.discovered} discovered, ${r.matches} matches`}</title>
              {(i % 2 === 1 || rows.length < 8) && <text x={x + bar / 2} y={h - 6} className="axis" textAnchor="middle">{r.day.slice(8)}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Funnel({ funnel }: { funnel: any }) {
  const steps: [string, number][] = [
    ["Discovered", funnel.discovered],
    ["Unique postings", funnel.unique_postings],
    ["Passed free filters", funnel.passed_filters],
    ["Scored by AI", funnel.analyzed],
    ["Matches (70+)", funnel.matches],
    ["Strong (85+)", funnel.strong_matches],
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
