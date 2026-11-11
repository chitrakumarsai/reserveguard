// The real ops console at /console after the demo cycle, with Seller C's re-rate waiting for
// approval, so the home page shows what "Try the live agent" opens. Captured with a gutter on
// every side and without the top bar (it names the local, simulated backend).
export function ConsoleFrame() {
  return (
    <section className="section" id="console" aria-labelledby="console-h">
      <div className="wrap">
        <div className="grid">
          <h2 id="console-h" className="span-6">Every reserve decision, with its evidence</h2>
          <p className="span-6 start-7 console-intro">
            Pick a seller to see their rate, what is held and what the wallet holds. Why this rate splits the
            rate into the tier base and each risk signal, with the evidence it cites. Reconciliation checks
            every seller&rsquo;s books on every load. <a className="link" href="#rates">See how rates are set</a>
          </p>
        </div>
        <img
          className="console-shot"
          src="/img/console-overview.webp"
          srcSet="/img/console-overview-1440.webp 1440w, /img/console-overview.webp 2880w"
          sizes="(max-width: 1279px) 100vw, 1280px"
          width={1440}
          height={1245}
          loading="lazy"
          alt="The ReserveGuard ops console: three sellers at 5%, 10% and 15%; Seller C's evidence supports 35%, split into a 15% tier base, +15% for a complaint cluster and +5% for a tracking failure, each citing evidence; an $800.00 charge waits for approval; all seven reconciliation checks passing."
        />
      </div>
    </section>
  );
}
