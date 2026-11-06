import { QUESTION_HREF, RATE_CAP } from "@/lib/site";

const FAQ = [
  {
    q: "Does Claude decide how much to hold?",
    a: "No. Claude names the risk signals and cites the evidence. Fixed weights in code set the rate, and every amount is integer cents.",
  },
  {
    q: "What happens when a seller's wallet is short?",
    a: "ReserveGuard charges what the wallet holds and records the rest as debt. It is collected from that seller's next payouts.",
  },
  {
    q: "Can one seller's funds cover another's?",
    a: "No. Each seller has its own connected account, and the checks after every money move confirm no balance was mixed.",
  },
  {
    q: "When does a person approve a change?",
    a: "Changes above the auto-approval thresholds wait in the ops console. Nothing moves until an operator approves and confirms.",
  },
  {
    q: "Is there a limit on the reserve rate?",
    a: `Yes. Rates are capped at ${RATE_CAP}, whatever the evidence says.`,
  },
  {
    q: "Does it run on Airwallex today?",
    a: "Yes, on the Airwallex sandbox, with connected accounts, transfers and charges. Its last sandbox run passed all 33 checks.",
  },
] as const;

export function Questions() {
  return (
    <section className="section" id="questions" aria-labelledby="questions-h">
      <div className="wrap">
        <div className="grid grid--top">
          <div className="span-5 stack-4">
            <h2 id="questions-h">Questions</h2>
            <p className="muted">What marketplace finance and risk teams ask first.</p>
            <a className="link" href={QUESTION_HREF}>
              Email the team
            </a>
          </div>
          <div className="faq span-6 start-7">
            {FAQ.map((item) => (
              <div className="faq__item" key={item.q}>
                <h3>{item.q}</h3>
                <p>{item.a}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
