// app-frame: the decision log (kit `window`) beside the open decision memo (kit `doc`).
// Every row and figure is from the demo settlement cycle in scripts/demo.py.
type Row = { initials: string; tone: number; who: string; sub: string; status: string; state?: "is-you" | "is-done"; age: string };

const ROWS: readonly Row[] = [
  { initials: "C", tone: 2, who: "Seller C", sub: "Re-rate 15.00% to 35.00%", status: "Needs you", state: "is-you", age: "1m" },
  { initials: "C", tone: 2, who: "Seller C", sub: "Evidence read · 5 items", status: "Read", state: "is-done", age: "1m" },
  { initials: "P", tone: 4, who: "Platform", sub: "7 reconciliation checks", status: "Passed", state: "is-done", age: "6m" },
  { initials: "A", tone: 1, who: "Seller A", sub: "Payout $2,550.00", status: "Paid", state: "is-done", age: "6m" },
  { initials: "B", tone: 3, who: "Seller B", sub: "Payout $2,400.00", status: "Paid", state: "is-done", age: "6m" },
  { initials: "C", tone: 2, who: "Seller C", sub: "Payout $3,000.00", status: "Paid", state: "is-done", age: "6m" },
  { initials: "A", tone: 1, who: "Seller A", sub: "Reserve hold $150.00 · 5%", status: "Held", state: "is-done", age: "6m" },
  { initials: "B", tone: 3, who: "Seller B", sub: "Reserve hold $300.00 · 10%", status: "Held", state: "is-done", age: "6m" },
  { initials: "C", tone: 2, who: "Seller C", sub: "Reserve hold $600.00 · 15%", status: "Held", state: "is-done", age: "6m" },
  { initials: "P", tone: 4, who: "Platform", sub: "Fees $1,000.00 · 10%", status: "Kept", state: "is-done", age: "6m" },
];

function DecisionLog() {
  return (
    <div className="ui ui-panel ui-win" data-ui="window" data-open="Waiting" data-work="Moving" data-done="Done">
      <div className="ui-win__bar">
        <span className="ui-dots" aria-hidden="true"><i /><i /><i /></span>
        <span className="ui-win__crumb">ReserveGuard <i>/</i> <b>Activity</b></span>
        <span className="ui-spacer" />
        <span className="ui-win__search" aria-hidden="true">
          <svg className="ui-icon" viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></svg>
          <span className="ui-win__ph">Search</span><span className="ui-kbd">/</span>
        </span>
      </div>
      <div className="ui-win__body">
        <nav className="ui-win__side" aria-hidden="true">
          <span className="ui-win__nav is-on">Activity<em>10</em></span>
          <span className="ui-win__nav">Approvals<em>1</em></span>
          <span className="ui-win__nav">Sellers<em>3</em></span>
          <span className="ui-win__nav">Checks<em>7</em></span>
          <span className="ui-win__label">Tiers</span>
          <span className="ui-win__nav"><span className="ui-win__hash">#</span>Trusted</span>
          <span className="ui-win__nav"><span className="ui-win__hash">#</span>Standard</span>
          <span className="ui-win__nav"><span className="ui-win__hash">#</span>New</span>
        </nav>
        <div className="ui-win__main">
          <div className="ui-win__cols" aria-hidden="true"><span>Seller</span><span>Entry</span><span>Status</span><span>Age</span></div>
          <ul className="ui-win__rows">
            {ROWS.map((row) => (
              <li className="ui-win__row" data-row key={`${row.who}-${row.sub}`}>
                <span className="ui-avatar" data-tone={row.tone}>{row.initials}</span>
                <span className="ui-win__who">{row.who}</span>
                <span className="ui-win__sub">{row.sub}</span>
                <span className={`ui-win__st ${row.state ?? ""}`} data-st>{row.status}</span>
                <span className="ui-win__age">{row.age}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function DecisionMemo() {
  return (
    <div className="ui ui-doc" data-ui="doc">
      <article className="ui-doc__page">
        <div className="ui-doc__label">Decision memo, Seller C</div>
        <div className="ui-doc__title">Reserve rate 15.00% to 35.00%</div>
        <div className="ui-doc__text">
          Five new evidence items were read. <mark className="ui-doc__mark is-on">Complaint cluster, +15.00%</mark>,
          cited from ev-c-001 to ev-c-004.
        </div>
        <div className="ui-doc__text">
          <mark className="ui-doc__mark" data-kind="Signal" data-msg="Carrier event: delivery failed, returned to sender." data-where="Evidence ev-c-005">
            Tracking failure, +5.00%
          </mark>
          , cited from ev-c-005, on top of the new tier&rsquo;s base rate of 15.00%.
        </div>
        <div className="ui-doc__text">
          The target reserve moves from $600.00 to $1,400.00. Seller C holds $600.00, so $800.00 is charged from
          their wallet.
        </div>
        <div className="ui-doc__text">
          <mark className="ui-doc__mark" data-kind="Approval" data-msg="Above the auto-approval thresholds: an operator approves, then confirms." data-where="Rate rules, cap 40%">
            Waits for ops approval
          </mark>
          . Nothing has moved yet.
        </div>
        <div className="ui-doc__text">Weights come from fixed rules. Claude named the signals and cited the evidence; it set no amount.</div>
        <div className="ui-doc__text">If the wallet is short, the rest is recorded as debt and collected from Seller C&rsquo;s next payouts.</div>
      </article>
      <aside className="ui-doc__note ui-panel is-on" aria-live="polite">
        <div className="ui-doc__kind"><span className="ui-doc__dot" /><span data-doc-kind>Signal</span></div>
        <div className="ui-doc__msg" data-doc-msg>Four complaints: items not received.</div>
        <div className="ui-doc__where" data-doc-where>Evidence ev-c-001 to ev-c-004</div>
      </aside>
    </div>
  );
}

export function ConsoleFrame() {
  return (
    <section className="section" id="console" aria-labelledby="console-h">
      <div className="wrap">
        <div className="grid">
          <h2 id="console-h" className="span-5">Every reserve decision, with its evidence</h2>
          <p className="span-6 start-7 muted console-intro">
            The activity log lists each payout, hold and re-rate by seller. Open one to read its memo: the new
            rate, the evidence it cites and whether ops must approve. <a className="link" href="#rates">See how rates are set</a>
          </p>
        </div>
        <div className="ui app fill" role="img" aria-label="The ReserveGuard activity log beside the open decision memo for Seller C's re-rate" data-ui-pause-host>
          <div className="app__pane app__pane--list"><DecisionLog /></div>
          <div className="app__pane app__pane--open" data-ui-loop><DecisionMemo /></div>
        </div>
      </div>
    </section>
  );
}
