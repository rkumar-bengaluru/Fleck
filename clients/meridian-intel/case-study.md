# Case study: Meridian Intelligence

Simulated document for the Fleck demo. Meridian Intelligence is a fictional corporate investigations firm. Approved for external use by Meridian, 4 March 2026.

## Challenge

Analysts spent about 2 days building a background file on a subject, searching registries, court records and news by hand, and often missed links between entities in different sources.

## What Northbeam did

- A research agent that takes a subject name, searches 9 licensed and public sources, and drafts a background file with a source link behind every statement.
- Entity graph that flags shared directors, addresses and filings across sources.
- 8-week build, team of 6, 4 from Bengaluru. Runs inside Meridian's cloud account.

## Results

- **Background file:** from about 2 days to 3 hours, including analyst review.
- **Coverage:** 9 sources searched per subject, up from 4 by hand.
- **Capacity:** 5× the background files per analyst per week.
- **Accuracy:** 4% of statements corrected in review, each one traced to a source.

## Client quote

"My analysts spend their time judging evidence, not hunting for it." Director of Research, Meridian Intelligence.

## What we'd do differently

Two sources changed their page layout during the build and broke the collectors. We would now agree a fallback for each source, a licensed feed or a manual step, before the build starts.
