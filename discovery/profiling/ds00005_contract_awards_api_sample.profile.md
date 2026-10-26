### ds00005_contract_awards_api_sample

Source: Finances One DS00005 API. Rows profiled: 1000

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `as_of_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 2026-10-20 → 2026-10-20 |
| `borrower_contract_reference_number` | str, str:int | 100.0 | 0.0 | 0.0 | 0.0 | 892 | IN-DPO, KANGRA-506517-CW-RFQ (13); PY-MSPBS-244916-GO-RFB (12); IN-DPO, HAMIRPUR-368169-CW-RFQ (5) |
| `borrower_country` | str | 100.0 | 0.0 | 0.0 | 0.0 | 114 | India (99); Western and Central Africa (50); Bangladesh (40) |
| `borrower_country_code` | str | 97.6 | 2.4 | 0.0 | 0.0 | 109 | IN (99); 3W (50); BD (40) |
| `contract_description` | str, str:multiline | 100.0 | 0.0 | 0.0 | 0.0 | 884 | SSIII ONE STEP RT-PCR KIT (500RX) [30 NO (19); National telephone and face-to-face inte (8); 10 Facilitadores UOR Riberalta (aproxima (8) |
| `contract_signed___calendar_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 8 | 2026 (191); 2020 (162); 2023 (148) |
| `contract_signing_date` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 549 | 2019-07-16 → 2026-10-09 |
| `fiscal_year` | int | 100.0 | 0.0 | 0.0 | 0.0 | 8 | 2020 (125); 2021 (125); 2022 (125) |
| `procurement_category` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | Consultant Services (456); Goods (284); Works (192) |
| `procurement_method` | str | 100.0 | 0.0 | 0.0 | 0.0 | 12 | Request for Quotations (288); Individual Consultant Selection (257); Request for Bids (192) |
| `project_global_practice` | str | 86.0 | 14.0 | 0.0 | 0.0 | 101 | Agriculture (72); Health (72); Agriculture;Industry & Trade/Ser (69) |
| `project_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 411 | P165129 (27); P124015 (23); P163742 (21) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 410 | Integrated Project for Source Sustainabi (27); Social Safety Net Project (23); Climate Smart Irrigated Agriculture Proj (21) |
| `region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 7 | Western and Central Africa (233); Eastern and Southern Africa (190); South Asia (186) |
| `review_type` | str | 98.8 | 1.2 | 0.0 | 0.0 | 2 | Post (874); Prior (114) |
| `supplier` | str | 100.0 | 0.0 | 0.0 | 0.0 | 659 | INDIVIDUAL CONSULTANT (257); NIRAV BIOSOLUTIONS PVT LTD. (19); VISIONARY KNOWLEDGE AND MANAGEMENT SERVI (4) |
| `supplier_contract_amount_usd` | float, int | 100.0 | 0.0 | 0.0 | 0.0 | 863 | 1266.8 (23); 66870.75 (19); 2666.67 (8) |
| `supplier_country` | str | 99.9 | 0.0 | 0.0 | 0.1 | 122 | India (106); Nigeria (37); Bangladesh (37) |
| `supplier_country_code` | str | 99.9 | 0.0 | 0.0 | 0.1 | 122 | IN (106); NG (37); BD (37) |
| `supplier_id` | str:int, str | 100.0 | 0.0 | 0.0 | 0.0 | 913 | 543422 (19); 466599 (4); 409835 (3) |
| `wb_contract_number` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 957 | 1893771 (4); 1893773 (4); 1893778 (4) |
