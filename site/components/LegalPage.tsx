import type { ReactNode } from "react";

export function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="legal">
      <div className="wrap measure stack-4">
        <p><a className="link" href="/">ReserveGuard home</a></p>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}
