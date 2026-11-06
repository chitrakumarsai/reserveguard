import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";

export const metadata: Metadata = { title: "Privacy · ReserveGuard", alternates: { canonical: "/privacy/" } };

export default function Privacy() {
  return (
    <LegalPage title="Privacy">
      <p>This site sets no cookies and runs no analytics. It loads its fonts from Google Fonts.</p>
      <p>If you email us, we use your address and message only to reply to you.</p>
    </LegalPage>
  );
}
