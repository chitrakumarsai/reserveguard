// One source for the facts and actions that repeat across the page (copy rule: one fact, one wording).
export const SITE = {
  name: "ReserveGuard",
  url: "https://reserveguard.example",
  title: "ReserveGuard: seller reserves for Airwallex marketplaces",
  description:
    "ReserveGuard holds a reserve for each marketplace seller, pays the rest to their Airwallex connected account and re-rates one seller when new risk evidence arrives.",
} as const;

// The demo is the real ops console, served by the same API server at /console.
// shortcut: on a static host without the API this link 404s; point it at the hosted console when there is one.
export const MAIN_ACTION = {
  label: "Request a demo",
  href: "/console",
} as const;

export const QUESTION_HREF = "mailto:hello@reserveguard.example?subject=Question%20about%20ReserveGuard";

export const NAV = [
  { label: "Console", href: "#console" },
  { label: "Rates", href: "#rates" },
  { label: "Integrations", href: "#integrations" },
  { label: "Questions", href: "#questions" },
] as const;

export const RATE_CAP = "40%";
