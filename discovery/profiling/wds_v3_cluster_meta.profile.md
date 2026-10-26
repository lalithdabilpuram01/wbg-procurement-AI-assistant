### wds_v3_cluster_meta

Source: search API v3 wds (cluster projects). Rows profiled: 22337

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `EnvironmentalCategory` | str | 60.6 | 39.4 | 0.0 | 0.0 | 14 | B (9921); A (2307); C (1119) |
| `abstracts` | object | 6.33 | 93.67 | 0.0 | 0.0 | 882 | {"cdata!": "The development objective of (63); {"cdata!": "El proyecto Programa de Apoy (53); {"cdata!": "The Project Development Obje (25) |
| `admreg` | str | 99.64 | 0.36 | 0.0 | 0.0 | 62 | South Asia (4446); Eastern and Southern Africa (3388); Latin America and Caribbean (3228) |
| `alt_title` | object | 2.25 | 97.75 | 0.0 | 0.0 | 167 | {"0": {"alt_title": "Colombia - Lineamie (53); {"0": {"alt_title": "Ecuador - Proyecto  (25); {"0": {"alt_title": "Colombia - Apoyo al (24) |
| `authors` | object | 93.96 | 6.04 | 0.0 | 0.0 | 2831 | {"0": {"author": "Borrowing Agency"}} (1077); {"0": {"author": "LEGKL"}} (758); {"0": {"author": "World Bank"}} (504) |
| `available_in` | str | 99.94 | 0.0 | 0.06 | 0.0 | 34 | English (21433); Spanish (307); French (295) |
| `bdmdt` | str:date[ISO8601Z] | 1.26 | 98.74 | 0.0 | 0.0 | 200 | 2012-01-26 → 2026-09-04 |
| `chronical_docm_id` | str | 99.99 | 0.01 | 0.0 | 0.0 | 22326 | BOSIB-f4e6400f-2ce3-490d-872c-da1de4cbfd (3); 090224b082c25786 (2); BOSIB-886f2c77-3446-4e62-b744-9ea8a18051 (2) |
| `closedt` | str:date[ISO8601Z] | 1.13 | 98.87 | 0.0 | 0.0 | 206 | 2013-04-25 → 2026-10-08 |
| `colti` | str | 0.53 | 99.47 | 0.0 | 0.0 | 22 | Board Minutes (55); Board minutes (11); Feature Story (10) |
| `count` | str | 99.91 | 0.09 | 0.0 | 0.0 | 255 | India (2429); Mozambique (795); Brazil (693) |
| `datestored` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 12779 | 2007-07-31 → 2026-10-20 |
| `disclosure_date` | str:date[ISO8601Z] | 99.78 | 0.22 | 0.0 | 0.0 | 12687 | 2007-08-09 → 2026-10-20 |
| `disclosure_type` | str | 0.11 | 99.89 | 0.0 | 0.0 | 1 | Simultaneous Disclosure (24) |
| `disclstat` | str | 97.95 | 2.05 | 0.0 | 0.0 | 1 | Disclosed (21878) |
| `display_title` | str, str:multiline | 100.0 | 0.0 | 0.0 | 0.0 | 9485 | India - SOUTH ASIA- P152698- National Hy (694); India - SOUTH ASIA - P152698 - National  (286); Bolivia - LATIN AMERICA AND CARIBBEAN- P (219) |
| `docdt` | str:date[ISO8601Z], str | 100.0 | 0.0 | 0.0 | 0.0 | 3946 | 2002-01-01 → 2026-12-08 |
| `docna` | object | 100.0 | 0.0 | 0.0 | 0.0 | 9327 | {"0": {"docna": "India - SOUTH ASIA- P15 (694); {"0": {"docna": "India - SOUTH ASIA - P1 (286); {"0": {"docna": "Bolivia - LATIN AMERICA (219) |
| `docty` | str | 99.83 | 0.17 | 0.0 | 0.0 | 77 | Procurement Plan (11244); Implementation Status and Results Report (2947); Auditing Document (2032) |
| `dois` | str | 0.02 | 99.98 | 0.0 | 0.0 | 5 (unique) | 10.1596/1813-9450-10230 (1); 10.1596/978-1-4648-1840-0 (1); 10.1596/1813-9450-10841 (1) |
| `entityids` | object | 46.17 | 53.83 | 0.0 | 0.0 | 10200 | {"entityid": "HQBOCS"} (110); {"entityid": "AFRLR"} (2); {"entityid": "000350881_20141205102225"} (2) |
| `fullavailablein` | list | 100.0 | 0.0 | 0.0 | 0.0 | 1 | [] (22337) |
| `geo_regions` | object | 6.7 | 93.3 | 0.0 | 0.0 | 65 | {"0": {"geo_region": "Latin America & Ca (261); {"0": {"geo_region": "Asia"}, "1": {"geo (216); {"0": {"geo_region": "Asia"}, "1": {"geo (114) |
| `guid` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 22327 | 099101625140041006 (3); 362221468291332192 (2); 099845006082229648 (2) |
| `historic_topic` | str | 33.9 | 66.1 | 0.0 | 0.0 | 1688 | Health, Nutrition and Population,Industr (303); Governance,Information and Communication (179); Education (141) |
| `id` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 22329 | 40056479 (3); 20474566 (2); 40029314 (2) |
| `isbn` | str | 0.01 | 99.99 | 0.0 | 0.0 | 3 (unique) | 978-1-4648-1840-0 (1); 978-617-7239-08-5 (1); 978-617-7239-07-8 (1) |
| `keywd` | object | 42.31 | 57.69 | 0.0 | 0.0 | 7702 | {"0": {"keywd": "credit market"}} (105); {"0": {"keywd": "accessibility"}, "1": { (54); {"0": {"keywd": "accessibility"}, "1": { (25) |
| `lang` | str | 99.94 | 0.06 | 0.0 | 0.0 | 34 | English (21433); Spanish (307); French (295) |
| `last_modified_date` | str:date[ISO8601Z] | 26.34 | 73.66 | 0.0 | 0.0 | 2702 | 2024-11-08 → 2026-10-21 |
| `lndinstr` | object | 100.0 | 0.0 | 0.0 | 0.0 | 11 | {"0": {"lndinstr": "Investment Project F (21744); {"0": {"lndinstr": "Program-for-Results  (538); {"0": {"lndinstr": "Investment Project F (11) |
| `lndinstr_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 11 | Investment Project Financing (21744); Program-for-Results Financing (538); Investment Project Financing,Specific In (11) |
| `majdocty` | str | 99.48 | 0.52 | 0.0 | 0.0 | 5 | Project Documents (21897); Publications & Research (208); Board Documents (113) |
| `majtheme` | str | 96.03 | 3.97 | 0.0 | 0.0 | 814 | FY17 - Environment and Natural Resource  (1258); FY17 - Urban and Rural Development,FY17  (900); FY17 - Human Development and Gender (826) |
| `ml_abstracts` | str | 0.11 | 99.89 | 0.0 | 0.0 | 6 | NULL$^$^$arabic (11); NULL$^$^$POR (5);    $^$^$russian (4) |
| `origu` | str | 70.83 | 28.94 | 0.0 | 0.23 | 465 | Water South Asia Region (SSAW1) (1084); SAR- Climate Change and DRM (SSACD) (429); Records & Library (LEGRL) (419) |
| `owner` | str | 40.46 | 58.83 | 0.0 | 0.71 | 437 | Water South Asia Region (SSAW1) (508); Education LCR (HLCED) (244); Transport-SAR (ISAT1) (194) |
| `pdfurl` | str | 99.54 | 0.46 | 0.0 | 0.0 | 22227 | https://documents.worldbank.org/curated/ (3); http://documents.worldbank.org/curated/e (2); https://documents.worldbank.org/curated/ (2) |
| `prdln` | str | 100.0 | 0.0 | 0.0 | 0.0 | 26 | IBRD/IDA (19035); Recipient Executed Activities (2448); Global Environment Project (534) |
| `prdln_exact` | str | 100.0 | 0.0 | 0.0 | 0.0 | 26 | IBRD/IDA (19035); Recipient Executed Activities (2448); Global Environment Project (534) |
| `projectid` | str | 100.0 | 0.0 | 0.0 | 0.0 | 396 | P152698 (1308); P152309 (351); P148591 (328) |
| `projn` | str | 100.0 | 0.0 | 0.0 | 0.0 | 397 | IN-National Hydrology Project -- P152698 (1308); VN-Vietnam Dam Rehabilitation and Safety (351); ET-Second Agricultural Growth Project -- (328) |
| `repnb` | str, str:int | 79.85 | 20.15 | 0.0 | 0.0 | 16756 | SFG1121 (63); E2686 (53); ISR60169 (26) |
| `repnme` | object | 40.83 | 59.17 | 0.0 | 0.0 | 3578 | {"repnme": "India - SOUTH ASIA- P152698- (425); {"repnme": "India/SOUTH ASIA- P152698- N (163); {"repnme": "Bolivia - LATIN AMERICA AND  (162) |
| `seccl` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1 | Public (22337) |
| `sectr` | object | 100.0 | 0.0 | 0.0 | 0.0 | 253 | {"0": {"sector": "Transportation"}} (1897); {"0": {"sector": "Health"}} (1486); {"0": {"sector": "Agriculture, Fishing a (1259) |
| `subsc` | str | 100.0 | 0.0 | 0.0 | 0.0 | 471 | FY17 - Other Water Supply, Sanitation an (1258); FY17 - Health Facilities and Constructio (785); FY17 - Rural and Inter-Urban Roads,FY17  (756) |
| `subtopic` | str | 42.18 | 57.82 | 0.0 | 0.0 | 3926 | De Facto Governments,Democratic Governme (107); De Facto Governments,Democratic Governme (75); Agribusiness,Business Cycles and Stabili (71) |
| `teratopic` | str | 33.9 | 66.1 | 0.0 | 0.0 | 1688 | Health, Nutrition and Population,Industr (303); Governance,Information and Communication (179); Education (141) |
| `theme` | str | 100.0 | 0.0 | 0.0 | 0.0 | 604 | FY17 - Disaster Risk Management,FY17 - D (1258); FY17 - Disaster Response and Recovery,FY (492); FY17 - Gender,FY17 - Finance for Develop (327) |
| `totvolnb` | str:int, str | 40.82 | 59.18 | 0.0 | 0.0 | 19 | 1 (8453); 3 (114); 2 (89) |
| `trustfund` | str | 21.52 | 78.48 | 0.0 | 0.0 | 473 | TF016291 - BRB-IN-Basin Focus Area; TF01 (280); TF016291 - BRB-IN-Basin Focus Area; TF01 (117); TF0B0807 - Enhancing the Resilience of D (90) |
| `txturl` | str | 99.54 | 0.46 | 0.0 | 0.0 | 22227 | https://documents.worldbank.org/curated/ (3); http://documents.worldbank.org/curated/e (2); https://documents.worldbank.org/curated/ (2) |
| `unregnbr` | str, str:int | 0.05 | 99.95 | 0.0 | 0.0 | 4 | LICUN (9); 47055 (1); 45247 (1) |
| `url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 22327 | http://documents.worldbank.org/curated/e (3); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `versiontyp` | str, str:int | 61.11 | 38.89 | 0.0 | 0.0 | 21 | Final (10718); Buff cover (2275); Buff Cover (314) |
| `virt_coll` | str | 0.01 | 99.99 | 0.0 | 0.0 | 1 | Results Profile (2) |
| `volnb` | str:int | 45.21 | 54.79 | 0.0 | 0.0 | 57 | 1 (9557); 2 (143); 3 (87) |
