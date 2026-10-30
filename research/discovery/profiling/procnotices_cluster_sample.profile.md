### procnotices_cluster_sample

Source: search API procnotices (project cluster). Rows profiled: 30977

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `bid_description` | str, str:multiline | 99.25 | 0.75 | 0.0 | 0.0 | 19653 | 5to REQUERIMIENTO DE RECURSOS HUMANOS EN (110); 2do REQUERIMIENTO DE RECURSOS HUMANOS EN (109); 2do REQUERIMIENTO DE RECURSOS HUMANOS EN (96) |
| `bid_reference_no` | str, str:decimal, str:int | 99.25 | 0.75 | 0.0 | 0.0 | 20733 | BM/AISEM/195/2020 (110); BM/AISEM/156/2020 (109); BM/AISEM/132/2020 (96) |
| `contact_ctry_name` | str | 23.48 | 76.52 | 0.0 | 0.0 | 110 | India (1037); Brazil (562); Pakistan (361) |
| `contact_organization` | str | 23.58 | 76.42 | 0.0 | 0.0 | 824 | INTERNATIONAL CONSERVANCY (322); Ministry of Health (132); Getulio Vargas Foundation (119) |
| `id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 30977 (unique) | OP00273941 (1); OP00264775 (1); OP00209847 (1) |
| `notice_lang_name` | str | 99.97 | 0.03 | 0.0 | 0.0 | 4 | English (22056); Spanish; Castilian (4091); French (3470) |
| `notice_status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | Published (30845); Revised (117); Draft (11) |
| `notice_text` | str, str:multiline | 99.4 | 0.6 | 0.0 | 0.0 | 30064 | <p><strong>Government of West Bengal</st (31); <p><strong>Sudurpashchim Province Govern (27); <p><strong>HOW TO APPLY</strong></p><p>T (27) |
| `notice_type` | str | 100.0 | 0.0 | 0.0 | 0.0 | 5 | Contract Award (23672); Request for Expression of Interest (3956); Invitation for Bids (3070) |
| `noticedate` | str:date[DD-Mon-YYYY] | 99.96 | 0.04 | 0.0 | 0.0 | 3375 | 2009-02-23 → 2026-10-19 |
| `procurement_group` | str | 97.66 | 2.34 | 0.0 | 0.0 | 4 | CS (12598); GO (8573); CW (5001) |
| `procurement_method_code` | str | 99.25 | 0.75 | 0.0 | 0.0 | 17 | INDV (7211); RFB (6943); RFQ (6881) |
| `procurement_method_name` | str | 99.25 | 0.75 | 0.0 | 0.0 | 16 | Individual Consultant Selection (7211); Request for Bids (6943); Request for Quotations (6881) |
| `project_ctry_name` | str | 99.96 | 0.04 | 0.0 | 0.0 | 112 | India (3741); Bolivia (2894); Mozambique (1258) |
| `project_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 272 | P164453 (2531); P152698 (1707); P177876 (1251) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 281 | Health Service Delivery Network Project (2531); National Hydrology Project (1707); West Bengal Accelerated Development of M (1027) |
| `submission_date` | str:date[ISO8601Z] | 99.96 | 0.04 | 0.0 | 0.0 | 3375 | 2009-02-23 → 2026-10-19 |
| `submission_deadline_date` | str:date[ISO8601Z] | 21.66 | 78.34 | 0.0 | 0.0 | 2211 | 2016-08-28 → 2026-11-30 |
| `submission_deadline_time` | str | 21.66 | 78.34 | 0.0 | 0.0 | 72 | 10:00 (770); 17:00 (726); 18:00 (647) |
