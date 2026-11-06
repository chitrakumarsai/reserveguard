import { Logo } from "./Arrow";
import { MAIN_ACTION, NAV } from "@/lib/site";

// nav-centre (dealt nav-cards needs photographs the page lacks, so it was dealt again).
export function Nav() {
  return (
    <header className="nav nav--centre">
      <div className="wrap nav__bar">
        <a className="skip-link" href="#console">
          Skip to content
        </a>
        <Logo />
        <nav className="nav__links" aria-label="Main">
          {NAV.map((item) => (
            <div className="nav__item" key={item.href}>
              <a className="nav__link" href={item.href}>
                {item.label}
              </a>
            </div>
          ))}
        </nav>
        <div className="nav__actions">
          <a className="btn btn--primary" href={MAIN_ACTION.href}>
            {MAIN_ACTION.label}
          </a>
        </div>
        <button className="nav__toggle" type="button" aria-expanded="false" aria-label="Menu">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path d="M2 5h14M2 13h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </div>
    </header>
  );
}
