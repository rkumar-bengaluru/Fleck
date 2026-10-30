# Test audio

Built by `make fixtures` (macOS `say`; Rishi = rupak.k on the left channel, Samantha = the prospect on the right). The same scripts give byte-identical WAVs. 16 kHz, 16-bit, stereo. Do not edit the WAVs; edit `script.yaml` and rebuild.

- `conversation/`: Presales call with Harbourline Legal, 6.4 min. `conversation-bleed.wav` has the right channel leaked into the left at -14 dB, 30 ms delay, 250 ms reverb tail (for tuning the speaker gate). Both share `reference.json`.
- `negative/`: 12.5 min, 120 short (<1 s), 80 medium (about 2-3 s) and 30 logistics segments, gaps of at least 1.5 s. Nothing in it may trigger an answer. Each turn has a `category`.

`reference.json`: `duration_ms`, `turns` (`speaker`, `start_ms`, `end_ms`, `text`, `question_id`), `questions` (`id`, `text`, `end_ms`, `expect`, `doc`, `used`). `end_ms` is the end of the client's question audio.

## Expected outcome for each scripted question

| id | Client asks | Expect | Document | Used |
|---|---|---|---|---|
| q_rate | What does a senior engineer cost per month, offshore? | library | Rate card FY2026 | yes: you say "five thousand two hundred dollars a month" |
| q_start | How quickly could you have a team working on this? | library | Delivery FAQ | yes: pod of six in two weeks, twenty in four |
| q_ip | Who owns the code you write for us? | library | Delivery FAQ | no: you talk about iManage |
| q_sla | If something breaks at night, how fast do you respond? | library | Delivery FAQ | no: on topic, no figures |
| q_case | Have you automated redline review for a law firm before? | library | Case study: Calloway & Marsh | yes |
| q_general | How do you stop the model inventing clauses in a contract? | general | none | yes |
| q_liability | Would you cap liability at twelve months of fees? | reference | Photronics outsourcing agreement (its limited-liability clause caps liability at the amounts paid in the twelve months before the event) | no: you defer to the contract discussion |
| q_price | Ballpark, what would the whole project cost us? | not_in_material | none | no: you say it is scoped in paid discovery, no figure |

Other client lines that must not trigger an answer: the opening "Can you hear me okay?" and "Can you see my screen?", "Shall we take ten minutes at the end for next steps?", "Is it all right if I send you our playbook after this call?" and "Does Thursday work for the follow up?". Two overlaps (the client starts 0.4 s before you finish) are non-questions. Spoken figures and date: "five thousand two hundred dollars a month", "fifteenth of November".
