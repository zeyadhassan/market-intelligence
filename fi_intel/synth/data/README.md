# Synthetic corpus layout

`synthetic_wire.json` serves **12 raw records** that dedupe down to **10
canonical documents** — the number Milestone 2's acceptance criterion
refers to ("ingesting twice produces 10 documents, not 20"). Exact dedupe
(content hash) collapses the reprint pair 0001/0002 (12 → 11);
near-duplicate detection then collapses 0003 into 0001 (11 → 10).

| Records | Role |
|---|---|
| SW-2024-0001 | Gulf Meridian rating outlook → negative (canonical) |
| SW-2024-0002 | Exact reprint of 0001 (same content hash — exact dedupe case) |
| SW-2024-0003 | Same story, different wire wording (near-duplicate case) |
| SW-2024-0004 | FY2023 results — *different* story in the same event window; must **not** be collapsed with 0001–0003 |
| SW-2024-0005 | Treasurer departure |
| SW-2024-0006 | Board approves EMTN programme update |
| SW-2024-0007 | Sukuk maturity wall + AT1 first call date |
| SW-2024-0008 | Mandate announced — the outcome event (day ~191 of episode) |
| SW-2024-0009..0011 | Northern Harbour steady-state decoy (3 docs, zero expected signals) |
| SW-2024-0012 | Gulf Meridian Capital Partners — similar name, different LEI; entity-resolution trap |

Name variants for the M3 resolution test are spread across records:
"Gulf Meridian Bank Q.P.S.C." (0001), "Gulf Meridian Bank" (0004),
"Gulf Meridian" (0005).
