### projects_v2_population

Source: search API v2 projects (selected fields; frozen index). Rows profiled: 22729

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `approvalfy` | str:int | 69.17 | 30.83 | 0.0 | 0.0 | 79 | 2010 (433); 2007 (433); 2011 (411) |
| `boardapprovaldate` | str:date[ISO8601Z] | 69.17 | 30.83 | 0.0 | 0.0 | 5418 | 1947-05-09 → 2024-12-20 |
| `borrower` | str | 26.04 | 73.96 | 0.0 | 0.0 | 1948 | Ministry of Finance (532); Ministry of Economy and Finance (125); People's Republic of China (116) |
| `cancellations` | list | 26.84 | 73.16 | 0.0 | 0.0 | 3559 | ["0"] (1951); ["0", "0"] (401); ["0", "0", "0"] (86) |
| `ccsa_practice_name` | list | 13.23 | 86.77 | 0.0 | 0.0 | 41 | ["Jobs"] (1887); ["Infrastructure, PPP's & Guarantees"] (552); ["Climate Change"] (94) |
| `closingdate` | str:date[M/D/YYYY h:mm:ss AM] | 68.08 | 31.92 | 0.0 | 0.0 | 1796 | 1947-12-31 → 2039-12-13 |
| `cons_serv_reqd_ind` | str | 6.87 | 93.13 | 0.0 | 0.0 | 3 | Y (1013); N (312); TBD (236) |
| `countrycode` | list | 87.45 | 12.55 | 0.0 | 0.0 | 209 | ["IN"] (882); ["ID"] (593); ["BR"] (566) |
| `countryname` | list | 87.45 | 12.55 | 0.0 | 0.0 | 209 | ["Republic of India"] (882); ["Republic of Indonesia"] (593); ["Federative Republic of Brazil"] (566) |
| `countryshortname` | str | 87.45 | 12.55 | 0.0 | 0.0 | 209 | India (882); Indonesia (593); Brazil (566) |
| `curr_ibrd_commitment` | str:int, str:decimal | 87.47 | 12.53 | 0.0 | 0.0 | 1415 | 0 (11965); 100 (408); 50 (292) |
| `curr_ida_commitment` | str:int, str:decimal | 87.47 | 12.53 | 0.0 | 0.0 | 1323 | 0 (11107); 50 (337); 20 (316) |
| `curr_total_commitment` | str:int, str:decimal | 87.47 | 12.53 | 0.0 | 0.0 | 3792 | 0 (949); 100 (664); 50 (541) |
| `envassesmentcategorycode` | str | 50.41 | 49.59 | 0.0 | 0.0 | 9 | B (5316); C (4334); A (1112) |
| `esrc_ovrl_risk_rate` | str | 8.06 | 91.94 | 0.0 | 0.0 | 4 | Substantial (710); Moderate (694); High (215) |
| `grantamt` | str:thousands-separated-number, str:int | 9.75 | 90.25 | 0.0 | 0.0 | 885 | 0 (246); 5,000,000 (61); 10,000,000 (51) |
| `ibrdcommamt` | str:int, str:thousands-separated-number | 87.47 | 12.53 | 0.0 | 0.0 | 1307 | 0 (12002); 100,000,000 (417); 50,000,000 (297) |
| `id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 22729 (unique) | P000001 (1); P000003 (1); P000010 (1) |
| `idacommamt` | str:int, str:thousands-separated-number | 87.47 | 12.53 | 0.0 | 0.0 | 1193 | 0 (11136); 50,000,000 (347); 20,000,000 (330) |
| `impagency` | str | 26.89 | 73.11 | 0.0 | 0.0 | 4366 | Ministry of Finance (192); Ministry of Health (143); Ministry of Economy and Finance (68) |
| `last_stage_reached_name` | str | 58.28 | 41.72 | 0.0 | 0.0 | 5 | Bank Approved (11470); Concept Review (1235); Begin Appraisal (223) |
| `lendinginstr` | str | 86.59 | 13.41 | 0.0 | 0.0 | 18 | Investment Project Financing (7110); Specific Investment Loan (6217); Sector Investment and Maintenance Loan (1197) |
| `lendinginstrcode` | str | 86.59 | 13.41 | 0.0 | 0.0 | 18 | IPF (7110); SIL (6217); SIM (1197) |
| `lendinginstrtype` | str | 86.59 | 13.41 | 0.0 | 0.0 | 4 | IN (17186); AD (2029); PR (418) |
| `lendprojectcost` | str:thousands-separated-number, str:int | 86.77 | 13.23 | 0.0 | 0.0 | 4311 | 100,000,000 (488); 0 (486); 50,000,000 (471) |
| `loanid` | list | 46.72 | 53.28 | 0.0 | 0.0 | 10596 | ["A0350"] (2); ["37930"] (2); ["35430"] (2) |
| `mainloan` | str:int, str | 44.27 | 12.53 | 0.0 | 43.2 | 8882 | N/A (9819); 34760 (2); 44730 (2) |
| `p2a_updated_date` | str:date[YYYY-MM-DD hh:mm:ss.f] | 55.65 | 44.35 | 0.0 | 0.0 | 1389 | 2017-03-17 → 2025-01-20 |
| `parentprojid` | list | 9.7 | 90.3 | 0.0 | 0.0 | 1844 | ["P000621"] (9); ["P000622"] (7); ["P000623"] (7) |
| `pdo` | str, str:multiline | 44.45 | 55.53 | 0.0 | 0.01 | 8861 | Project Development Objective (Note: wil (14); DON'T PUB DROPPED (8); TBD (7) |
| `prodline` | str | 87.47 | 12.53 | 0.0 | 0.0 | 10 | PE (16501); RE (2021); GE (561) |
| `prodlinetext` | str | 87.47 | 12.53 | 0.0 | 0.0 | 10 | IBRD/IDA (16501); Recipient Executed Activities (2021); Global Environment Project (561) |
| `productlinetype` | str | 87.47 | 12.53 | 0.0 | 0.0 | 1 | L (19880) |
| `proj_last_upd_date` | str:date[ISO8601Z] | 59.94 | 40.06 | 0.0 | 0.0 | 2306 | 2013-01-15 → 2025-01-03 |
| `project_abstract` | object | 53.09 | 46.91 | 0.0 | 0.0 | 11927 | {"cdata!": "The Second Loan Administrati (6); {"cdata!": "The objective of the Acceler (6); {"cdata!": "The Loan Administration Repo (5) |
| `project_gp_info` | str | 58.86 | 41.14 | 0.0 | 0.0 | 13378 (unique) | <PROJECT_GP_MAPPING PROJECTID="P000001"> (1); <PROJECT_GP_MAPPING PROJECTID="P000003"> (1); <PROJECT_GP_MAPPING PROJECTID="P000017"> (1) |
| `project_name` | str | 87.47 | 12.53 | 0.0 | 0.0 | 17200 | Education Project (02) (70); Highway Project (03) (51); Education Project (03) (46) |
| `projectstatusdisplay` | str | 87.45 | 12.55 | 0.0 | 0.0 | 4 | Closed (14602); Active (2666); Dropped (1909) |
| `recent_close_date1` | str:date[M/D/YYYY h:mm:ss AM] | 26.84 | 73.16 | 0.0 | 0.0 | 787 | 1975-06-30 → 2033-12-31 |
| `regionname` | str | 87.45 | 12.55 | 0.0 | 0.0 | 9 | Latin America and Caribbean (3562); Eastern and Southern Africa (3246); East Asia and Pacific (3074) |
| `rel_loan_ids` | list | 69.5 | 30.5 | 0.0 | 0.0 | 14474 | ["IBRD04281, IBRD04282, IBRD04283, IBRD0 (10); ["IBRD04830, IBRD04831, IBRD04832, IBRD0 (8); ["IBRD06750, IBRD06751, IBRD06752, IBRD0 (8) |
| `sector1` | object | 87.47 | 12.53 | 0.0 | 0.0 | 3320 | {"Name": "", "Percent": 0} (2119); {"Name": "(Historic)Highways", "Percent" (478); {"Name": "(Historic)Agriculture adjustme (394) |
| `sector2` | object | 43.78 | 56.22 | 0.0 | 0.0 | 2601 | {"Name": "Central Government (Central Ag (92); {"Name": "Central Government (Central Ag (75); {"Name": "Central Government (Central Ag (62) |
| `sector3` | object | 29.46 | 70.54 | 0.0 | 0.0 | 1796 | {"Name": "Social Protection", "Percent": (63); {"Name": "Central Government (Central Ag (60); {"Name": "Central Government (Central Ag (57) |
| `source` | list | 100.0 | 0.0 | 0.0 | 0.0 | 1 | ["IBRD"] (22729) |
| `status` | str | 87.45 | 12.55 | 0.0 | 0.0 | 4 | Closed (14602); Active (2666); Dropped (1909) |
| `supplementprojectflg` | str | 85.31 | 14.69 | 0.0 | 0.0 | 2 | N (17491); Y (1899) |
| `themev2_level1_exact` | list | 55.95 | 44.05 | 0.0 | 0.0 | 242 | ["Environment and Natural Resource Manag (610); ["Human Development and Gender"] (610); ["Environment and Natural Resource Manag (572) |
| `totalamt` | str:thousands-separated-number, str:int | 87.47 | 12.53 | 0.0 | 0.0 | 1891 | 0 (3704); 100,000,000 (715); 50,000,000 (618) |
| `totalcommamt` | str:thousands-separated-number, str:int | 100.0 | 0.0 | 0.0 | 0.0 | 2450 | 0 (5410); 100,000,000 (692); 50,000,000 (588) |
| `url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 22729 (unique) | https://projects.worldbank.org/en/projec (1); https://projects.worldbank.org/en/projec (1); https://projects.worldbank.org/en/projec (1) |
