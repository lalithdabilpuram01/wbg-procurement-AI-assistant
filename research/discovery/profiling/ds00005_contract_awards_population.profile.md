### ds00005_contract_awards_population

Source: Finances One DS00005 bulk CSV. Rows profiled: 295058

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `As of Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 1 | 2026-10-20 → 2026-10-20 |
| `Borrower Contract Reference Number` | str, str:int, str:decimal, str:date[YYYY-MM-DD], str:thousands-separated-number, str:multiline | 100.0 | 0.0 | 0.0 | 0.0 | ≥200001 (capped) | MZ-UEM- FA-474115-GO-RFB (188); SB-NSO-412651-NC-RFQ (133); SVG-HDSD-CS-INDV-34A (102) |
| `Borrower Country / Economy` | str | 100.0 | 0.0 | 0.0 | 0.0 | 159 | India (35005); Western and Central Africa (14400); Ethiopia (12787) |
| `Borrower Country / Economy Code` | str, str:int | 96.63 | 0.0 | 3.37 | 0.0 | 146 | IN (35005); 3W (14400); ET (12787) |
| `Contract Description` | str, str:multiline, str:int, str:thousands-separated-number, str:date[DD-Mon-YYYY] | 99.88 | 0.0 | 0.12 | 0.0 | ≥200000 (capped) | Enumerador  (284); Procurement Specialist (188); Enumeration for Population and Housing C (159) |
| `Contract Signing Date` | str:date[MM/DD/YYYY] | 100.0 | 0.0 | 0.0 | 0.0 | 2658 | 2019-07-01 → 2026-10-13 |
| `Contract signed - Calendar year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 8 | 2024 (47532); 2023 (44966); 2021 (42360) |
| `Fiscal Year` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 8 | 2024 (45961); 2025 (45870); 2021 (44923) |
| `Procurement Category` | str | 100.0 | 0.0 | 0.0 | 0.0 | 4 | Consultant Services (112594); Goods (92657); Works (66281) |
| `Procurement Method` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18 | Request for Quotations (106532); Individual Consultant Selection (65121); Request for Bids (56799) |
| `Project Global Practice` | str | 89.73 | 0.0 | 10.27 | 0.0 | 241 | Agriculture (34281); Agriculture;Industry & Trade/Ser (21411); Health (20481) |
| `Project ID` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3155 | P165129 (15325); P158522 (3661); P151072 (3034) |
| `Project Name` | str | 100.0 | 0.0 | 0.0 | 0.0 | 3146 | Integrated Project for Source Sustainabi (15325); Tamil Nadu Irrigated Agriculture Moderni (3661); National Agricultural Higher Education P (3034) |
| `Region` | str | 100.0 | 0.0 | 0.0 | 0.0 | 8 | Eastern and Southern Africa (68356); Western and Central Africa (62888); South Asia (50733) |
| `Review type` | str | 99.94 | 0.0 | 0.06 | 0.0 | 2 | Post (273808); Prior (21062) |
| `Supplier` | str, str:int | 100.0 | 0.0 | 0.0 | 0.0 | 124823 | INDIVIDUAL CONSULTANT (65121); SRS FERTILIZERS CORPORATION (728); UNITED NATIONS OFFICE PROJECT SERVICES ( (524) |
| `Supplier Contract Amount (USD)` | str:decimal | 100.0 | 0.0 | 0.0 | 0.0 | ≥200000 (capped) | 30000.00 (362); 7380.00 (318); 15000.00 (296) |
| `Supplier Country / Economy` | str | 99.77 | 0.0 | 0.01 | 0.22 | 230 | India (35853); Ethiopia (14987); Nigeria (9666) |
| `Supplier Country / Economy Code` | str | 99.77 | 0.0 | 0.01 | 0.22 | 230 | IN (35853); ET (14987); NG (9666) |
| `Supplier ID` | str:int, str | 100.0 | 0.0 | 0.0 | 0.0 | 188653 | 430957 (728); 35295 (520); 34990 (311) |
| `WB Contract Number` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | ≥200000 (capped) | 1814624 (42); 1703700 (28); 1743413 (21) |
