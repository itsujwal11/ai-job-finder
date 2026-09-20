import { useData } from "./api";

/* eslint-disable @typescript-eslint/no-explicit-any */

const fmtTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "never";

/** Health of a single source over the last 7 days, as one word. */
function verdict(s: any): { tone: string; text: string } {
  if (!s.enabled) return { tone: "muted", text: "Off" };
  if (s.ok === 0 && s.skipped > 0) return { tone: "warn", text: "Skipped" };
  if (s.ok === 0 && (s.failed > 0 || s.blocked > 0)) return { tone: "bad", text: "Failing" };
  if (s.ok === 0) return { tone: "muted", text: "Not run yet" };
  if (s.found === 0) return { tone: "warn", text: "No results" };
  if (s.failed > s.ok) return { tone: "warn", text: "Unreliable" };
  return { tone: "good", text: "Healthy" };
}

export default function Sources() {
  const { data, error, reload } = useData<any>("/sources");

  if (error) return <div className="notice bad">{error}</div>;
  if (!data) return <div className="loading">Loading…</div>;

  const remote = data.sources.filter((s: any) => s.kind === "Remote job API");
  const local = data.sources.filter((s: any) => s.kind === "Nepali job board");
  const problems = data.sources.filter((s: any) => s.enabled && verdict(s).tone !== "good" && verdict(s).text !== "Not run yet");

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Sources</h1>
          <p className="sub">Everywhere jobs are looked for, and what each one actually delivered in the last 7 days.</p>
        </div>
        <button className="btn ghost" onClick={reload}>Refresh</button>
      </header>

      {problems.length > 0 && (
        <div className="notice warn">
          <strong>{problems.length} source{problems.length > 1 ? "s" : ""} not delivering</strong>
          <ul>
            {problems.map((s: any) => (
              <li key={s.name}>
                <strong>{s.label}</strong> — {verdict(s).text.toLowerCase()}
                {s.last_note ? <>: {String(s.last_note).slice(0, 140)}</> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      <section className="tiles">
        <Tile label="Remote job APIs" value={remote.filter((s: any) => s.enabled).length} note={`of ${remote.length} configured`} />
        <Tile label="Nepali boards" value={local.filter((s: any) => s.enabled).length} note={`of ${local.length} configured`} />
        <Tile label="Company ATS boards" value={data.ats_boards?.enabled ?? 0} note={`${data.ats_boards?.total ?? 0} discovered`} />
        <Tile label="Search queries" value={data.search.queries.length} note={`${data.search.max_queries_per_run} run each time`} />
      </section>

      <SourceTable
        title="Remote job APIs and feeds"
        blurb="Read directly through each site's public API. No AI credit is spent on these."
        rows={remote}
      />

      <SourceTable
        title="Nepali job boards"
        blurb="Read from each board's sitemap, then parsed from the schema.org data on the posting page. These are where onsite Kathmandu roles come from. Found = job pages queued."
        rows={local}
      />

      <div className="grid-2">
        <section className="card">
          <h2>Search engines</h2>
          <p className="sub">Used to find postings on career pages no job board lists.</p>
          <table className="legend">
            <tbody>
              {data.search.providers_configured.map((p: string) => {
                const active = data.search.providers_active.includes(p);
                const h = data.search_health.find((x: any) => x.source === p);
                return (
                  <tr key={p}>
                    <td><strong>{p}</strong></td>
                    <td>
                      {active ? (
                        <>
                          <span className="chip good">Active</span>{" "}
                          {h ? `${h.ok} searches, ${h.found} results` : "not run yet"}
                        </>
                      ) : (
                        <><span className="chip muted">No API key</span> set it in <code>.env</code> to enable</>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>

        <section className="card">
          <h2>Job pages fetched</h2>
          <p className="sub">Individual postings opened from search results and Nepali boards.</p>
          <table className="legend">
            <tbody>
              <tr><td><span className="chip good">Parsed</span></td><td>{data.pages?.ok ?? 0}</td></tr>
              <tr><td><span className="chip warn">Skipped</span></td><td>{data.pages?.skipped ?? 0} — not a single job posting, or no structured data</td></tr>
              <tr><td><span className="chip bad">Blocked</span></td><td>{data.pages?.blocked ?? 0} — site refused automated access. Expected, never worked around.</td></tr>
              <tr><td><span className="chip bad">Failed</span></td><td>{data.pages?.failed ?? 0} — network error or bad response</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <section className="card">
        <div className="card-head"><h2>Search queries in rotation</h2><span className="sub">{data.search.max_queries_per_run} of {data.search.queries.length} run each time</span></div>
        <ul className="query-list">
          {data.search.queries.map((q: string) => <li key={q}><code>{q}</code></li>)}
        </ul>
        <p className="sub">Edit these under <code>search.queries</code> in <code>config/config.yaml</code>. Changes apply on the next run.</p>
      </section>
    </>
  );
}

function SourceTable({ title, blurb, rows }: { title: string; blurb: string; rows: any[] }) {
  return (
    <section className="card flush">
      <div className="card-head" style={{ padding: "16px 18px 0" }}>
        <div><h2>{title}</h2><p className="sub">{blurb}</p></div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th><th>Health</th><th className="num">Fetches</th>
              <th className="num">Found</th><th className="num">New</th><th className="num">Stored</th><th>Last run</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s: any) => {
              const v = verdict(s);
              return (
                <tr key={s.name}>
                  <td>
                    <div className="cell-title">{s.label}</div>
                    {s.last_note && <div className="cell-sub">{String(s.last_note).slice(0, 90)}</div>}
                  </td>
                  <td><span className={`chip ${v.tone}`}>{v.text}</span></td>
                  <td className="num">{s.ok}{s.failed + s.blocked > 0 ? <span className="dim"> / {s.failed + s.blocked} bad</span> : null}</td>
                  <td className="num">{s.found}</td>
                  <td className="num">{s.new}</td>
                  <td className="num">{s.stored_total}</td>
                  <td className="nowrap">{fmtTime(s.last_run)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Tile({ label, value, note }: { label: string; value: any; note?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}
