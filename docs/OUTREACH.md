# Outreach kit: get 2–3 "yes, I'd test this" before the demo

Goal for today: a few people who own this problem agree **in writing** to try Lethe on a non-production pipeline
and give feedback, and say whether we may name their role or company on stage.

## Target companies, ranked (sources in docs/RESEARCH.md)
**Fastest today:** sponsor and mentor engineers at the venue. Snowflake (customers fine-tune on their own data in
Cortex), Google (Vertex AI tuning), Sentry. Show the demo for 60 seconds, then ask for the one-line yes.

**Tier 1: their own public policy is the hook**
| Company | Hook | Ask |
|---|---|---|
| Atlassian (Rovo) | Trains across customers on de-identified content; promises a retrain within 90 days of opt-out, with no proof | ML platform, Rovo AI, privacy eng |
| HubSpot | Says removal from trained models is "not possible"; reversed cross-account pooling after backlash (Jul 2026) | AI/ML eng, data privacy |
| Intercom (Fin) | Fine-tunes retrieval/reranking models on anonymized conversations across customers | Fin ML team |
| Zendesk | Models "could be used by other customers"; opt-out by ticket | AI/ML, trust & privacy |
| Salesforce (Einstein, Slack) | Data used before an opt-out "may continue to be retained… for re-training" | Einstein ML, Slack ML |

**Tier 2:**
- Healthcare AI note-takers (Abridge, Microsoft Dragon Copilot, Nabla): health data; anonymized data "will not be purged".
- Cursor: online RL on users' accept/reject signals.
- Figma: content training by default on lower tiers, plus a class action.
- Fine-tuning platforms hosting many customers' fine-tunes (e.g. Together AI, Fireworks).

**Skip:**
- Fraud networks (they want pooling).
- Zoom, Notion, Grammarly Enterprise (they don't train on customer data).
- Competitors (Hirundo, Transcend): ask them as experts, not as pilots.

## Who to ask (roles, not companies)
- ML platform / applied ML engineers who fine-tune models on customer data
- Privacy engineers, data-protection and AI-governance people
- PMs for AI features at B2B SaaS companies

Companies where the problem is real today (see docs/RESEARCH.md): Atlassian (Rovo), HubSpot, Intercom (Fin),
Zendesk, Salesforce (Einstein), Figma, Slack, Cursor, and healthcare AI scribes.

## Channels, fastest first
1. Mentors, judges and sponsor engineers **at the hackathon**. Show the demo on a laptop in person.
2. Each teammate's **1st-degree connections** at the companies above.
3. **Warm intros:** ask a friend to forward the short message.
4. Cold LinkedIn/X DMs (expect a low same-day reply rate; send 15–20). Send by hand; no automation tools.

## Message (short; paste the demo link)
> Hi {name}. I'm at {hackathon} today building **Lethe**: when a B2B customer leaves, it removes their data's
> influence from a shared fine-tuned model (by retraining only that customer's shard) and issues a deletion
> certificate the customer can verify. Since you work on {ML platform / AI features} at {company}, I'd really value a
> reality check. Would you take a 2-minute look ({demo link}) and, if it seems relevant, be open to trying it on a toy
> pipeline in the coming weeks? A one-line reply is plenty.

In person, the same ask in one breath:
> "Can I show you 60 seconds of this and ask whether you'd ever test it?"

## When they say yes: the one-line non-binding LOI
Ask them to reply in writing (email, DM or a note) with something like:
> I'm interested in evaluating Lethe on a non-production setup and giving feedback. This is non-binding, in my
> personal capacity, and doesn't commit {company}.
> You may mention: ☐ my name ☐ my role ☐ my company ☐ none of these (anonymous)

## Rules for what we say on stage
- Count only **written** yeses. Keep screenshots.
- Name a company only with explicit permission. Otherwise say "an ML engineer at a large B2B SaaS company".
- Say "engineers at X agreed to test it", never "X is piloting / partnering".
- If we have fewer than 2 by demo time, say it honestly: "we're talking to engineers at …".

## Tracker
| Who (role @ company) | Channel | Sent by | Sent at | Reply | Written yes? | May name? |
|---|---|---|---|---|---|---|
| | | | | | | |
