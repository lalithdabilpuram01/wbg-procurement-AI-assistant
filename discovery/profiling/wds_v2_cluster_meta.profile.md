### wds_v2_cluster_meta

Source: search API v2 wds (cluster projects). Rows profiled: 18350

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `abstracts` | object | 4.43 | 95.57 | 0.0 | 0.0 | 479 | {"cdata!": "The Support to the National  (53); {"cdata!": "The Support to the National  (24); {"cdata!": "The objectives of the First  (16) |
| `admreg` | str | 99.89 | 0.11 | 0.0 | 0.0 | 57 | South Asia,South Asia (4573); Latin America & Caribbean,Latin America  (2784); Africa,Africa (2612) |
| `available_in` | str | 99.96 | 0.0 | 0.04 | 0.0 | 94 | English (17130); Spanish (278); French (224) |
| `chronical_docm_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | 090224b082c25786 (2); 090224b087fb1f22 (2); BOSIB-edbaff96-ebc5-4ac1-8e90-e2f415130c (2) |
| `count` | str | 100.0 | 0.0 | 0.0 | 0.0 | 253 | India (2186); Mozambique (609); Ethiopia (606) |
| `count_key` | str:int, str | 99.43 | 0.57 | 0.0 | 0.0 | 203 | 82602 (2186); 82527 (609); 82611 (606) |
| `disclosure_date` | str:date[ISO8601Z] | 79.41 | 20.59 | 0.0 | 0.0 | 2792 | 2007-08-09 → 2025-03-11 |
| `disclstat` | str | 97.56 | 2.44 | 0.0 | 0.0 | 1 | Disclosed (17903) |
| `display_title` | str:multiline, str | 100.0 | 0.0 | 0.0 | 0.0 | 8177 | India - SOUTH ASIA- P152698-             (689); India - SOUTH ASIA - P152698 -           (216); Bolivia - LATIN AMERICA AND              (216) |
| `docdt` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 3413 | 2002-01-01 → 2025-03-12 |
| `docm_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | 090224b082c25786 (2); 090224b087fb1f22 (2); BOSIB-edbaff96-ebc5-4ac1-8e90-e2f415130c (2) |
| `docna` | object | 100.0 | 0.0 | 0.0 | 0.0 | 8005 | {"0": {"docna": "India - SOUTH ASIA- P15 (689); {"0": {"docna": "India - SOUTH ASIA - P1 (216); {"0": {"docna": "Bolivia - LATIN AMERICA (216) |
| `docty` | str, str:multiline | 99.83 | 0.17 | 0.0 | 0.0 | 64 | Procurement Plan (9155); Implementation Status and Results Report (2448); Auditing Document (1573) |
| `entityids` | object | 100.0 | 0.0 | 0.0 | 0.0 | 18232 | {"entityid": "HQBOCS"} (113); {"entityid": "AFRLR"} (2); {"entityid": "000350881_20141205102225"} (2) |
| `fullavailablein` | list | 100.0 | 0.0 | 0.0 | 0.0 | 548 | [] (17803); [{"entityid": "000333037_20140321122054" (1); [{"entityid": "000333037_20140321122401" (1) |
| `guid` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | 362221468291332192 (2); 485121605927708392 (2); 099070523151018799 (2) |
| `id` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | 20474566 (2); 32576791 (2); 34112816 (2) |
| `lang` | str | 99.96 | 0.04 | 0.0 | 0.0 | 35 | English (17531); Spanish (301); French (244) |
| `last_modified_date` | str:date[ISO8601Z] | 46.54 | 53.46 | 0.0 | 0.0 | 1286 | 2007-08-01 → 2025-03-12 |
| `listing_relative_url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | /projects/documents/2014/11/20474566/fed (2); /projects/documents/2020/07/32576791/min (2); /projects/documents/2023/05/34112816/min (2) |
| `lndinstr` | object | 49.17 | 50.83 | 0.0 | 0.0 | 17 | {"0": {"lndinstr": "Investment Project F (7648); {"0": {"lndinstr": "Specific Investment  (906); {"0": {"lndinstr": "Program-for-Results" (217) |
| `majdocty` | str | 99.51 | 0.49 | 0.0 | 0.0 | 7 | Project Documents (9945); Project Documents,Project Documents (8056); Publications & Research (172) |
| `new_url` | str | 86.83 | 13.17 | 0.0 | 0.0 | 15929 | 2014/11/20474566/Federated-States-of-Mic (2); 2023/05/34112816/BOSIB0edbaff960c108e900 (2); 2023/07/34133739/BOSIB0dcc91f1f0d5085990 (2) |
| `no_of_pages` | str:int | 73.96 | 26.04 | 0.0 | 0.0 | 450 | 7 (1196); 8 (1088); 6 (1052) |
| `pdfurl` | str | 97.76 | 2.24 | 0.0 | 0.0 | 17934 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `prdln` | str | 55.87 | 44.13 | 0.0 | 0.0 | 30 | IBRD/IDA (8841); Recipient Executed Activities (1003); Global Environment Project (229) |
| `projectid` | str | 100.0 | 0.0 | 0.0 | 0.0 | 411 | P152698 (1228); P152309 (346); P148591 (326) |
| `projn` | str:multiline, str | 100.0 | 0.0 | 0.0 | 0.0 | 511 | IN-National Hydrology Project -- P152698 (1228); VN-Vietnam Dam Rehabilitation And Safety (346); ET-Second Agricultural Growth Project -- (326) |
| `repnb` | str, str:int | 87.59 | 12.41 | 0.0 | 0.0 | 15151 | SFG1121 (63); E2686 (53); ISR60169 (26) |
| `seccl` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1 | Public (18350) |
| `subsc` | str, str:multiline | 53.49 | 46.51 | 0.0 | 0.0 | 763 | Public Administration - Social Protectio (338); Public Administration - Health,Health-HG (264); Public Administration - Water, Sanitatio (249) |
| `theme` | str, str:multiline | 43.05 | 56.95 | 0.0 | 0.0 | 840 | Water resource management (245); Environment and natural resource managem (153); Disaster Risk Management,Disaster Prepar (138) |
| `txturl` | str | 99.98 | 0.02 | 0.0 | 0.0 | 18342 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `url_friendly_title` | str | 100.0 | 0.0 | 0.0 | 0.0 | 18345 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `versiontyp` | str, str:int | 53.33 | 46.67 | 0.0 | 0.0 | 19 | Final (7107); Buff cover (2368); Revised (135) |
