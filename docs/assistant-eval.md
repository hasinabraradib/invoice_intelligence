# Assistant evaluation set

15 questions with known answers over a small **synthetic corpus** (the real sample invoices
are private and not in the repository). `backend/scripts/assistant_eval.py` builds the
corpus in a throw-away database with the real pipeline and LLM provider, asks every
question through the real agent, and scores two things per question:

- **number**: the expected value appears in the answer. Amounts are compared as Decimal, so
  `2,000.00` = `2000`; dates and text are compared case-insensitively. `NONE` means the
  answer must say nothing was found and must not invent an amount.
- **citation**: one of the answer's citations points at the expected file and page (`-` = no
  citation required).

```bash
make assistant-eval   # needs LLM_PROVIDER and LLM_API_KEY in .env (it makes real API calls)
```

## Corpus

| File | Pages | Invoice | Supplier | Date | Due | Total |
|---|---|---|---|---|---|---|
| eval-acme-march.pdf | 1 | INV-1001 | ACME Supplies Ltd | 2026-03-02 | | 1,200.00 BDT |
| eval-acme-late-march.pdf | 1 | INV-1002 | ACME Supplies Ltd | 2026-03-20 | | 800.00 BDT |
| eval-globex.pdf | 1 | GX-77 | Globex Corp | 2026-02-11 | | 450.50 USD |
| eval-power.pdf | 1 | DP-3 | Dhaka Power Co (monthly electricity bill) | 2026-03-05 | 2026-03-25 | 3,400.00 BDT |
| eval-batch.pdf | 3 | IN-9 (p. 1) | Initech | 2026-01-15 | | 99.99 USD |
| | | UT-5 (p. 2-3) | Umbrella Trading | 2026-03-28 | | 2,000.00 BDT (total on p. 3) |

## Questions

| # | Question | Expected value | Expected citation |
|---|---|---|---|
| 1 | How much did we spend with ACME in total? | 2000.00 | eval-acme-march.pdf:1 |
| 2 | How many invoices do we have from ACME Supplies? | 2 | eval-acme-march.pdf:1 |
| 3 | What is the total of invoice GX-77? | 450.50 | eval-globex.pdf:1 |
| 4 | How much was the electricity bill? | 3400.00 | eval-power.pdf:1 |
| 5 | When is the Dhaka Power invoice due? | 2026-03-25 | eval-power.pdf:1 |
| 6 | How much did we spend in USD altogether? | 550.49 | eval-globex.pdf:1 |
| 7 | What did we spend in BDT in March 2026? | 7400.00 | eval-power.pdf:1 |
| 8 | How many invoices are dated in March 2026? | 4 | eval-acme-march.pdf:1 |
| 9 | What is the invoice number of the Umbrella Trading invoice? | UT-5 | eval-batch.pdf:2 |
| 10 | On which page of eval-batch.pdf does the Umbrella Trading invoice start? | 2 | eval-batch.pdf:2 |
| 11 | What was the total of the Initech invoice? | 99.99 | eval-batch.pdf:1 |
| 12 | What is the total of invoice UT-5? | 2000.00 | eval-batch.pdf:3 |
| 13 | How many invoices are there in total? | 6 | - |
| 14 | What is the invoice date of INV-1001? | 2026-03-02 | eval-acme-march.pdf:1 |
| 15 | How much did we spend with Globex in March 2026? | NONE | - |
