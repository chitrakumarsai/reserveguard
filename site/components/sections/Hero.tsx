import { Arrow } from "../Arrow";
import { MAIN_ACTION, RATE_CAP } from "@/lib/site";

// Dealt hero: split. The picture is the real ops console waiting on Seller C's re-rate.
export function Hero() {
  return (
    <section className="hero hero--split" aria-labelledby="hero-h">
      <div className="wrap">
        <div className="hero-text grid hero__grid">
          <div className="hero__text span-6">
            <p className="hero__kicker">
              Reserve rates capped at {RATE_CAP} per seller
            </p>
            <h1 id="hero-h">Seller reserves for Airwallex marketplaces</h1>
            <p className="hero__lead">
              ReserveGuard holds a reserve for each seller, pays the rest to their connected account
              and re-rates one seller when new risk evidence arrives.
            </p>
            <div className="btn-row">
              <a className="btn btn--primary" href={MAIN_ACTION.href}>
                {MAIN_ACTION.label}
                <Arrow />
              </a>
              <a className="btn btn--secondary" href="#console">
                See the console
              </a>
            </div>
          </div>
          <div className="hero__media span-6 start-7">
            <img
              src="/img/console-approval.webp"
              srcSet="/img/console-approval-640.webp 640w, /img/console-approval.webp 1248w"
              sizes="(max-width: 1023px) 100vw, 50vw"
              width={1248}
              height={1822}
              alt="The ReserveGuard ops console: Seller C's rate rises from 15% to 35% on two cited risk signals, and an $800.00 charge waits for approval."
              style={{ ["--ly-pos" as string]: "50% 100%" }}
              fetchPriority="high"
              data-source="client"
              data-subject="ReserveGuard ops console"
            />
          </div>
        </div>
      </div>
    </section>
  );
}
