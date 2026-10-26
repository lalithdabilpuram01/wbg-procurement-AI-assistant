### projects_v3_cluster_allfields

Source: search API v3 projects fl=* (cluster). Rows profiled: 276

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `boardapprovaldate` | str:date[ISO8601Z] | 99.64 | 0.36 | 0.0 | 0.0 | 244 | 2008-05-01 → 2026-03-19 |
| `boardapprovaldate_exact` | str:date[YYYY-MM-DD] | 99.64 | 0.36 | 0.0 | 0.0 | 244 | 2008-05-01 → 2026-03-19 |
| `borrower` | str | 99.64 | 0.36 | 0.0 | 0.0 | 193 | Ministry of Finance (9); India (8); People's Republic of Bangladesh (6) |
| `borrower_exact` | str | 99.64 | 0.36 | 0.0 | 0.0 | 193 | Ministry of Finance (9); India (8); People's Republic of Bangladesh (6) |
| `closingdate` | str:date[YYYY-MM-DD] | 99.64 | 0.36 | 0.0 | 0.0 | 121 | 2019-08-31 → 2033-12-31 |
| `cons_serv_reqd_ind` | str | 89.49 | 10.51 | 0.0 | 0.0 | 3 | Yes (163); TBD (46); No (38) |
| `cons_serv_reqd_ind_exact` | str | 89.49 | 10.51 | 0.0 | 0.0 | 3 | Yes (163); TBD (46); No (38) |
| `countrycode` | list | 100.0 | 0.0 | 0.0 | 0.0 | 106 | ["1W"] (15); ["IN"] (14); ["BD"] (9) |
| `countryhomepageurl` | str | 86.23 | 13.77 | 0.0 | 0.0 | 84 | http://www.worldbank.org/en/country/indi (14); http://www.worldbank.org/en/country/bang (9); http://www.worldbank.org/en/country/paci (9) |
| `countryname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 106 | World (15); Republic of India (14); People's Republic of Bangladesh (9) |
| `countryshortname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 106 | World (15); India (14); Bangladesh (9) |
| `countryshortname_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 106 | World (15); India (14); Bangladesh (9) |
| `curr_ibrd_commitment` | str:int | 73.55 | 26.45 | 0.0 | 0.0 | 53 | 0 (128); 50000000 (6); 300000000 (5) |
| `curr_ida_commitment` | str:int | 73.55 | 26.45 | 0.0 | 0.0 | 89 | 0 (71); 150000000 (6); 30000000 (6) |
| `curr_project_cost` | str:int | 92.75 | 7.25 | 0.0 | 0.0 | 186 | 50000000 (8); 250000000 (7); 0 (7) |
| `curr_total_commitment` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 189 | 50000000 (13); 300000000 (9); 100000000 (8) |
| `envassesmentcategorycode` | str | 46.01 | 53.99 | 0.0 | 0.0 | 5 | B (85); A (21); C (19) |
| `esrc_ovrl_risk_rate` | str | 51.45 | 48.55 | 0.0 | 0.0 | 4 | S (56); M (50); L (25) |
| `esrc_ovrl_risk_rate_exact` | str | 51.45 | 48.55 | 0.0 | 0.0 | 4 | S (56); M (50); L (25) |
| `financers` | list | 99.64 | 0.36 | 0.0 | 0.0 | 267 | [{"fincr_curr_amt": "250000000", "fincr_ (3); [{"fincr_curr_amt": "50000000", "fincr_c (2); [{"fincr_curr_amt": "300000", "fincr_cur (2) |
| `fiscalyear` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 18 | 2022 (41); 2023 (24); 2017 (22) |
| `geo_locations` | list | 93.84 | 6.16 | 0.0 | 0.0 | 259 (unique) | [{"admin_unit1_name": null, "admin_unit2 (1); [{"admin_unit1_name": "Yozgat Province", (1); [{"admin_unit1_name": null, "admin_unit2 (1) |
| `grantamt` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 97 | 0 (159); 5000000 (6); 300000 (4) |
| `icr_ratings` | list | 44.93 | 55.07 | 0.0 | 0.0 | 120 | [{"bankqualityentry": null, "banksupervi (2); [{"bankqualityentry": null, "banksupervi (2); [{"bankqualityentry": null, "banksupervi (2) |
| `id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 276 (unique) | P099270 (1); P106284 (1); P111996 (1) |
| `idacommamt` | str:int | 73.55 | 26.45 | 0.0 | 0.0 | 89 | 0 (71); 150000000 (6); 30000000 (6) |
| `ieg_ratings` | list | 33.7 | 66.3 | 0.0 | 0.0 | 92 | [{"bankqualityofsupervision": "Satisfact (2); [{"bankqualityofsupervision": "Satisfact (1); [{"bankqualityofsupervision": "Satisfact (1) |
| `impagency` | str | 100.0 | 0.0 | 0.0 | 0.0 | 255 | Ministry of Health (8); Ministry of Finance (7); Ministry of Education (3) |
| `impagency_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 255 | Ministry of Health (8); Ministry of Finance (7); Ministry of Education (3) |
| `indicators` | list | 85.51 | 14.49 | 0.0 | 0.0 | 236 (unique) | [{"Baseline": {"Comment": null, "Date":  (1); [{"Baseline": {"Comment": null, "Date":  (1); [{"Baseline": {"Comment": null, "Date":  (1) |
| `isr_ratings` | list | 98.91 | 1.09 | 0.0 | 0.0 | 271 | [{"code": "1000", "isr_date": "2023-05-2 (2); [{"code": "18", "isr_date": "2026-07-28" (2); [{"code": "18", "isr_date": "2024-06-02" (1) |
| `last_stage_reached_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 5 | Board Approved (217); CD Approved (27); RVP Approved (19) |
| `lendprojectcost` | str:int | 92.75 | 7.25 | 0.0 | 0.0 | 186 | 50000000 (8); 250000000 (7); 0 (7) |
| `loan_effective_date` | str:date[YYYY-MM-DD] | 99.64 | 0.36 | 0.0 | 0.0 | 255 | 2008-08-13 → 2026-08-06 |
| `loan_transactions` | list | 73.55 | 26.45 | 0.0 | 0.0 | 203 (unique) | [{"amount": "-1350000", "financier_loan" (1); [{"amount": "-1.036179004E7", "financier (1); [{"amount": "-1.313193776E7", "financier (1) |
| `major_sector_code` | str | 100.0 | 0.0 | 0.0 | 0.0 | 84 | TX (28); BX (28); HX (22) |
| `major_sector_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 84 | FY17 - Transportation (28); FY17 - Public Administration (28); FY17 - Health (22) |
| `major_sectors` | list | 100.0 | 0.0 | 0.0 | 0.0 | 245 | [{"major_sector": {"major_sector_code":  (11); [{"major_sector": {"major_sector_code":  (9); [{"major_sector": {"major_sector_code":  (4) |
| `milestones` | list | 100.0 | 0.0 | 0.0 | 0.0 | 276 (unique) | [{"apprvl_date": "2009-04-30T00:00:00Z", (1); [{"apprvl_date": "2008-05-01T00:00:00Z", (1); [{"apprvl_date": "2009-07-09T00:00:00Z", (1) |
| `mjsector` | str | 100.0 | 0.0 | 0.0 | 0.0 | 188 | FY17 - Public Administration - Transport (14); FY17 - Health,FY17 - Public Administrati (13); FY17 - Central Government (Central Agenc (12) |
| `mjsector_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 188 | ["FY17 - Public Administration - Transpo (14); ["FY17 - Health", "FY17 - Public Adminis (13); ["FY17 - Central Government (Central Age (12) |
| `p2a_flag` | str | 2.54 | 97.46 | 0.0 | 0.0 | 1 | Y (7) |
| `p2a_updated_date` | str:date[YYYY-MM-DD] | 2.54 | 97.46 | 0.0 | 0.0 | 6 | 2025-05-21 → 2026-03-19 |
| `parentprojid` | str | 0.36 | 99.64 | 0.0 | 0.0 | 1 (unique) | P155047 (1) |
| `parentprojid_exact` | str | 0.36 | 99.64 | 0.0 | 0.0 | 1 (unique) | P155047 (1) |
| `pdo` | str | 95.65 | 4.35 | 0.0 | 0.0 | 264 (unique) | The objective of the Project is to impro (1); The objective of the Project is to impro (1); (a) to improve the level of service prov (1) |
| `prodline_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | PE (203); RE (61); GE (7) |
| `proj_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 276 (unique) | P099270 (1); P106284 (1); P111996 (1) |
| `proj_last_upd_date` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 188 | 2020-12-23 → 2025-08-18 |
| `project_abstract` | str, str:multiline | 81.88 | 18.12 | 0.0 | 0.0 | 225 | The objective of the Pacific Islands Reg (2); The development objective of he South We (1); The Cadastre Modernization Project for T (1) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 275 | Implementation of the International Comp (2); SOUTH-WEST ROADS:  WESTERN EUROPE-WESTER (1); Turkey Land Registration and Cadastre Mo (1) |
| `projectfinancialtype` | list | 99.28 | 0.72 | 0.0 | 0.0 | 36 | ["Grants"] (51); ["IDA"] (46); ["IBRD", "Other"] (36) |
| `projectfinancialtype_exact` | str | 99.28 | 0.72 | 0.0 | 0.0 | 36 | Grants (51); IDA (46); IBRD,Other (36) |
| `projid_id_display` | str | 100.0 | 0.0 | 0.0 | 0.0 | 276 (unique) | P099270 (1); P106284 (1); P111996 (1) |
| `public_disclosure_date` | str:date[YYYY-MM-DD] | 100.0 | 0.0 | 0.0 | 0.0 | 266 | 2007-07-31 → 2025-04-22 |
| `regionhomepageurl` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7 | http://www.worldbank.org/afr (74); http://www.worldbank.org/eca (37); https://www.worldbank.org/lac (37) |
| `regionname` | str | 100.0 | 0.0 | 0.0 | 0.0 | 8 | Europe and Central Asia (37); Latin America and Caribbean (37); Eastern and Southern Africa (37) |
| `regionname_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 8 | Europe and Central Asia (37); Latin America and Caribbean (37); Eastern and Southern Africa (37) |
| `sector_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 188 | FY17 - Public Administration - Transport (14); FY17 - Health,FY17 - Public Administrati (13); FY17 - Central Government (Central Agenc (12) |
| `sector_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 188 | FY17 - Public Administration - Transport (14); FY17 - Health,FY17 - Public Administrati (13); FY17 - Central Government (Central Agenc (12) |
| `sector_percent` | str, str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | 174 | 100.0 (38); 1.0,99.0 (9); 10.0,90.0 (7) |
| `sectorcode` | str | 100.0 | 0.0 | 0.0 | 0.0 | 188 | TF,TI (14); HF,HG (13); BC (12) |
| `sectorcode_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 188 | TF,TI (14); HF,HG (13); BC (12) |
| `status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | Closed (145); Active (130); Dropped (1) |
| `status_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 3 | ["Closed"] (145); ["Active"] (130); ["Dropped"] (1) |
| `teamleaderupi` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 250 | 00000352122 (3); 00000399276 (3); 00000268001 (3) |
| `teamleadname` | list | 100.0 | 0.0 | 0.0 | 0.0 | 250 | ["Haileyesus Adamtei "] (3); ["Ketevan Nozadze "] (3); ["Fabio Pittaluga "] (3) |
| `theme` | str | 100.0 | 0.0 | 0.0 | 0.0 | 266 | FY17 - Climate change,FY17 - Environment (2); FY17 - Job Creation,FY17 - Jobs,FY17 - P (2); FY17 - Access to Education,FY17 - Educat (2) |
| `theme_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 266 | ["FY17 - Climate change", "FY17 - Enviro (2); ["FY17 - Job Creation", "FY17 - Jobs", " (2); ["FY17 - Access to Education", "FY17 - E (2) |
| `theme_list` | list | 100.0 | 0.0 | 0.0 | 0.0 | 274 | [{"code": "60", "name": "FY17 - Human De (2); [{"code": "40", "name": "FY17 - Public S (2); [{"code": "10", "name": "FY17 - Economic (1) |
| `themecode` | str | 100.0 | 0.0 | 0.0 | 0.0 | 266 | 80,81,811,84 (2); 20,22,221,70,71,711,72,723 (2); 60,65,651,652 (2) |
| `themecode_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 266 | ["80", "81", "811", "84"] (2); ["20", "22", "221", "70", "71", "711", " (2); ["60", "65", "651", "652"] (2) |
| `themev2_level1_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 237 | ["FY17 - Environment and Natural Resourc (6); ["FY17 - Public Sector Management", "FY1 (3); ["FY17 - Environment and Natural Resourc (3) |
| `themev2_level2_exact` | list | 100.0 | 0.0 | 0.0 | 0.0 | 264 | ["FY17 - Climate change", "FY17 - Enviro (2); ["FY17 - Jobs", "FY17 - Rural Developmen (2); ["FY17 - Education", "FY17 - Education"] (2) |
| `themev2_level3_exact` | list | 99.64 | 0.36 | 0.0 | 0.0 | 263 | ["FY17 - Mitigation"] (3); ["FY17 - Job Creation", "FY17 - Rural In (2); ["FY17 - Access to Education", "FY17 - E (2) |
| `totalamt` | str:int | 73.55 | 26.45 | 0.0 | 0.0 | 120 | 50000000 (11); 300000000 (10); 150000000 (9) |
