### wds_v2_meta_sample_by_type

Source: search API v2 wds (per docty sample). Rows profiled: 1200

| Column | Observed type(s) | Populated % | Null % | Empty % | Sentinel % | Distinct | Range / top values |
|---|---|---:|---:|---:|---:|---:|---|
| `EnvironmentalCategory` | str | 13.92 | 86.08 | 0.0 | 0.0 | 5 | B (104); C (33); A (21) |
| `abstracts` | object | 50.5 | 49.5 | 0.0 | 0.0 | 563 | {"cdata!": "The Pilot Fisheries Developm (2); {"cdata!": "The Third Urban Development  (2); {"cdata!": "Ratings for the Bahia Socio  (2) |
| `admreg` | str | 99.75 | 0.25 | 0.0 | 0.0 | 12 | Africa,Africa (258); Latin America & Caribbean,Latin America  (227); East Asia and Pacific,East Asia and Paci (194) |
| `admreg_key` | str | 99.75 | 0.25 | 0.0 | 0.0 | 10 | 119222,119222 (258); 119228,119228 (227); 119225,119225 (194) |
| `alt_title` | object | 2.25 | 97.75 | 0.0 | 0.0 | 19 | {"0": {"alt_title": "Colombia - Fortalec (2); {"0": {"alt_title": "Moroc - Projet d'Ap (2); {"0": {"alt_title": "Guinée - Troisième  (2) |
| `author_keys` | object | 35.67 | 64.33 | 0.0 | 0.0 | 254 | {"0": {"author_key": "NA"}} (141); {"0": {"author_key": "124470"}} (4); {"0": {"author_key": "1010584"}} (4) |
| `authors` | object | 42.58 | 57.42 | 0.0 | 0.0 | 395 | {"0": {"author": "IEG Review Team"}} (75); {"0": {"author": "Ji You"}} (4); {"0": {"author": "Draugelis,Gailius J."} (4) |
| `available_in` | str | 100.0 | 0.0 | 0.0 | 0.0 | 14 | English (1165); Arabic,English (6); English,Arabic (6) |
| `bdmdt` | str:date[ISO8601Z] | 1.0 | 99.0 | 0.0 | 0.0 | 9 | 2019-05-23 → 2024-06-30 |
| `chronical_docm_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | 090224b0828abb66 (2); 090224b0828ad589 (2); 090224b0828ad896 (2) |
| `closedt` | str:date[ISO8601Z] | 0.08 | 99.92 | 0.0 | 0.0 | 1 (unique) | 2024-06-30 → 2024-06-30 |
| `colti` | str | 0.67 | 99.33 | 0.0 | 0.0 | 1 | World Development Sources, WDS 1998-4 (8) |
| `count` | str | 100.0 | 0.0 | 0.0 | 0.0 | 189 | China (66); India (55); Brazil (44) |
| `count_key` | str:int, str | 100.0 | 0.0 | 0.0 | 0.0 | 182 | 82628 (66); 82602 (55); 82649 (44) |
| `credit_no` | str, str:int | 17.25 | 82.75 | 0.0 | 0.0 | 155 | CRDNM (49); 3083 (2); 3331 (2) |
| `datestored` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 602 | 1981-06-01 → 2024-12-03 |
| `disclosure_date` | str:date[ISO8601Z] | 93.92 | 6.08 | 0.0 | 0.0 | 256 | 2005-03-29 → 2024-07-10 |
| `disclosure_type` | — | 0.0 | 0.0 | 0.0 | 100.0 | 1 | NA (1200) |
| `disclstat` | str | 85.58 | 14.42 | 0.0 | 0.0 | 1 | Disclosed (1027) |
| `display_title` | str:multiline, str | 100.0 | 0.0 | 0.0 | 0.0 | 1129 | Nigeria - WESTERN AND CENTRAL            (12); India - SOUTH ASIA- P177876- West        (6); Malaysia - Social Sector Support Project (3) |
| `docdt` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 445 | 1981-05-21 → 2024-07-11 |
| `docm_id` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | 090224b0828abb66 (2); 090224b0828ad589 (2); 090224b0828ad896 (2) |
| `docna` | object | 100.0 | 0.0 | 0.0 | 0.0 | 1121 | {"0": {"docna": "Nigeria - WESTERN AND C (12); {"0": {"docna": "India - SOUTH ASIA- P17 (6); {"0": {"docna": "Malaysia - Social Secto (3) |
| `docty` | str | 100.0 | 0.0 | 0.0 | 0.0 | 8 | Project Appraisal Document (150); Implementation Status and Results Report (150); Implementation Completion and Results Re (150) |
| `docty_key` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 8 | 540656 (150); 791001 (150); 540632 (150) |
| `entityids` | object | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | {"entityid": "000333037_20080307014114"} (2); {"entityid": "000020439_20070830102824"} (2); {"entityid": "000020439_20070820140528"} (2) |
| `ext_pub_date` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 212 | 2009-07-22 → 2024-07-10 |
| `fullavailablein` | list | 100.0 | 0.0 | 0.0 | 0.0 | 36 | [] (1165); [{"entityid": "000333038_20110203223253" (1); [{"entityid": "000333037_20100621235735" (1) |
| `geo_reg_key` | str, str:int | 59.25 | 40.75 | 0.0 | 0.0 | 104 | 517191 (68); 517191,119228 (63); 80475,80451,80471 (33) |
| `geo_region_mdks` | object | 59.25 | 40.75 | 0.0 | 0.0 | 136 | {"0": {"geo_region_mdk": "World!$!517191 (40); {"0": {"geo_region_mdk": "World!$!80475" (33); {"0": {"geo_region_mdk": "World!$!517191 (25) |
| `geo_regions` | object | 59.25 | 40.75 | 0.0 | 0.0 | 126 | {"0": {"geo_region": "World"}, "1": {"ge (45); {"0": {"geo_region": "World"}, "1": {"ge (40); {"0": {"geo_region": "World"}, "1": {"ge (38) |
| `guid` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | 212321468273649766 (2); 865041468072236170 (2); 797161468025170030 (2) |
| `historic_topic` | str | 67.92 | 32.08 | 0.0 | 0.0 | 473 | Finance and Financial Sector Development (42); Health, Nutrition and Population (39); Education (35) |
| `id` | str:int | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | 9061239 (2); 8201163 (2); 8132893 (2) |
| `keywd` | object | 80.83 | 19.17 | 0.0 | 0.0 | 932 | {"0": {"keywd": "access road;Planning an (2); {"0": {"keywd": "Autonomous City of Buen (2); {"0": {"keywd": "monitoring and evaluati (2) |
| `lang` | str | 100.0 | 0.0 | 0.0 | 0.0 | 6 | English (1184); Arabic (6); French (6) |
| `lang_key` | str:int | 99.83 | 0.17 | 0.0 | 0.0 | 6 | 120701 (1184); 120699 (6); 120702 (4) |
| `last_modified_date` | str:date[ISO8601Z] | 34.42 | 65.58 | 0.0 | 0.0 | 44 | 1999-08-15 → 2024-12-09 |
| `listing_relative_url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | /projects/documents/2007/11/9061239/el-s (2); /projects/documents/2007/08/8201163/para (2); /projects/documents/2007/06/8132893/ecua (2) |
| `lndinstr` | object | 69.58 | 30.42 | 0.0 | 0.0 | 32 | {"0": {"lndinstr": "Specific Investment  (382); {"0": {"lndinstr": "Investment Project F (203); {"0": {"lndinstr": "Adaptable Program Lo (41) |
| `lndinstr_key` | str:int, str | 69.5 | 30.5 | 0.0 | 0.0 | 32 | 639306 (382); 1353855 (202); 639294 (41) |
| `loan_no` | str:int, str, str:multiline | 41.08 | 58.92 | 0.0 | 0.0 | 334 | <iwLINK iwREL=foreign_key iwTYPE=mdk     (78); LOANM (69); 4452 (3) |
| `majdocty` | str | 100.0 | 0.0 | 0.0 | 0.0 | 2 | Project Documents (911); Project Documents,Project Documents (289) |
| `majdocty_key` | str:int, str | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 658102 (911); 658102,658102 (289) |
| `majtheme` | str | 36.25 | 63.75 | 0.0 | 0.0 | 211 | Environment and natural resource managem (36); Human development (25); Urban development (19) |
| `new_url` | str | 99.58 | 0.42 | 0.0 | 0.0 | 1159 | 2007/11/9061239/El-Salvador-Second-Land- (2); 2007/08/8201163/Paraguay-Modernization-o (2); 2007/06/8132893/Ecuador-Institutional-Re (2) |
| `no_of_pages` | str:int | 99.83 | 0.17 | 0.0 | 0.0 | 150 | 6 (79); 4 (67); 5 (66) |
| `origu` | str, str:multiline | 46.08 | 47.67 | 0.0 | 6.25 | 213 | OPS2DATASHEET (77); None (75); Sector Evaluations (IEGSE) (51) |
| `owner` | str | 91.42 | 8.42 | 0.0 | 0.17 | 467 | Infrastructure & Energy Srvc Dpt (ECSIE) (26); Human Development Sector Dept (EASHD) (20); Sustainable Devmt Sector Unit (ECSSD) (15) |
| `pdfurl` | str | 85.75 | 14.25 | 0.0 | 0.0 | 1009 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `prdln` | str | 70.58 | 29.42 | 0.0 | 0.0 | 21 | IBRD/IDA (683); Recipient Executed Activities (51); Global Environment Project (36) |
| `prdln_key` | str:int, str | 70.58 | 29.42 | 0.0 | 0.0 | 21 | 639266 (683); 904549 (51); 639246 (36) |
| `projectid` | str | 99.67 | 0.33 | 0.0 | 0.0 | 1089 | P167156 (12); P177876 (6); P086446 (3) |
| `projn` | str:multiline, str | 100.0 | 0.0 | 0.0 | 0.0 | 1094 | NG-Nigeria Improved Child Survival Progr (12); IN-West Bengal Accelerated Development O (6); CN-Chongqing Urban-Rural Integration Pro (3) |
| `publishtoextweb_dt` | str:date[ISO8601Z] | 100.0 | 0.0 | 0.0 | 0.0 | 212 | 2009-07-22 → 2024-07-10 |
| `repnb` | str, str:int | 100.0 | 0.0 | 0.0 | 0.0 | 1150 | 52760 (2); 50768 (2); 49278 (2) |
| `repnme` | object | 81.25 | 18.75 | 0.0 | 0.0 | 916 | {"repnme": "Malaysia - Social Sector Sup (3); {"repnme": "Colombia - Strengthening the (2); {"repnme": "Kazakhstan - Technical and V (2) |
| `seccl` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1 | Public (1200) |
| `sectr` | object | 61.25 | 38.75 | 0.0 | 0.0 | 206 | {"0": {"sector": "Agriculture, Fishing a (71); {"0": {"sector": "Education"}} (48); {"0": {"sector": "Public Administration" (47) |
| `sectr_key` | str:int, str | 60.67 | 39.33 | 0.0 | 0.0 | 196 | 369180 (71); 369163 (52); 369165 (48) |
| `subsc` | str, str:multiline | 88.33 | 11.67 | 0.0 | 0.0 | 736 | Central Government (Central Agencies) (28); Public Administration - Health,Health-HG (20); Power (15) |
| `subtopic` | str, str:multiline | 80.83 | 19.17 | 0.0 | 0.0 | 896 | Sanitation and Sewerage,Engineering,Wate (4); Primary Education,Educational Sciences,E (4); Labor Markets,Vocational & Technical Edu (3) |
| `subtopic_key` | str, str:int | 80.83 | 19.17 | 0.0 | 0.0 | 896 | 672757,672779,738556,738559,758511,78700 (4); 672926,672941,672945,672946,738818,75858 (4); 672803,672811,672929,672941,808997 (3) |
| `teratopic` | str | 67.92 | 32.08 | 0.0 | 0.0 | 473 | Finance and Financial Sector Development (42); Health, Nutrition and Population (39); Education (35) |
| `teratopic_key` | str, str:int | 67.92 | 32.08 | 0.0 | 0.0 | 473 | 644297 (42); 644300 (39); 644301 (35) |
| `theme` | str, str:multiline | 54.67 | 45.33 | 0.0 | 0.0 | 576 | Malaria,Mitigation,Human Development and (12); Environment and natural resource managem (6); Climate change (6) |
| `topicv3` | str, str:multiline | 78.67 | 21.33 | 0.0 | 0.0 | 874 | Water m1326522 137,Water Supply m1326961 (4); Education, Skills Development and Labor  (3); Poverty m1326687 695,Education m1327048  (3) |
| `totvolnb` | str:int | 81.25 | 18.75 | 0.0 | 0.0 | 1 | 1 (975) |
| `trustfund` | str:multiline, str | 57.42 | 42.58 | 0.0 | 0.0 | 631 | TF0B2619-Nigeria Improved Child Survival (12); TF097371-The Palestinian NGO-IV Project (2); TF035586-MOROCCO - FISHERIES PROJECT AND (2) |
| `trustfund_key` | str, str:int | 57.25 | 42.75 | 0.0 | 0.0 | 630 | 1900335,2011553,2328641 (12); 1122251 (2); 593517,593410 (2) |
| `txturl` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `url` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `url_friendly_title` | str | 100.0 | 0.0 | 0.0 | 0.0 | 1164 | http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2); http://documents.worldbank.org/curated/e (2) |
| `versiontyp` | str | 80.5 | 19.5 | 0.0 | 0.0 | 7 | Final (778); Buff cover (119); Gray cover (38) |
| `versiontyp_key` | str:int | 80.33 | 19.67 | 0.0 | 0.0 | 6 | 1309935 (778); 1309937 (119); 1309938 (38) |
| `volnb` | str:int | 81.33 | 18.67 | 0.0 | 0.0 | 1 | 1 (976) |
