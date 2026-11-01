# Case study: Brightwater Insurance

Simulated document for the Fleck demo. Brightwater Insurance is a fictional commercial insurer. Approved for external use by Brightwater, 12 February 2026.

## Challenge

Underwriting assistants re-typed key terms from broker contracts and policy schedules into the policy system: limits, deductibles, renewal dates and exclusions. About 9,000 contracts a year took 25 minutes each, and 6% had a keying error found later.

## What Northbeam did

- A contract-extraction pipeline that reads scanned and digital PDFs, pulls 32 fields, and shows each value beside the page it came from.
- Low-confidence fields go to a person for confirmation; the rest flow into the policy system.
- 10-week build on Azure, team of 7, 5 from Bengaluru.

## Results

- **Handling time:** from 25 minutes to 6 minutes per contract.
- **Keying errors:** from 6% to 0.8%.
- **Straight-through:** 71% of contracts needed no manual correction.
- **Payback:** 9 months on the project cost.

## Client quote

"We stopped typing and started checking." Head of Underwriting Operations, Brightwater Insurance.

## What we'd do differently

We trained on clean sample contracts and the first scanned batch scored badly. We would now ask for 200 real scans, including the worst ones, in the first week.
