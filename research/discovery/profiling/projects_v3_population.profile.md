### projects_v3_population

Source: search API v3 projects (selected fields). Rows profiled: 28193

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `boardapprovaldate` | str:date[ISO8601Z] | 97.72 | 2.28 | 0.0 | 0.0 | 7313 | 1947-05-09 → 2027-10-26 |
| `borrower` | str | 28.93 | 71.07 | 0.0 | 0.0 | 2698 | Ministry of Finance (652); Ministry of Economy and Finance (153); India (137) |
| `closingdate` | str:date[YYYY-MM-DD] | 56.1 | 43.9 | 0.0 | 0.0 | 1904 | 1972-07-31 → 2045-12-31 |
| `cons_serv_reqd_ind` | str | 21.23 | 78.77 | 0.0 | 0.0 | 3 | Yes (3812); TBD (1177); No (995) |
| `countrycode` | list | 100.0 | 0.0 | 0.0 | 0.0 | 218 | ["IN"] (1119); ["BR"] (834); ["ID"] (810) |
| `countryname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 218 | Republic of India (1119); Federative Republic of Brazil (834); Republic of Indonesia (810) |
| `countryshortname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 219 | India (1119); Brazil (834); Indonesia (810) |
| `curr_ibrd_commitment` | str:int, str:decimal | 48.69 | 51.31 | 0.0 | 0.0 | 1393 | 0 (7997); 100000000 (412); 50000000 (252) |
| `curr_ida_commitment` | str:int, str:decimal | 48.66 | 51.34 | 0.0 | 0.0 | 1510 | 0 (5408); 100000000 (395); 50000000 (377) |
| `curr_project_cost` | str:int, str:decimal | 97.71 | 2.29 | 0.0 | 0.0 | 5974 | 0 (10504); 100000000 (467); 50000000 (394) |
| `curr_total_commitment` | str:int, str:decimal | 64.2 | 35.8 | 0.0 | 0.0 | 4802 | 100000000 (704); 50000000 (530); 150000000 (401) |
| `envassesmentcategorycode` | str | 55.64 | 44.36 | 0.0 | 0.0 | 7 | C (7343); B (6518); A (1348) |
| `financers` | list | 64.51 | 35.49 | 0.0 | 0.0 | 14642 | [{"fincr_curr_amt": "0", "fincr_curr_id" (76); [{"fincr_curr_amt": "100000000", "fincr_ (65); [{"fincr_curr_amt": "0", "fincr_curr_id" (57) |
| `fiscalyear` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 84 | 1999 (878); 1995 (783); 2010 (703) |
| `grantamt` | str:int, str:decimal | 64.2 | 35.8 | 0.0 | 0.0 | 2959 | 0 (10864); 5000000 (228); 10000000 (183) |
| `icr_ratings` | list | 22.84 | 77.16 | 0.0 | 0.0 | 5308 | [{"bankqualityentry": "Not Rated", "bank (87); [{"bankqualityentry": null, "banksupervi (49); [{"bankqualityentry": null, "banksupervi (37) |
| `id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 28193 (unique) | P000001 (1); P000002 (1); P000003 (1) |
| `idacommamt` | str:int, str:decimal | 48.66 | 51.34 | 0.0 | 0.0 | 1510 | 0 (5408); 100000000 (395); 50000000 (377) |
| `ieg_ratings` | list | 45.15 | 54.85 | 0.0 | 0.0 | 10731 | [{"bankqualityofsupervision": "Not Rated (29); [{"bankqualityofsupervision": "Not Rated (20); [{"bankqualityofsupervision": "Not Rated (19) |
| `impagency` | str | 29.81 | 70.19 | 0.0 | 0.0 | 5871 | Ministry of Finance (306); Ministry of Health (197); Ministry of Economy and Finance (120) |
| `isr_ratings` | list | 32.07 | 67.93 | 0.0 | 0.0 | 8633 | [{"code": "18", "isr_date": "2026-06-30" (11); [{"code": "18", "isr_date": "2026-06-25" (7); [{"code": "18", "isr_date": "2026-06-26" (5) |
| `last_stage_reached_name` | str | 62.43 | 37.57 | 0.0 | 0.0 | 13 | Board Approved (14127); Concept Review (1302); RVP Approved (708) |
| `lendprojectcost` | str:int, str:decimal | 97.71 | 2.29 | 0.0 | 0.0 | 5974 | 0 (10504); 100000000 (467); 50000000 (394) |
| `loan_effective_date` | str:date[YYYY-MM-DD] | 62.33 | 37.67 | 0.0 | 0.0 | 10035 | 1947-06-09 → 2026-10-13 |
| `major_sector_code` | str | 82.9 | 17.1 | 0.0 | 0.0 | 1038 | AX (2235); TX (1682); BX (1456) |
| `major_sector_name` | str | 82.9 | 17.1 | 0.0 | 0.0 | 1395 | Agriculture, Fishing and Forestry (1452); FY17 - Public Administration (1249); Transportation (1079) |
| `major_sectors` | list | 82.9 | 17.1 | 0.0 | 0.0 | 11873 | [{"major_sector": {"major_sector_code":  (471); [{"major_sector": {"major_sector_code":  (465); [{"major_sector": {"major_sector_code":  (389) |
| `milestones` | list | 100.0 | 0.0 | 0.0 | 0.0 | 20097 | [{"apprvl_date": "1999-06-30T00:00:00Z", (475); [{"apprvl_date": "1995-06-30T00:00:00Z", (470); [{"apprvl_date": "1988-06-30T00:00:00Z", (444) |
| `pdo` | str | 57.53 | 42.44 | 0.0 | 0.03 | 14091 | TBD (32); SUPPLEMENTAL CREDIT (16); To contribute to sustaining the governme (11) |
| `proj_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 28193 (unique) | P000001 (1); P000002 (1); P000003 (1) |
| `proj_last_upd_date` | str:date[ISO8601Z] | 62.91 | 37.09 | 0.0 | 0.0 | 1418 | 2013-01-15 → 2026-10-14 |
| `project_abstract` | str, str:multiline | 52.48 | 47.52 | 0.0 | 0.01 | 14584 | The Second Loan Administration Report fo (6); The objective of the Pacific Islands Reg (6); The projects under the Pacific Resilienc (6) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 24280 | EDUCATION II (76); POWER (55); EDUCATION (54) |
| `projectfinancialtype` | list | 64.41 | 35.59 | 0.0 | 0.0 | 149 | ["IDA"] (3979); ["IBRD"] (2720); ["Grants"] (2614) |
| `public_disclosure_date` | str:date[YYYY-MM-DD] | 62.07 | 37.93 | 0.0 | 0.0 | 6941 | 1980-02-01 → 2026-10-15 |
| `regionname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 9 | Latin America and Caribbean (5255); Eastern and Southern Africa (4653); Western and Central Africa (4238) |
| `status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | Closed (18319); Dropped (6907); Active (2624) |
| `themev2_level1_exact` | list | 60.69 | 39.31 | 0.0 | 0.0 | 5748 | ["FY17 - Environment and Natural Resourc (274); ["FY17 - Public Sector Management"] (241); ["FY17 - Human Development and Gender",  (209) |
| `totalamt` | str:int, str:decimal | 48.65 | 51.35 | 0.0 | 0.0 | 2339 | 100000000 (779); 50000000 (607); 150000000 (458) |
