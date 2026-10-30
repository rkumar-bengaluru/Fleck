# Case study: Larkspur Retail Group handles 3× Black Friday peak on a new order platform

Simulated document for the Fleck demo. Approved for external use by Larkspur, 10 January 2026.

## Challenge

A 2009 on-premise order management system failed at 1,200 orders/minute during Black Friday 2024, costing an estimated USD 2.1M in lost sales.

## What Northbeam did

- 4-week discovery, then a 6½-month build on AWS (EKS, Kafka, Aurora PostgreSQL), replacing the old system one order type at a time.
- Ship-from-store and BOPIS piloted in 20 stores, then rolled out to all 300 in 5 weekly waves.
- Team of 17, 85% from Bengaluru.

## Results

- **Black Friday 2025:** peak of 3,700 orders/minute with no checkout errors; load-tested to 5,600.
- **Order-to-ship time:** from 31 hours to 9.5 hours.
- **BOPIS:** 14% of online orders within 3 months of launch.
- **Infrastructure cost:** down 22% against the on-premise data centre.
- Delivered on 9 October 2025, 6 days early, within the fixed price.

## Client quote

"Northbeam took the scariest system we had and replaced it without a single bad night." CIO, Larkspur Retail Group.

## What we'd do differently

The SAP integration slipped by 3 weeks because the third-party SAP team joined late. We now ask for the integration partner to be named and staffed before the SOW is signed.
