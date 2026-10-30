### procnotices_global_sample

Source: search API procnotices (stratified by notice_type). Rows profiled: 800

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `bid_description` | str, str:multiline | 87.38 | 12.62 | 0.0 | 0.0 | 658 | 4to REQUERIMIENTO DE RECURSOS HUMANOS EN (19); Contratação de consultoria individual, 2 (5); Procurement of polythene tube for 20 wor (4) |
| `bid_reference_no` | str, str:int | 87.0 | 12.62 | 0.0 | 0.38 | 663 | BM/AISEM/183/2020 (19); ET-W-AMHARA-325520-GO-RFQ (4); GA-PRODECE-145505-GO-RFQ (3) |
| `contact_address` | str, str:multiline | 74.75 | 25.25 | 0.0 | 0.0 | 440 | Sethsiripaya Stage II, Kotte - Bope Rd,  (14); Federal Government of Somalia Ministry o (9); Saudamini, Plot No.2, Sector-29, Gurgao (6) |
| `contact_ctry_name` | str | 74.38 | 25.62 | 0.0 | 0.0 | 108 | India (54); Pakistan (25); Cote d'Ivoire (19) |
| `contact_email` | str | 74.75 | 25.25 | 0.0 | 0.0 | 413 | pdeccd@gmail.com (14); pwd.wb.uk@gmail.com (11); nur.ali.mohamud@gmail.com (9) |
| `contact_name` | str | 74.38 | 25.25 | 0.0 | 0.38 | 426 | Ravi Ravi Prasanna Nanayakkara (14); Nur Ali  Mohamud (9); Mahamat ABICHO (8) |
| `contact_organization` | str | 74.75 | 25.25 | 0.0 | 0.0 | 394 | Ministry of Women and Child Affairs (14); PIU (Roads & Bridge) (11); Federal Government of Somalia Ministry o (9) |
| `contact_phone_no` | str, str:int | 74.62 | 25.25 | 0.0 | 0.12 | 453 | 01352608815 (11); 695771337 (8); 077 7749361 (7) |
| `contact_web_url` | str | 31.25 | 68.75 | 0.0 | 0.0 | 160 | www.childwomenmin.gov.lk (14); www.uktenders.gov.in (10); www.moh.gov.so (8) |
| `id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 768 | OP00060856 (2); OP00060638 (2); OP00060381 (2) |
| `notice_lang_name` | str | 99.62 | 0.38 | 0.0 | 0.0 | 4 | English (578); French (148); Spanish; Castilian (47) |
| `notice_status` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3 | Published (775); Revised (22); Cancelled (3) |
| `notice_text` | str, str:multiline | 95.88 | 4.12 | 0.0 | 0.0 | 715 | <p><strong>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp (10); <table align="left" cellspacing="0" styl (4); <table align="left" cellspacing="0" styl (4) |
| `notice_type` | str | 100.0 | 0.0 | 0.0 | 0.0 | 5 | Contract Award (200); Request for Expression of Interest (200); Invitation for Bids (200) |
| `noticedate` | str:date[DD-Mon-YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 206 | 2010-08-03 → 2025-10-15 |
| `procurement_group` | str | 68.75 | 31.25 | 0.0 | 0.0 | 4 | CS (220); GO (146); CW (145) |
| `procurement_method_code` | str | 87.38 | 12.62 | 0.0 | 0.0 | 15 | RFB (180); INDV (134); RFQ (98) |
| `procurement_method_name` | str | 87.38 | 12.62 | 0.0 | 0.0 | 14 | Request for Bids (180); Individual Consultant Selection (134); Request for Quotations (98) |
| `project_ctry_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 118 | India (68); Western and Central Africa (46); Eastern and Southern Africa (31) |
| `project_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 506 | P164453 (19); P151916 (14); P146653 (11) |
| `project_name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 507 | Health Service Delivery Network Project (19); Early Childhood Development Project (14); Uttarakhand Disaster Recovery Project (11) |
| `submission_date` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 206 | 2010-08-03 → 2025-10-15 |
| `submission_deadline_date` | str:date[ISO8601Z] | 43.25 | 56.75 | 0.0 | 0.0 | 173 | 2016-10-04 → 2025-12-15 |
| `submission_deadline_time` | str | 43.25 | 56.75 | 0.0 | 0.0 | 36 | 10:00 (54); 15:00 (41); 16:00 (34) |
