import type { ReactNode } from "react";

// features-2x2 on the surface band (rhythm: surface-pairs), as two screens (.g2).
// Each panel holds a kit fragment rewritten to ReserveGuard's own subject.

const CHECK = (
  <span className="ui-check__box">
    <svg viewBox="0 0 16 16"><path d="m4.5 8.2 2.3 2.3 4.7-5" /></svg>
  </span>
);

const EVIDENCE = [
  "ev-c-001 · Item not received",
  "ev-c-002 · Item not received",
  "ev-c-003 · Item not received",
  "ev-c-004 · Item not received",
  "ev-c-005 · Delivery failed, returned",
] as const;

// Bars: Seller C's reserve rate per review, 15% for the first reviews, then 35% after the evidence.
const BARS = Array.from({ length: 14 }, (_, i) => {
  const height = i < 11 ? 42 : 98;
  return { x: (i * 29.14).toFixed(2), y: (120 - height).toFixed(2), height };
});

const PAYOUT_LANES = {
  layout: "lanes",
  alt: "One settlement cycle goes to ReserveGuard, which pays Seller A, Seller B and Seller C to their own connected accounts.",
  input: { label: "Settlement cycle", icon: "ui:landmark", sub: "$10,000.00 gross", kindCompact: "pill" },
  core: { label: "ReserveGuard", icon: "ui:shield-check" },
  lanes: [
    { label: "Seller A", icon: "ui:wallet", done: true, sub: "$2,550.00 paid", kindCompact: "pill" },
    { label: "Seller B", icon: "ui:wallet", done: true, sub: "$2,400.00 paid", kindCompact: "pill" },
    { label: "Seller C", icon: "ui:wallet", done: true, sub: "$3,000.00 paid", kindCompact: "pill" },
  ],
};

function Card({ title, body, children }: { title: string; body: string; children: ReactNode }) {
  return (
    <article className="card card--row card--open span-6 m-nolift">
      <h3>{title}</h3>
      <p>{body}</p>
      <div className="panel">{children}</div>
    </article>
  );
}

function RateStat() {
  return (
    <div className="stage stage--fade-b">
      <div className="at-rise fit to-edge" style={{ ["--fit-max" as string]: "400px", ["--ax" as string]: "max(24px, 50% - 200px)", ["--ay" as string]: "56px", ["--ay-sm" as string]: "56px" }} data-ui-loop>
        <div className="ui ui-panel ui-stat" data-ui="stat" data-from="15" data-to="35" data-suffix="%" data-ui-opts='{"fill":true}'>
          <div className="ui-stat__head">
            <div className="ui-stat__top">
              <span className="ui-stat__label">Seller C reserve rate</span>
              <span className="ui-stat__range">Last 14 reviews</span>
            </div>
            <div className="ui-stat__line">
              <span className="ui-stat__num" data-stat-num>35%</span>
              <span className="ui-chip ui-chip--plain ui-stat__delta">20 pts</span>
              <span className="ui-stat__sub">after two risk signals</span>
            </div>
          </div>
          <div className="ui-stat__chart">
            <svg className="ui-stat__svg" viewBox="0 0 400 120" preserveAspectRatio="none" aria-hidden="true">
              <path className="ui-stat__grid" d="M0 30.5H400M0 60.5H400M0 90.5H400" />
              <path className="ui-stat__cover" fillRule="evenodd" d="" />
              <g className="ui-stat__bars">
                {BARS.map((bar) => (
                  <rect key={bar.x} rx="3" x={bar.x} y={bar.y} width="21.14" height={bar.height} />
                ))}
              </g>
            </svg>
            <div className="ui-stat__axis" aria-hidden="true"><span>Onboarded</span><span>Week 2</span><span>Today</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

function EvidenceList() {
  return (
    <div className="stage stage--fade-b">
      <div className="at-rise fit" style={{ ["--fit-max" as string]: "400px", ["--ax" as string]: "max(24px, 50% - 200px)", ["--ay" as string]: "calc(100% - 220px)", ["--ay-sm" as string]: "calc(100% - 220px)" }}>
        <div className="ui ui-panel ui-checks" data-ui="checklist">
          <div className="ui-checks__head">
            <div className="ui-title">Evidence for Seller C</div>
            <span className="ui-spacer" />
            <div className="ui-meta" data-checks-count>5 of 5 cited</div>
          </div>
          <div className="ui-checks__bar"><i data-checks-bar style={{ ["--k" as string]: 1 }} /></div>
          <ul className="ui-checks__list">
            {EVIDENCE.map((item) => (
              <li className="ui-check is-done" key={item}>{CHECK}<span className="ui-check__label">{item}</span></li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ShortfallNotes() {
  return (
    <div className="stage stage--fade-b">
      <div className="at-rise fit" style={{ ["--fit-max" as string]: "420px", ["--ax" as string]: "max(24px, 50% - 210px)", ["--ay" as string]: "calc(100% - 200px)", ["--ay-sm" as string]: "calc(100% - 200px)" }}>
        <div className="ui ui-notify" data-ui="notify">
          <ul className="ui-notify__list" data-notify-list>
            <li className="ui-note">
              <span className="ui-agent ui-note__av" aria-hidden="true" />
              <span className="ui-note__text"><span className="ui-note__title">$500.00 charged from Seller C<time>now</time></span><span className="ui-note__body">The wallet held $500.00 of $800.00</span></span>
            </li>
            <li className="ui-note">
              <span className="ui-avatar ui-note__av" data-tone="2">C</span>
              <span className="ui-note__text"><span className="ui-note__title">$300.00 recorded as debt<time>now</time></span><span className="ui-note__body">Collected from Seller C&rsquo;s next payouts</span></span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}

export function Features() {
  return (
    <section className="section g2 ground-surface" id="rates" aria-labelledby="rates-h">
      <div className="wrap">
        <div className="group">
          <div className="section-head">
            <h2 id="rates-h">
              Reserves set by rules, one seller at a time. <span className="muted">Claude reads the evidence.</span>
            </h2>
          </div>
          <div className="grid">
            <Card title="Holds a reserve per seller" body="Each tier has its own base rate: 5% trusted, 10% standard and 15% new. It is held back from every payout.">
              <RateStat />
            </Card>
            <Card title="Re-rates on new evidence" body="Claude names the risk signals in complaints and carrier events and cites each item. Fixed weights set the rate.">
              <EvidenceList />
            </Card>
          </div>
        </div>
        <div className="grid">
          <Card title="Recovers a shortfall" body="When a wallet cannot cover a higher reserve, ReserveGuard charges what is there and collects the rest later.">
            <ShortfallNotes />
          </Card>
          <Card title="Pays each seller separately" body="Net proceeds go to each seller's own Airwallex connected account. One seller's funds never cover another's.">
            <div className="dg" data-dg-still data-diagram={JSON.stringify(PAYOUT_LANES)} />
          </Card>
        </div>
      </div>
    </section>
  );
}
