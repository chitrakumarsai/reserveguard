// split-text-left: the words on span-6, the hub diagram of the real tools on span-6 start-7 (the page's split).
// Airwallex has no logo in the kit's permitted icon sets, so it is named in type with a plain glyph.
const STACK_HUB = {
  layout: "hub",
  alt: "ReserveGuard connects to Airwallex for money movement, Claude for evidence, and runs on Python and FastAPI.",
  center: { label: "ReserveGuard", icon: "ui:shield-check" },
  spokes: [
    { label: "Airwallex", icon: "ui:landmark" },
    { label: "Claude", icon: "claude" },
    { label: "Python", icon: "python" },
    { label: "FastAPI", icon: "fastapi" },
  ],
};

const FACTS = [
  { name: "Money", value: "Airwallex connected accounts, transfers and charges" },
  { name: "Evidence", value: "Claude reads it and drafts the memo; it holds no API keys" },
  { name: "Amounts", value: "Integer cents and basis points, set in Python" },
  { name: "Retries", value: "Each money call has its own idempotency key" },
] as const;

export function Integrations() {
  return (
    <section className="section" id="integrations" aria-labelledby="integrations-h">
      <div className="wrap">
        <div className="grid grid--top fill">
          <div className="span-6 stack-6">
            <h2 id="integrations-h">Built on Airwallex and Claude</h2>
            <p className="measure">
              Every payout, hold and charge goes through Airwallex. Claude only reads the evidence and writes the
              memo, so the money logic stays plain code you can test.
            </p>
            <ul className="rows">
              {FACTS.map((fact) => (
                <li key={fact.name}>
                  <span className="rows__name">{fact.name}</span>
                  <p>{fact.value}</p>
                </li>
              ))}
            </ul>
          </div>
          <div className="frame frame--lit span-6 start-7" data-visual>
            <div className="dg" data-dg-still data-diagram={JSON.stringify(STACK_HUB)} />
          </div>
        </div>
      </div>
    </section>
  );
}
