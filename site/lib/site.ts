// The site's public address, read at build time: the deploy sets SITE_URL to the URL AWS assigns
// (e.g. https://abc123.us-east-1.awsapprunner.com), or a custom domain later. A bad value fails the build.
const SITE_URL = new URL(process.env.SITE_URL ?? "http://localhost:8000").origin;

// One source for the facts and actions that repeat across the page (copy rule: one fact, one wording).
export const SITE = {
  name: "ReserveGuard",
  url: SITE_URL,
  title: "ReserveGuard: seller reserves for Airwallex marketplaces",
  description:
    "ReserveGuard holds a reserve for each marketplace seller, pays the rest to their Airwallex connected account and re-rates one seller when new risk evidence arrives.",
} as const;

// The demo is the real ops console, served by the same API server at /console.
// shortcut: on a static host without the API this link 404s; point it at the hosted console when there is one.
export const MAIN_ACTION = {
  label: "Try the live agent",
  href: "/console",
} as const;

export const QUESTION_HREF = "mailto:chitrakumarsai@gmail.com?subject=Question%20about%20ReserveGuard";

export const NAV = [
  { label: "Console", href: "#console" },
  { label: "Rates", href: "#rates" },
  { label: "Integrations", href: "#integrations" },
  { label: "Questions", href: "#questions" },
] as const;

export const RATE_CAP = "40%";
