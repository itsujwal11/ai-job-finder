import { useData } from "./api";

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Plain-language explanation of the whole system, and what to do with it day to day. */
export default function Guide() {
  const { data } = useData<any>("/overview");
  const status = data?.status;
  const kpis = data?.kpis;
  const hasRun = (data?.recent_runs?.length ?? 0) > 0;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>How this works</h1>
          <p className="sub">What the system does, and what you do. Read once, then use the Overview.</p>
        </div>
      </header>

      {status && status.warnings.length > 0 && (
        <div className="notice warn">
          <strong>Finish setting up</strong>
          <ul>{status.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      <section className="card">
        <h2>The idea in one line</h2>
        <p className="guide-lead">
          Every day it collects job postings from dozens of sources, throws away the ones that are
          clearly wrong for you, scores the rest against your CV with an AI model, and hands you a
          short list. <strong>It never applies for you</strong> &mdash; you always decide.
        </p>
        <ol className="steps">
          <li>
            <span className="step-n">1</span>
            <div>
              <strong>Collect</strong>
              <p>
                Remote job APIs, company career boards, Ojiiz and the Nepali boards (merojob,
                froxjob, jobaxle) are read through their public APIs and sitemaps.
              </p>
            </div>
          </li>
          <li>
            <span className="step-n">2</span>
            <div>
              <strong>Filter &mdash; free, no AI</strong>
              <p>
                Out go senior roles, jobs closed to Nepal, stale postings, scam-shaped listings,
                duplicates and roles unrelated to your target titles. This is where most postings
                stop, and it costs nothing.
              </p>
            </div>
          </li>
          <li>
            <span className="step-n">3</span>
            <div>
              <strong>Score with AI</strong>
              <p>
                What survives is ranked by a free rule-based pre-score, and the best of them are
                read by the AI model and scored <strong>0&ndash;100</strong> against your CV. Only
                a limited number per run, so the pre-score decides who is looked at first.
              </p>
            </div>
          </li>
          <li>
            <span className="step-n">4</span>
            <div>
              <strong>You decide</strong>
              <p>
                Strong matches appear under <em>Ready to apply</em> and <em>Needs approval</em>.
                For 85+ matches a tailored CV and cover letter are drafted for you to check, edit
                and send yourself.
              </p>
            </div>
          </li>
        </ol>
      </section>

      <section className="card">
        <h2>Your routine</h2>
        <ol className="routine">
          <li>
            Press <strong>Run now</strong> on the Overview, or let the 06:00 schedule do it.
          </li>
          <li>
            Open <a href="#/opportunities?view=needs_approval">Needs approval</a> and{" "}
            <a href="#/opportunities?view=ready">Ready to apply</a>.
          </li>
          <li>Open a job. Read the score breakdown and the evidence the AI quoted from the posting.</li>
          <li>
            Press <strong>Approve</strong>, <strong>Reject</strong> or <strong>Dismiss</strong>.
          </li>
          <li>
            Apply on the hiring company&apos;s own site, then press <strong>I applied</strong> so the
            same role is never suggested to you twice.
          </li>
        </ol>
        {!hasRun && (
          <div className="notice info">
            No runs yet &mdash; press <strong>Run now</strong> on the Overview to start the first
            one. The first run takes a while because every source is read from scratch.
          </div>
        )}
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>What the score means</h2>
          <table className="legend">
            <tbody>
              <tr>
                <td><span className="score good">85</span>+</td>
                <td><strong>Top match</strong> &mdash; a tailored CV and cover letter are drafted automatically.</td>
              </tr>
              <tr>
                <td><span className="score accent">70</span>&ndash;84</td>
                <td><strong>Needs your approval</strong> &mdash; worth a look, but check it yourself.</td>
              </tr>
              <tr>
                <td><span className="score muted">&lt;70</span></td>
                <td>Ignored. Still visible under <em>Analysed</em> if you want to see why.</td>
              </tr>
            </tbody>
          </table>
          <p className="sub">
            The score weighs skills, experience fit, role fit, Nepal eligibility, pay and growth
            value. Senior roles, jobs closed to Nepal, scam-shaped listings and duplicates are
            blocked <em>at any score</em>.
          </p>
        </section>

        <section className="card">
          <h2>What the statuses mean</h2>
          <table className="legend">
            <tbody>
              <tr>
                <td><span className="chip neutral">Ready to apply</span></td>
                <td>Passed everything. Go apply.</td>
              </tr>
              <tr>
                <td><span className="chip neutral">Needs approval</span></td>
                <td>Good score, wants your judgement.</td>
              </tr>
              <tr>
                <td><span className="chip neutral">Awaiting AI</span></td>
                <td>
                  Queued for scoring. Usually the AI quota ran out &mdash; it is picked up on the
                  next run, best first. Nothing is lost.
                </td>
              </tr>
              <tr>
                <td><span className="chip bad">Blocked</span></td>
                <td>Ruled out for a concrete reason, shown on the job page.</td>
              </tr>
              <tr>
                <td><span className="chip neutral">Filtered out</span></td>
                <td>Did not pass the free filters. Never cost an AI call.</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <section className="card">
        <h2>Where the jobs come from</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Source</th><th>How it is read</th><th>AI cost</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Remotive, RemoteOK, Jobicy, Himalayas, We Work Remotely, Working Nomads</td>
                <td>Public job APIs and RSS feeds</td>
                <td>None</td>
              </tr>
              <tr>
                <td>Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee</td>
                <td>Official public board APIs. Any board seen anywhere is added automatically.</td>
                <td>None</td>
              </tr>
              <tr>
                <td>Ojiiz</td>
                <td>Public listing API. Company names sit behind their paid unlock, so open the link to see the rest.</td>
                <td>None</td>
              </tr>
              <tr>
                <td>merojob, froxjob, jobaxle</td>
                <td>Board sitemap, then the schema.org data every detail page carries</td>
                <td>None</td>
              </tr>
              <tr>
                <td>Hacker News <em>Who is hiring</em></td>
                <td>Public API</td>
                <td>None</td>
              </tr>
              <tr>
                <td>Brave / Tavily / Google search</td>
                <td>Official search APIs, when a key is set</td>
                <td>Only if a page has no structured data</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="sub">
          Nepali boards mostly advertise onsite Kathmandu roles on PHP, .NET, WordPress and Java.
          Those stacks are filtered out of worldwide-remote results, but deliberately allowed
          through for Nepal, because that is what the local market actually runs on.
        </p>
      </section>

      <section className="card">
        <h2>Limits worth knowing</h2>
        <ul className="bullets">
          <li>
            <strong>Sites are never tricked.</strong> Logins, CAPTCHAs and anti-bot protection are
            never bypassed, and robots.txt is respected. A site that refuses shows as
            <em> blocked</em>, which is expected, not a fault.
          </li>
          <li>
            <strong>AI calls are capped per run and per day.</strong>{" "}
            {status?.ai?.model ? (
              <>
                Currently <code>{status.ai.model}</code> with a $
                {Number(status.ai.daily_budget_usd).toFixed(2)}/day budget.{" "}
              </>
            ) : null}
            Run <code>python -m app.cli quota</code> to see what is left today.
          </li>
          <li>
            <strong>Nothing is ever submitted.</strong> Automatic applying is off, and this build
            ships no submitter at all.
          </li>
          <li>
            <strong>Facts come only from your CV.</strong> Generated CVs and cover letters are
            fact-checked against <code>profile/cv.md</code>; send only the ones marked
            <em> Fact-checked</em>.
          </li>
        </ul>
      </section>

      <section className="card">
        <h2>If nothing is showing up</h2>
        <table className="legend">
          <tbody>
            <tr>
              <td><strong>No matches at all</strong></td>
              <td>
                Check <a href="#/opportunities?view=awaiting">Awaiting AI</a>. If it is full, the
                AI quota ran out &mdash; see the model note above.
              </td>
            </tr>
            <tr>
              <td><strong>Everything is filtered out</strong></td>
              <td>
                Your target titles may be too narrow. Edit{" "}
                <code>relevance.include_title_terms</code> in <code>config/config.yaml</code>.
              </td>
            </tr>
            <tr>
              <td><strong>Sources failing</strong></td>
              <td>
                Open <a href="#/runs">Runs &amp; logs</a> &mdash; every fetch, error and AI call is
                recorded there.
              </td>
            </tr>
            <tr>
              <td><strong>Scores look wrong</strong></td>
              <td>
                Update <code>profile/cv.md</code> and <code>profile/preferences.yaml</code>, then
                run again.
              </td>
            </tr>
          </tbody>
        </table>
        {kpis && (
          <p className="sub">
            Right now: {kpis.total} postings stored, {kpis.awaiting_analysis} waiting for AI,{" "}
            {kpis.ready_to_apply} ready to apply, {kpis.needs_approval} awaiting your approval.
          </p>
        )}
      </section>
    </>
  );
}
