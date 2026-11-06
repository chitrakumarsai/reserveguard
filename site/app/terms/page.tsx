import type { Metadata } from "next";
import { LegalPage } from "@/components/LegalPage";

export const metadata: Metadata = { title: "Terms · ReserveGuard", alternates: { canonical: "/terms/" } };

export default function Terms() {
  return (
    <LegalPage title="Terms">
      <p>This site describes ReserveGuard. Nothing on it is an offer of payment or financial services.</p>
      <p>Product names and logos are trademarks of their owners.</p>
    </LegalPage>
  );
}
