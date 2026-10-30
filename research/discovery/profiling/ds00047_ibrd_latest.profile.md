### ds00047_ibrd_latest

Source: Finances One DS00047 bulk CSV. Rows profiled: 9538

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `Agreement Signing Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 5146 | 1900-01-01 → 2026-09-29 |
| `Board Approval Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3647 | 1947-05-09 → 2026-09-30 |
| `Board approval - Calendar year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 80 | 1994 (284); 1991 (276); 1995 (265) |
| `Board approval - Fiscal year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 81 | 1995 (296); 1994 (263); 1991 (260) |
| `Borrower` | str | 99.39 | 0.0 | 0.61 | 0.0 | 975 | MINISTRY OF FINANCE (1292); Ministry of Finance (1122); CONTROLLER OF AID ACCOUNTS & AUDIT (368) |
| `Borrower's Obligation (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2001 | 0.0 (7048); -0.01 (173); 0.01 (154) |
| `Cancelled Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 4566 | 0.0 (4654); 20000000.0 (23); 100000000.0 (19) |
| `Closed Date (Most Recent)` | str:date[MM/DD/YYYY] | 99.92 | 0.0 | 0.08 | 0.0 | 1352 | 1947-12-31 → 2035-11-30 |
| `Country / Economy` | str | 100.0 | 0.0 | 0.0 | 0.0 | 148 | Indonesia (657); Brazil (533); China (497) |
| `Country / Economy Code` | str | 99.97 | 0.0 | 0.0 | 0.03 | 148 | ID (657); BR (533); CN (497) |
| `Currency of Commitment` | — | 0.0 | 0.0 | 100.0 | 0.0 | 0 |  |
| `Disbursed Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 7016 | 0.0 (758); 100000000.0 (116); 25000000.0 (72) |
| `Due 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0.0 (9538) |
| `Due to IBRD (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2154 | 0.0 (7256); 500000000.0 (22); 700000000.0 (8) |
| `Effective Date (Most Recent)` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 5788 | 1900-01-01 → 2026-09-30 |
| `End of Period` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 2026-09-30 → 2026-09-30 |
| `Exchange Adjustment (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 774 | 0.0 (8426); -0.01 (165); 0.01 (143) |
| `First Repayment Date` | str:date[MM/DD/YYYY] | 99.36 | 0.0 | 0.64 | 0.0 | 1657 | 1949-01-15 → 2044-05-15 |
| `Guarantor` | str | 97.05 | 0.0 | 2.95 | 0.0 | 127 | Indonesia (657); Brazil (532); China (497) |
| `Guarantor Country / Economy Code` | str | 97.02 | 0.0 | 2.95 | 0.03 | 127 | ID (657); BR (532); CN (497) |
| `Interest Rate` | str:decimal | 98.86 | 0.0 | 1.14 | 0.0 | 463 | 0.0 (3292); 7.25 (368); 8.5 (273) |
| `Last Disbursement Date` | str:date[MM/DD/YYYY] | 62.34 | 0.0 | 37.66 | 0.0 | 4034 | 1983-10-11 → 2026-10-05 |
| `Last Repayment Date` | str:date[MM/DD/YYYY] | 99.37 | 0.0 | 0.63 | 0.0 | 1780 | 1951-01-01 → 2068-02-01 |
| `Loan Number` | str | 100.0 | 0.0 | 0.0 | 0.0 | 9538 (unique) | IBRD86710 (1); IBRD86760 (1); IBRD86840 (1) |
| `Loan Status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 11 | Fully Repaid (6634); Repaying (1209); Disbursing (531) |
| `Loan Type` | str | 100.0 | 0.0 | 0.0 | 0.0 | 11 | FSL (2936); CPL (2345); NPL (2013) |
| `Loans Held (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2093 | 0.0 (7062); 500000000.0 (41); 200000000.0 (35) |
| `Original Principal Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 3540 | 100000000.0 (342); 0.0 (314); 50000000.0 (234) |
| `Project ID` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7309 | P005748 (14); P178946 (11); P004925 (10) |
| `Project Name` | str | 99.73 | 0.0 | 0.27 | 0.0 | 6284 | POWER (41); EDUCATION II (33); SAL I (25) |
| `Region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7 | LATIN AMERICA AND CARIBBEAN (2982); EAST ASIA AND PACIFIC (2021); EUROPE AND CENTRAL ASIA (1953) |
| `Repaid 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 768 | 0.0 (8667); 100000.0 (19); 50000.0 (14) |
| `Repaid to IBRD (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 7074 | 0.0 (1560); 100000000.0 (61); 25000000.0 (34) |
| `Sold 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 768 | 0.0 (8667); 100000.0 (19); 50000.0 (14) |
| `Undisbursed Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 747 | 0.0 (8718); 200000000.0 (14); 0.01 (13) |
