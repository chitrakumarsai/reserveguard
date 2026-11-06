import { Arrow, Logo } from "./Arrow";
import { MAIN_ACTION, QUESTION_HREF } from "@/lib/site";

// footer.closing: close-type (row 1) and footer-columns (row 2), one screen.
const COLUMNS = [
  { heading: "Product", links: [{ label: "Console", href: "#console" }, { label: "Rates", href: "#rates" }] },
  { heading: "Platform", links: [{ label: "Integrations", href: "#integrations" }] },
  { heading: "Help", links: [{ label: "Questions", href: "#questions" }, { label: "Contact", href: QUESTION_HREF }] },
] as const;

export function Footer() {
  const year = new Date().getFullYear();
  return (
    <footer className="footer closing" id="contact" aria-labelledby="close-h">
      <div className="wrap">
        <div className="closing__cta close--type">
          <div className="section-head">
            <h2 id="close-h">See ReserveGuard run on your sellers</h2>
            <p className="t-small muted">We run a settlement cycle with you on the Airwallex sandbox.</p>
          </div>
          <a className="btn btn--primary btn--xl btn--wide" href={MAIN_ACTION.href}>
            {MAIN_ACTION.label}
            <Arrow />
          </a>
        </div>
        <div className="closing__links">
          <div className="grid">
            <div className="span-3 stack-4">
              <Logo />
              <p className="footer__lede">Seller reserves on Airwallex.</p>
            </div>
            {COLUMNS.map((col) => (
              <div className="span-3 footer__col" key={col.heading}>
                <h3>{col.heading}</h3>
                <ul>
                  {col.links.map((link) => (
                    <li key={link.href}><a href={link.href}>{link.label}</a></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="footer__legal">
            <span>
              &copy; {year} ReserveGuard &nbsp; <a href="/privacy/">Privacy</a> &nbsp; <a href="/terms/">Terms</a>
            </span>
            <span>Product names and logos are trademarks of their owners.</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
