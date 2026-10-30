### ds00975_history_sample

Source: Finances One DS00975 API (3 loans). Rows profiled: 507

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `agreement_signing_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1974-02-13 → 2015-09-16 |
| `board_approval___calendar_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1987 (188); 1974 (186); 2015 (133) |
| `board_approval___fiscal_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1988 (188); 1974 (186); 2016 (133) |
| `board_approval_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1974-02-05 → 2015-09-15 |
| `borrower` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | BANK OF INDUS. & MAIN BANK OF IRAN (186); NACIONAL FINANCIERA, S.N.C. NAFIN (185); Ministry of Finance (133) |
| `borrowers_obligation` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 15 | 0 (297); 500000000 (77); -0.01 (47) |
| `cancelled_amount` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 0 (321); 4113815.38 (186) |
| `closed_date_most_recent` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1979-03-31 → 2016-06-30 |
| `country` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | Mexico (188); Iran, Islamic Republic of (186); Ukraine (133) |
| `country_code` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | MX (188); IR (186); UA (133) |
| `currency_of_commitment` | — | 0.0 | 88.76 | 11.24 | 0.0 | 0 |  |
| `disbursed_amount` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 29975000 (188); 70886184.62 (186); 500000000 (133) |
| `due_3rd_party` | int | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0 (507) |
| `due_to_ibrd` | int | 100.0 | 0.0 | 0.0 | 0.0 | 12 | 0 (374); 500000000 (77); 386250000 (6) |
| `effective_date_most_recent` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1974-05-15 → 2015-09-16 |
| `end_of_period` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 186 | 2011-04-30 → 2026-09-30 |
| `exchange_adjustment` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 4 | 0 (430); -0.01 (47); 0.01 (23) |
| `first_repayment_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1979-04-15 → 2022-02-15 |
| `guarantor` | str | 98.22 | 1.78 | 0.0 | 0.0 | 3 | Mexico (184); Iran, Islamic Republic of (182); Ukraine (132) |
| `guarantor_country_code` | str | 99.61 | 0.39 | 0.0 | 0.0 | 3 | MX (187); IR (185); UA (133) |
| `interest_rate` | float, int | 99.41 | 0.59 | 0.0 | 0.0 | 6 | 7.96 (183); 7.25 (182); 0 (131) |
| `last_disbursement_date` | str:date[DD-Mon-YYYY] | 26.63 | 73.37 | 0.0 | 0.0 | 2 | 1998-01-01 → 2015-09-17 |
| `last_repayment_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 1991-10-15 → 2032-08-15 |
| `loan_number` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | IBRD2876S (188); IBRD09630 (186); IBRD85370 (133) |
| `loan_status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 6 | Fully Repaid (244); Repaid (130); Fully Disbursed (64) |
| `loan_type` | str | 100.0 | 0.0 | 0.0 | 0.0 | 5 | FSL (133); SCPD (95); NPL (95) |
| `loans_held` | int | 100.0 | 0.0 | 0.0 | 0.0 | 12 | 0 (374); 500000000 (77); 386250000 (6) |
| `original_principal_amount` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 29975000 (188); 75000000 (186); 500000000 (133) |
| `project_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | P007657 (188); P005201 (186); P151941 (133) |
| `project_name_` | str | 88.76 | 11.24 | 0.0 | 0.0 | 3 | MANPWR TRG PRJ (169); IMDBI VIII (167); Programmatic Financial Sector DPL2 (114) |
| `region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | LATIN AMERICA AND CARIBBEAN (188); MIDDLE EAST AND NORTH AFRICA (171); EUROPE AND CENTRAL ASIA (133) |
| `repaid_3rd_party` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 0 (321); 5709725.63 (186) |
| `repaid_to_ibrd` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 13 | 29975000 (188); 65176458.99 (186); 0 (77) |
| `sold_3rd_party` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 0 (321); 5709725.63 (186) |
| `undisbursed_amount` | int | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0 (507) |
