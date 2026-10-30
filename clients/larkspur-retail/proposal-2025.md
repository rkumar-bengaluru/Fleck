# Proposal: Order management modernisation for Larkspur Retail Group

Simulated document for the Fleck demo. Submitted by Northbeam Digital, 14 January 2025.

## Client situation

Larkspur runs 300 stores across 11 US states and an online store with 28% of revenue. Orders are managed in a 2009 on-premise OMS customised in Java EE. Black Friday 2024 peaked at 1,900 orders/minute and the OMS failed at 1,200, causing 3 hours of degraded checkout.

## Objectives

1. Handle 5,000 orders/minute at peak.
2. Ship-from-store and buy-online-pick-up-in-store (BOPIS) across all 300 stores.
3. Cut order-to-ship time from 31 hours to under 12.
4. Retire the on-premise OMS by Q4 2025.

## Approach

- **Discovery (4 weeks):** map 140 order flows, data model, integrations (SAP S/4HANA finance, Manhattan WMS, Salesforce Commerce Cloud).
- **Build (5 months):** event-driven order services on AWS (EKS, MSK/Kafka, Aurora PostgreSQL); strangler pattern, one order type at a time.
- **Cutover (6 weeks):** parallel run, state-by-state rollout, rollback plan per wave.

## Team

1 engagement manager, 1 solution architect, 2 tech leads, 9 engineers, 2 QA, 1 DevOps, 1 BA. 85% offshore.

## Commercials

Discovery fixed at USD 38,000. Build and cutover on fixed price USD 1.42M, milestone billing. Hypercare: 8 weeks included.

## Risks

- SAP integration owned by a third party: mitigated with contract tests and a shared integration environment from week 2.
- Peak season freeze from 1 November: cutover must finish by 15 October.
