### ds00001_ida_latest

Source: Finances One DS00001 bulk CSV. Rows profiled: 11486

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `Agreement Signing Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 5815 | 1900-01-01 → 2026-09-30 |
| `Board Approval Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 4015 | 1961-05-11 → 2026-09-30 |
| `Board approval - Calendar year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 66 | 2020 (495); 2025 (468); 2022 (416) |
| `Board approval - Fiscal year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 67 | 2020 (445); 2025 (434); 2022 (430) |
| `Borrower` | str | 100.0 | 0.0 | 0.0 | 0.0 | 304 | MINISTRY OF FINANCE (1684); Ministry of Finance (1303); MINISTERE DE L'ECONOMIE ET DES FINANCES (577) |
| `Borrowers Obligation (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 5316 | 0.0 (5384); -0.01 (183); 0.01 (177) |
| `Cancelled Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 5991 | 0.0 (5385); 10000000.0 (11); 20000000.0 (7) |
| `Closed Date (Most Recent)` | str:date[MM/DD/YYYY] | 99.98 | 0.0 | 0.02 | 0.0 | 1328 | 1963-09-30 → 2035-12-31 |
| `Country / Economy` | str | 100.0 | 0.0 | 0.0 | 0.0 | 131 | India (442); Bangladesh (398); Pakistan (341) |
| `Country / Economy Code` | str | 100.0 | 0.0 | 0.0 | 0.0 | 131 | IN (442); BD (398); PK (341) |
| `Credit Number` | str | 100.0 | 0.0 | 0.0 | 0.0 | 11486 (unique) | IDA29410 (1); IDA29440 (1); IDA29580 (1) |
| `Credit Status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 10 | Repaying (4274); Fully Repaid (2612); Disbursing (1822) |
| `Credits Held (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 6252 | 0.0 (3966); -0.01 (187); 0.01 (178) |
| `Currency of Commitment` | str | 100.0 | 0.0 | 0.0 | 0.0 | 6 | XDR (9031); USD (1773); EUR (654) |
| `Disbursed Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 10331 | 0.0 (737); 10000000.0 (27); 5000000.0 (23) |
| `Due 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0.0 (11486) |
| `Due to IDA (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 5316 | 0.0 (5384); -0.01 (183); 0.01 (177) |
| `Effective Date (Most Recent)` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 6561 | 1900-01-01 → 2026-09-29 |
| `End of Period` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 2026-09-30 → 2026-09-30 |
| `Exchange Adjustment (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0.0 (11486) |
| `First Repayment Date` | str:date[MM/DD/YYYY] | 76.19 | 0.0 | 23.81 | 0.0 | 1408 | 1968-04-01 → 2038-11-01 |
| `Last Disbursement Date` | str:date[MM/DD/YYYY] | 81.8 | 0.0 | 18.2 | 0.0 | 4920 | 1984-10-01 → 2026-10-02 |
| `Last Repayment Date` | str:date[MM/DD/YYYY] | 76.19 | 0.0 | 23.81 | 0.0 | 1223 | 1968-04-01 → 2075-06-15 |
| `Original Principal Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2355 | 50000000.0 (406); 10000000.0 (405); 20000000.0 (398) |
| `Project ID` | str, str:int | 100.0 | 0.0 | 0.0 | 0.0 | 8037 | P150080 (21); P169265 (18); P175043 (17) |
| `Project Name` | str | 99.97 | 0.0 | 0.03 | 0.0 | 7299 | EDUCATION II (39); SAC (33); EDUCATION (31) |
| `Region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7 | WESTERN AND CENTRAL AFRICA (3292); EASTERN AND SOUTHERN AFRICA (3077); SOUTH ASIA (1325) |
| `Repaid 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 20 | 0.0 (11466); 6000000.0 (2); 6861742.0 (1) |
| `Repaid to IDA (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 7007 | 0.0 (4252); 10000000.0 (19); 5000000.0 (19) |
| `Service Charge Rate` | str:decimal | 76.23 | 0.0 | 23.77 | 0.0 | 121 | 0.75 (6825); 0.0 (607); 2.0 (553) |
| `Sold 3rd Party (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 20 | 0.0 (11466); 6000000.0 (2); 6861742.0 (1) |
| `Undisbursed Amount (US$)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2275 | 0.0 (8980); -0.01 (39); 0.01 (30) |
