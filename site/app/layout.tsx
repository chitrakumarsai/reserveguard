import type { Metadata, Viewport } from "next";
import Script from "next/script";
import type { ReactNode } from "react";
import { SITE } from "@/lib/site";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE.url),
  title: SITE.title,
  description: SITE.description,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "/",
    siteName: SITE.name,
    title: SITE.title,
    description: SITE.description,
    images: [{ url: "/img/console-approval.webp", width: 1248, height: 1822, alt: "The ReserveGuard ops console" }],
  },
  twitter: { card: "summary_large_image", title: SITE.title, description: SITE.description },
  // The kit's checks read these to judge the page as software (taste-skill SKILL.md section 1).
  other: { "site-kind": "software", "page-type": "software" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#f7f9f8",
};

// The kit's stylesheets, in the order starter.html gives; the :root tokens follow them.
const KIT_CSS = [
  "system.css",
  "ui/cards.css",
  "motion/motion.css",
  "layout/layout.css",
  "icons/icon.css",
  "ui/base.css",
  "ui/stat/fragment.css",
  "ui/checklist/fragment.css",
  "ui/notify/fragment.css",
  "diagrams/diagram.css",
] as const;

const KIT_JS = ["system.js", "motion/motion.js", "layout/layout.js", "diagrams/mount.js"] as const;

// As `start.mjs css --page software --palette teal-on-paper --type geist --radius sharp` printed it.
const TOKENS = `:root {
  --p-bg: #f7f9f8; --p-surface: #ebf0ee; --p-ink: #06525c; --p-muted: #2f737d;
  --p-accent: #06525c; --p-accent-ink: #f7f9f8; --p-action: #06525c; --p-action-ink: #f7f9f8;
  --p-inv-bg: #06525c; --p-inv-surface: #205d66; --p-inv-ink: #f7f9f8; --p-inv-muted: #c1d0d2;
  --p-inv-accent: #f7f9f8; --p-inv-accent-ink: #06525c; --p-inv-action: #f7f9f8; --p-inv-action-ink: #06525c;
  --font-display: "Geist", system-ui, sans-serif;
  --font-text: "Geist", system-ui, sans-serif;
  --font-mono: "Geist Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  --display-weight: 500;
  --display-tracking: -0.035em;
  --display-tracking-xl: -0.045em;
}`;

const JSON_LD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: SITE.name,
  applicationCategory: "FinanceApplication",
  operatingSystem: "Web",
  description: SITE.description,
  url: SITE.url,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-type="software" data-radius="sharp" data-ground="sage" data-buttons="pill" data-motion="">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@400..700&family=Geist+Mono:wght@400..500&display=swap"
        />
        {KIT_CSS.map((href) => (
          <link key={href} rel="stylesheet" href={`/assets/${href}`} />
        ))}
        <style dangerouslySetInnerHTML={{ __html: TOKENS }} />
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(JSON_LD) }} />
      </head>
      <body>
        {/* type: software (fintech)
            deal: hero split, nav nav-centre (nav-cards dealt, no photographs), close close-type,
            footer footer-columns, rhythm surface-pairs, buttons pill, mark ring, ground sage
            effect and moments: none (kept to the lit diagram frame) */}
        {children}
        {/* After hydration, so the kit's DOM changes never meet React's hydration pass. */}
        {KIT_JS.map((src) => (
          <Script key={src} src={`/assets/${src}`} type="module" crossOrigin="anonymous" strategy="afterInteractive" />
        ))}
      </body>
    </html>
  );
}
