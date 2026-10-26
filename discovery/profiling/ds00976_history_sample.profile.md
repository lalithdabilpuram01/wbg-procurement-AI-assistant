### ds00976_history_sample

Source: Finances One DS00976 API (3 loans). Rows profiled: 408

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `agreement_signing_date` | str:date[DD-Mon-YYYY] | 98.04 | 1.96 | 0.0 | 0.0 | 3 | 2008-10-30 → 2022-10-26 |
| `board_approval___calendar_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 2008 (186); 2012 (167); 2022 (55) |
| `board_approval___fiscal_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 2008 (186); 2013 (167); 2022 (55) |
| `board_approval_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 2008-06-24 → 2022-03-09 |
| `borrower` | str | 100.0 | 0.0 | 0.0 | 0.0 | 6 | Ministry of Eco, Planning and RegionDev (241); Ministère des Finances, du Budget et (68); Minist�re des Finances, du Budget et (42) |
| `borrowers_obligation_us_` | float, int | 100.0 | 0.0 | 0.0 | 0.0 | 230 | 0 (179); 31616586.25 (1); 34390207.18 (1) |
| `cancelled_amount_us_` | float, int | 100.0 | 0.0 | 0.0 | 0.0 | 5 | 103619.41 (151); 0 (95); 27348397.73 (79) |
| `closed_date_most_recent` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 6 | 2013-12-30 → 2028-03-30 |
| `country` | str | 100.0 | 0.0 | 0.0 | 0.0 | 2 | Cameroon (241); Comoros (167) |
| `country_code` | str | 100.0 | 0.0 | 0.0 | 0.0 | 2 | CM (241); KM (167) |
| `credit_number` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | IDA44840 (186); IDAH8210 (167); IDA70480 (55) |
| `credit_status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 8 | Disbursing (145); Fully Disbursed (121); Repaying (79) |
| `credits_held_us_` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 256 | 0 (151); 150958640 (2); 150011260 (2) |
| `currency_of_commitment` | str | 100.0 | 0.0 | 0.0 | 0.0 | 2 | XDR (353); EUR (55) |
| `disbursed_amount_us_` | float, int | 100.0 | 0.0 | 0.0 | 0.0 | 115 | 4971629 (151); 33622151.08 (80); 33703220.47 (23) |
| `due_3rd_party_us_` | int | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0 (408) |
| `due_to_ida_us_` | float, int | 100.0 | 0.0 | 0.0 | 0.0 | 230 | 0 (179); 31616586.25 (1); 34390207.18 (1) |
| `effective_date_most_recent` | str:date[DD-Mon-YYYY] | 98.04 | 1.96 | 0.0 | 0.0 | 4 | 2009-01-28 → 2022-11-21 |
| `end_of_period` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 186 | 2011-04-30 → 2026-09-30 |
| `exchange_adjustment_us_` | str:int, str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 0 (399); 0.000000 (9) |
| `first_repayment_date` | str:date[DD-Mon-YYYY] | 59.07 | 40.93 | 0.0 | 0.0 | 2 | 2018-08-15 → 2027-04-15 |
| `last_disbursement_date` | str:date[DD-Mon-YYYY] | 88.48 | 11.52 | 0.0 | 0.0 | 92 | 1947-04-29 → 2026-08-19 |
| `last_repayment_date` | str:date[DD-Mon-YYYY] | 59.07 | 40.93 | 0.0 | 0.0 | 2 | 2048-02-15 → 2051-10-15 |
| `original_principal_amount_us_` | int | 100.0 | 0.0 | 0.0 | 0.0 | 3 | 65000000 (186); 5000000 (167); 160000000 (55) |
| `project_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | P104456 (186); P122941 (167); P175363 (55) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | KM-DPO (167); CM-Energy Sector Development SIL (FY08) (150); Safety Nets and Economic Inclusion (55) |
| `region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7 | AFRICA (203); WESTERN AND CENTRAL AFRICA (104); EASTERN AND SOUTHERN AFRICA (52) |
| `repaid_3rd_party_us_` | int | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0 (408) |
| `repaid_to_ida_us_` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 23 | 0 (310); 2111639.71 (7); 3054955.36 (7) |
| `service_charge_rate` | float, int | 73.53 | 26.47 | 0.0 | 0.0 | 3 | 0.75 (186); 0 (66); 1.06 (48) |
| `sold_3rd_party_us_` | int | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 0 (408) |
| `undisbursed_amount_us_` | int, float | 100.0 | 0.0 | 0.0 | 0.0 | 179 | 0 (230); 121569103.8 (1); 112807192.8 (1) |
