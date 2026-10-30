# RAG scope v2: 10 countries, 70 projects (planning only)

Prepared 2026-10-24. Read-only planning. No PDFs were downloaded, Databricks was not modified, and no embeddings, indexes or tables were created. The previous selection in `selection_70/` is unchanged. **Nothing will be downloaded until this plan is approved.**

## Summary

| Item | Value |
|---|---|
| Countries | 10 (one or two per World Bank region, all single-country with valid WDI codes) |
| Projects | 70, exactly 7 per country (48 closed, 22 active) |
| Recommended PDFs | **382** (PAD 70, ISR 140, ICR 46, ICRR 42, procurement plans 84) |
| Already held locally | 5 (latest procurement plans of P132833, P178432, P148850, P175325, P500392) |
| **New downloads** | **377 PDFs, about 162.5 MB (155 MiB) estimated** |
| Official availability check | HEAD request on all 382 URLs: 382 returned HTTP 200 `application/pdf` |
| Duplicate IDs or URLs in the manifest | 0 |
| Languages | 380 English, 1 French (an earliest procurement plan), 1 with blank language metadata (an ICRR known to be English from its title) |
| Overlap with the previous 70 | 6 projects: P121019, P132833, P148850, P150357, P175325, P500392 |

The count is at the top of the 300–380 range: 382 documents in the manifest, of which 377 need downloading. If a hard ceiling of 380 is needed, the two lowest-value candidates to drop are the earliest procurement plans of P156019 and P164336 (the largest-project extras in Sri Lanka and Ethiopia). Those countries would still have earliest plans for their verified projects.

## Deliverables (all in `rag/module1_discovery/selection_v2_10_countries/`)

| File | Content |
|---|---|
| `RAG_10_COUNTRY_70_PROJECT_SELECTION_REPORT.md` | This report |
| `PROJECT_SELECTION_MANIFEST_v2.csv` | The 70 projects with country, region, income, sector, approval FY, status, contract and notice counts, document availability, verification status and Gold procurement facts |
| `DOCUMENT_DOWNLOAD_MANIFEST_v2.csv` | The 382 documents with WDS ID, type, role, selection reason, title, dates, version, report number, ISR sequence, language, GUID, PDF URL, final URL, HTTP status, size or estimate, local flag |
| `FIRST_BATCH_10_v2.csv` | The first 10 PDFs for manual inspection |
| `COUNTRY_RATIONALE_v2.csv` | Scores, eligibility pools and runners-up for each country |
| `METADATA_COMPLETENESS_v2.json` | Field completeness, availability and gap checks |
| `COMPARISON_WITH_PREVIOUS_70.json` | Profile of the previous and revised selections |
| `DOCUMENT_ISSUES_v2.csv` | Every duplicate, translation, superseded ISR, missing URL and missing-metadata record among the 7,931 listed documents |
| `all_documents_metadata_v2.jsonl` | Full WDS metadata for all 7,931 documents listed for the 70 projects |
| `country_scoring.json` | Country scores for all 61 viable countries and the top non-viable ones |
| `selection_v2_summary.json`, `m12_run.log` | Run summary and log |

Scripts (in `rag/module1_discovery/`): `m11_country_scoring.py` (country scoring), `m12_select_10_countries_70_projects.py` (project and document selection, WDS listing and HEAD checks, seed 20261026) and `m13_v2_checks_and_first_batch.py` (first batch, rationale, completeness, comparison; no network).

## 1. How the countries were chosen

**Project eligibility** (from the 2,000-project cohort): single-country with a WDI country code, and in the WDS API at least 1 PAD, at least 2 ISRs and at least 1 procurement plan, plus at least 10 contracts and at least 10 notices in Gold. 1,271 projects in 128 countries qualify.

**Country viability:** at least 7 eligible projects, including at least 2 active, at least 2 closed and at least 2 with an ICR. 61 countries qualify.

**Score:**

- eligible projects (up to 2 points, capped at 15);
- projects with an ICR (up to 2 points, capped at 7);
- share of DS00005 contract references in STEP format (up to 2 points; a proxy for whether plan rows can be matched to contracts);
- sector diversity (up to 1 point);
- procurement-method diversity (up to 1 point);
- approval-year spread (up to 1 point);
- 0.5 bonus if the earlier plan-to-contract probe already found exact matches.

Countries were picked by taking the highest score in each of the 7 regions, then the next highest scores overall, with at most 2 per region.

| Code | Country | Region | Income FY2026 | Score | Rank (of 61) | Basis | Eligible (closed/active) | With ICR | STEP share | Probe matches | Regional runner-up |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LK | Sri Lanka | SAR | LM | 8.762 | 1 | best score in region | 15 (10/5) | 9 | 73% | 20 | NP (7.882) |
| ET | Ethiopia | AFE | no FY2026 entry (FY2025: L) | 8.746 | 2 | best score in region | 20 (13/7) | 9 | 62% | 12 | CD (8.57) |
| SN | Senegal | AFW | LM | 8.688 | 4 | best score in region | 19 (8/11) | 7 | 74% | 22 | GH (8.47) |
| PS | West Bank and Gaza | MNA | LM | 8.662 | 6 | best score in region | 15 (7/8) | 6 | 77% | 16 | DJ (6.631) |
| AR | Argentina | LAC | UM | 8.574 | 8 | best score in region | 17 (12/5) | 10 | 79% | 0 | HT (7.833) |
| ID | Indonesia | EAP | UM | 7.948 | 20 | best score in region | 23 (16/7) | 14 | 62% | 0 | VN (6.842) |
| MD | Moldova | ECA | UM | 7.468 | 32 | best score in region | 14 (6/8) | 4 | 83% | 0 | TR (7.086) |
| KE | Kenya | AFE | LM | 8.7 | 3 | next-highest score (max 2 per region) | 16 (10/6) | 9 | 85% | 0 | CD (8.57) |
| BF | Burkina Faso | AFW | L | 8.68 | 5 | next-highest score (max 2 per region) | 16 (10/6) | 7 | 84% | 0 | GH (8.47) |
| PK | Pakistan | MNA | LM | 8.658 | 7 | next-highest score (max 2 per region) | 42 (21/21) | 16 | 88% | 0 | DJ (6.631) |

Notes:

- **South Asia** is represented by Sri Lanka, not India or Nepal. Sri Lanka has the highest overall score (8.762), 20 verified plan-to-contract matches and 9 eligible projects with an ICR.
- **Ethiopia** has no FY2026 income classification in the reference data (FY2025: low income), so it is shown as "no FY2026 entry". The cause is the reference table, not a selection error.
- **Pakistan** sits in the Bank's "Middle East, North Africa, Afghanistan and Pakistan" region, so that region and the two Africa regions each have two countries.
- **Income mix:** 1 low income (Burkina Faso, plus Ethiopia on FY2025), 5 lower-middle and 3 upper-middle. There are no high-income borrowers.

## 2. The 70 projects

How projects were picked within each country:

1. Projects whose plan rows were already verified against contracts come first: P132833, P148850, P175325, P500392.
2. A greedy pick then fills the 7 places, rewarding:
   - a new sector;
   - status balance (at least 3 closed and at least 2 active where possible);
   - closed projects that have an ICR;
   - a new approval-year band;
   - a modest bonus for contract volume.

Result: every country has exactly 7 projects in 7 distinct sectors, with 11 sectors overall.

Approval-year bands: FY≤2014: 16, FY2015–17: 15, FY2018–20: 20, FY2021–23: 12, FY2024+: 7.

Table columns:

- **PAD/ISR/ICR/ICRR/PP:** documents of each type available in the WDS API.
- **Sel.:** documents selected for the project.
- **Contract–document refs:** "verified n" means n plan rows were matched exactly to DS00005 contract references by the earlier probe. "expected x%" means the country's STEP-format share suggests matches are likely but they have not yet been verified, because that needs the PDFs.

| # | Project | Title | Ctry | Reg | Sector | FY | Status | Contracts | Notices | PAD/ISR/ICR/ICRR/PP | Sel. | Contract–document refs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | P146314 | Climate Resilience Improvement Project (CRIP) | LK | SAR | Water, Sanitation and Waste Management | 2014 | Closed | 66 | 122 | 1/14/1/1/35 | 6 | expected (73%) |
| 2 | P132833 | Transport Connectivity and Asset Management Proj | LK | SAR | Transportation | 2016 | Closed | 89 | 124 | 1/16/1/1/58 | 7 | verified 20 |
| 3 | P156019 | Sri Lanka Agriculture Sector Modernization Proje | LK | SAR | Agriculture, Fishing and Forestry | 2016 | Closed | 1354 | 1624 | 1/19/1/1/103 | 7 | expected (73%) |
| 4 | P159303 | Sri Lanka Financial Sector Modernization Project | LK | SAR | Financial Sector | 2017 | Closed | 11 | 45 | 1/11/1/1/44 | 6 | expected (73%) |
| 5 | P163721 | Sri Lanka: Primary Health Care System Strengthen | LK | SAR | Health | 2018 | Closed | 64 | 75 | 1/13/1/1/35 | 6 | expected (73%) |
| 6 | P163714 | General Education Modernization Project | LK | SAR | Education | 2018 | Active | 14 | 25 | 1/43/0/0/29 | 4 | expected (73%) |
| 7 | P178432 | Sri Lanka Public Financial Management Strengthen | LK | SAR | Public Administration | 2023 | Active | 55 | 67 | 1/7/0/0/32 | 4 | expected (73%) |
| 8 | P123531 | Health Sustainable Development Goals Program-for | ET | AFE | Health | 2013 | Closed | 72 | 88 | 1/22/1/1/32 | 6 | expected (62%) |
| 9 | P143302 | Ethiopia Competitiveness and Job Creation Projec | ET | AFE | Industry, Trade and Services | 2014 | Closed | 45 | 94 | 1/19/1/1/77 | 6 | expected (62%) |
| 10 | P148850 | Ethiopia- Expressway Development Support Project | ET | AFE | Transportation | 2015 | Closed | 43 | 73 | 1/21/0/0/65 | 5 | verified 12 |
| 11 | P163452 | Ethiopia Urban Institutional and Infrastructure  | ET | AFE | Public Administration | 2018 | Closed | 66 | 131 | 1/14/1/1/60 | 6 | expected (62%) |
| 12 | P164336 | Lowlands Livelihood Resilience Project | ET | AFE | Agriculture, Fishing and Forestry | 2019 | Closed | 719 | 826 | 1/13/1/0/258 | 6 | expected (62%) |
| 13 | P177233 | Response - Recovery - Resilience for Conflict-Af | ET | AFE | Social Protection | 2022 | Active | 312 | 302 | 1/13/0/0/79 | 4 | expected (62%) |
| 14 | P176731 | Power Sector Reform, Investment and Modernizatio | ET | AFE | Energy and Extractives | 2024 | Active | 11 | 50 | 1/5/0/0/21 | 4 | expected (62%) |
| 15 | P122841 | Stormwater Mgt. and Climate Change Adaptation Pr | SN | AFW | Water, Sanitation and Waste Management | 2012 | Closed | 25 | 71 | 1/14/1/1/24 | 6 | expected (74%) |
| 16 | P125565 | Electricity Sector Support Project | SN | AFW | Energy and Extractives | 2013 | Closed | 91 | 246 | 2/21/1/1/82 | 6 | expected (74%) |
| 17 | P124018 | SENEGAL - Sustainable and Inclusive Agribusiness | SN | AFW | Agriculture, Fishing and Forestry | 2014 | Closed | 36 | 61 | 1/14/1/1/31 | 6 | expected (74%) |
| 18 | P167681 | Improving Youth Employability Through Informal A | SN | AFW | Education | 2019 | Closed | 47 | 56 | 1/11/1/1/45 | 6 | expected (74%) |
| 19 | P162042 | Investing in Maternal, Child and Adolescent Heal | SN | AFW | Health | 2020 | Closed | 145 | 179 | 1/14/1/0/65 | 5 | expected (74%) |
| 20 | P156186 | Dakar Bus Rapid Transit Pilot Project | SN | AFW | Transportation | 2017 | Active | 154 | 189 | 1/16/0/0/80 | 5 | expected (74%) |
| 21 | P175325 | Casamance Economic Development Project | SN | AFW | Social Protection | 2022 | Active | 94 | 90 | 1/9/0/0/57 | 5 | verified 22 |
| 22 | P129861 | West Bank and Gaza: Education-to-Work Transition | PS | MNA | Education | 2012 | Closed | 31 | 29 | 1/17/1/1/28 | 6 | expected (77%) |
| 23 | P150991 | GZ-Integrated Cities and Urban Development Proje | PS | MNA | Public Administration | 2017 | Closed | 43 | 67 | 2/12/1/1/43 | 6 | expected (77%) |
| 24 | P164412 | Innovative Private Sector Development | PS | MNA | Industry, Trade and Services | 2018 | Closed | 159 | 170 | 2/11/1/1/86 | 6 | expected (77%) |
| 25 | P148600 | West Bank and Gaza Electricity Sector Performanc | PS | MNA | Energy and Extractives | 2018 | Closed | 31 | 43 | 2/14/1/1/45 | 6 | expected (77%) |
| 26 | P172571 | Technology for Youth and Jobs | PS | MNA | Information and Communications Technologies | 2020 | Active | 175 | 186 | 2/11/0/0/77 | 5 | expected (77%) |
| 27 | P180263 | Health System Efficiency and Resilience Project | PS | MNA | Health | 2023 | Active | 155 | 134 | 3/7/0/0/39 | 4 | expected (77%) |
| 28 | P500392 | Integrated Solid Waste Management Project-Phase  | PS | MNA | Water, Sanitation and Waste Management | 2025 | Active | 24 | 19 | 1/5/0/0/9 | 5 | verified 16 |
| 29 | P125151 | Plan Belgrano Water Supply and Sanitation Servic | AR | LAC | Water, Sanitation and Waste Management | 2011 | Closed | 47 | 69 | 2/26/1/1/40 | 6 | expected (79%) |
| 30 | P133288 | Argentina Renewable Energy for Rural Areas Proje | AR | LAC | Energy and Extractives | 2015 | Closed | 123 | 132 | 2/15/1/1/66 | 6 | expected (79%) |
| 31 | P106685 | AR Socio-Economic Inclusion In Rural Areas | AR | LAC | Industry, Trade and Services | 2015 | Closed | 10 | 17 | 1/19/1/1/26 | 6 | expected (79%) |
| 32 | P159929 | Integrated Habitat and Housing Project | AR | LAC | Public Administration | 2017 | Closed | 36 | 38 | 2/18/1/1/48 | 6 | expected (79%) |
| 33 | P170329 | Social Safety Nets for Vulnerable Populations in | AR | LAC | Social Protection | 2020 | Closed | 81 | 83 | 2/14/1/0/24 | 5 | expected (79%) |
| 34 | P175669 | Sustainable Recovery of Landscapes and Livelihoo | AR | LAC | Agriculture, Fishing and Forestry | 2022 | Active | 127 | 128 | 2/13/0/0/44 | 4 | expected (79%) |
| 35 | P179534 | Strengthening the Digital Health Agenda in the P | AR | LAC | Health | 2024 | Active | 171 | 163 | 2/7/0/0/25 | 5 | expected (79%) |
| 36 | P096532 | Dam Operational Improvement and Safety | ID | EAP | Water, Sanitation and Waste Management | 2009 | Closed | 139 | 323 | 1/26/2/2/124 | 8 | expected (62%) |
| 37 | P144269 | PROMOTING SUSTAINABLE CBNRM AND INSTITUTIONAL DE | ID | EAP | Agriculture, Fishing and Forestry | 2016 | Closed | 61 | 74 | 1/14/1/1/25 | 6 | expected (62%) |
| 38 | P157599 | Integrated Infrastructure Development for Nation | ID | EAP | Industry, Trade and Services | 2018 | Closed | 137 | 224 | 1/14/1/1/120 | 6 | expected (62%) |
| 39 | P165543 | Institutional Strengthening for Improved Village | ID | EAP | Public Administration | 2019 | Closed | 271 | 295 | 1/11/1/1/90 | 7 | expected (62%) |
| 40 | P168076 | Realizing  Education's  Promise:  Support to Ind | ID | EAP | Education | 2019 | Closed | 268 | 276 | 1/12/1/1/68 | 6 | expected (62%) |
| 41 | P169548 | Indonesia Mass Transit Project | ID | EAP | Transportation | 2022 | Active | 35 | 39 | 1/9/0/0/20 | 4 | expected (62%) |
| 42 | P180811 | Indonesia Health Systems Strengthening Project | ID | EAP | Health | 2024 | Active | 84 | 111 | 1/9/0/0/132 | 4 | expected (62%) |
| 43 | P118518 | Moldova Agriculture Competitiveness Project | MD | ECA | Agriculture, Fishing and Forestry | 2012 | Closed | 79 | 134 | 1/26/1/0/45 | 5 | expected (83%) |
| 44 | P127388 | Moldova Education Reform Project | MD | ECA | Education | 2013 | Closed | 241 | 233 | 1/20/1/1/37 | 7 | expected (83%) |
| 45 | P144103 | MD Second Competitiveness Enhancement Project | MD | ECA | Industry, Trade and Services | 2015 | Closed | 25 | 48 | 1/16/1/1/31 | 6 | expected (83%) |
| 46 | P150357 | Local Roads Improvement Project | MD | ECA | Transportation | 2016 | Closed | 13 | 26 | 1/16/1/1/7 | 6 | expected (83%) |
| 47 | P173776 | Moldova Emergency COVID-19 Response Project | MD | ECA | Health | 2020 | Closed | 101 | 108 | 1/14/0/0/40 | 4 | expected (83%) |
| 48 | P177895 | MSME Competitiveness Project | MD | ECA | Information and Communications Technologies | 2022 | Active | 96 | 129 | 1/8/0/0/74 | 4 | expected (83%) |
| 49 | P504278 | Strengthening Moldova's Disaster Risk Management | MD | ECA | Public Administration | 2025 | Active | 25 | 34 | 1/4/0/0/18 | 4 | expected (83%) |
| 50 | P121019 | Kenya Infrastructure Finance/PPP project | KE | AFE | Financial Sector | 2013 | Closed | 30 | 74 | 1/23/1/1/37 | 6 | expected (85%) |
| 51 | P105269 | Judicial Performance Improvement | KE | AFE | Public Administration | 2013 | Closed | 15 | 54 | 1/14/1/1/22 | 6 | expected (85%) |
| 52 | P152394 | Transforming Health Systems for Universal Care | KE | AFE | Health | 2016 | Closed | 971 | 1293 | 1/16/1/1/780 | 7 | expected (85%) |
| 53 | P164654 | Kenya Social and Economic Inclusion Project | KE | AFE | Social Protection | 2019 | Closed | 50 | 87 | 1/13/1/1/43 | 6 | expected (85%) |
| 54 | P174059 | KENYA GPE COVID 19 LEARNING CONTINUITY IN BASIC  | KE | AFE | Education | 2021 | Closed | 14 | 15 | 1/4/1/1/5 | 6 | expected (85%) |
| 55 | P163980 | Marine Fisheries and Socio-Economic Development  | KE | AFE | Agriculture, Fishing and Forestry | 2020 | Active | 144 | 219 | 1/13/0/0/100 | 4 | expected (85%) |
| 56 | P161305 | Horn of Africa Gateway Development Project | KE | AFE | Transportation | 2021 | Active | 121 | 229 | 1/14/0/0/154 | 4 | expected (85%) |
| 57 | P120517 | Local Government Support, COVID-19 and Resilienc | BF | AFW | Public Administration | 2012 | Closed | 93 | 114 | 1/19/1/1/33 | 6 | expected (84%) |
| 58 | P124015 | Social Safety Net Project | BF | AFW | Social Protection | 2014 | Closed | 215 | 293 | 1/23/1/1/87 | 6 | expected (84%) |
| 59 | P148062 | B. Faso - Education Access and Quality Improveme | BF | AFW | Education | 2015 | Closed | 426 | 477 | 1/20/1/1/67 | 7 | expected (84%) |
| 60 | P173858 | Burkina Faso COVID-19 Preparedness and Response  | BF | AFW | Health | 2020 | Closed | 127 | 130 | 1/7/0/0/58 | 4 | expected (84%) |
| 61 | P166785 | Burkina Faso Energy Access Project (SOLEER) | BF | AFW | Energy and Extractives | 2021 | Active | 70 | 80 | 1/12/0/0/23 | 4 | expected (84%) |
| 62 | P178598 | Burkina Faso Livestock Resilience and Competitiv | BF | AFW | Agriculture, Fishing and Forestry | 2023 | Active | 92 | 143 | 1/8/0/0/41 | 4 | expected (84%) |
| 63 | P177918 | Secondary Cities Urban Mobility and Development  | BF | AFW | Transportation | 2024 | Active | 75 | 87 | 1/6/0/0/32 | 4 | expected (84%) |
| 64 | P125999 | PK Punjab Irrig Agri Productivity Improvement Pr | PK | MNA | Agriculture, Fishing and Forestry | 2012 | Closed | 22 | 48 | 1/20/1/1/17 | 6 | expected (88%) |
| 65 | P154278 | FATA Temporarily Displaced Persons Emergency Rec | PK | MNA | Social Protection | 2016 | Closed | 181 | 234 | 1/18/1/1/48 | 7 | expected (88%) |
| 66 | P159712 | Sindh Solar Energy Project | PK | MNA | Energy and Extractives | 2018 | Closed | 41 | 68 | 1/15/1/1/55 | 6 | expected (88%) |
| 67 | P166308 | Balochistan Human Capital Investment Project | PK | MNA | Education | 2020 | Closed | 161 | 296 | 1/10/1/1/49 | 6 | expected (88%) |
| 68 | P151075 | Pakistan Community Support Project (CASA-1000) | PK | MNA | Public Administration | 2020 | Closed | 48 | 73 | 1/7/1/1/38 | 6 | expected (88%) |
| 69 | P180323 | Integrated Flood Resilience and Adaptation Proje | PK | MNA | Industry, Trade and Services | 2023 | Active | 131 | 205 | 1/6/0/0/73 | 4 | expected (88%) |
| 70 | P180707 | Khyber Pakhtunkhwa Citizen-Centered Service Deli | PK | MNA | Health | 2024 | Active | 126 | 160 | 1/6/0/0/17 | 4 | expected (88%) |

## 3. Documents: availability and recommended selection

Across the 70 projects the WDS API lists **7,931 documents**: PAD 83, ISR 991, ICR 46, ICRR 42, procurement plans 4,522, other 2,247.

Selection rules (all within the project's own WDS records, with `projectid` as the filter key):

| Role | Rule | Count |
|---|---|---|
| PAD_original | One PAD per project: English first, then the Final version, then the earliest that is not an additional-financing PAD. Translations and additional PADs are recorded in the issues file. | 70 |
| ISR_first / ISR_latest | First and latest ISR by sequence number. Re-uploads of the same sequence are deduplicated, keeping the last. | 70 + 70 |
| ICR | Every ICR | 46 |
| ICRR | Every ICRR (IEG review) | 42 |
| PP_latest | Latest procurement plan by document date | 70 |
| PP_earliest | Earliest procurement plan, only for the 4 verified-match projects and the largest project (by contracts) in each country | 14 |
| **Total** | | **382** |

Per-country document counts and estimated new download sizes:

| Country | Projects (closed/active) | Contracts | Notices | Documents | New downloads | Est. new MB |
|---|---|---|---|---|---|---|
| Sri Lanka | 7 (5/2) | 1653 | 2082 | 40 | 38 | 16.8 |
| Ethiopia | 7 (5/2) | 1268 | 1564 | 37 | 36 | 15.4 |
| Senegal | 7 (5/2) | 592 | 892 | 39 | 38 | 16.5 |
| West Bank and Gaza | 7 (4/3) | 618 | 648 | 38 | 37 | 15.6 |
| Argentina | 7 (5/2) | 595 | 630 | 38 | 38 | 16.8 |
| Indonesia | 7 (5/2) | 995 | 1342 | 41 | 41 | 18.4 |
| Moldova | 7 (5/2) | 580 | 712 | 36 | 36 | 15.0 |
| Kenya | 7 (5/2) | 1345 | 1971 | 39 | 39 | 16.8 |
| Burkina Faso | 7 (4/3) | 1098 | 1324 | 35 | 35 | 14.1 |
| Pakistan | 7 (5/2) | 710 | 1084 | 39 | 39 | 17.0 |
| **Total** | 70 (48/22) | 9454 | 12249 | 382 | 377 | 162.5 |

**Size estimate:** 162,469,371 bytes for the 377 new files. 18 files report `Content-Length` on HEAD. The other 364 use the median size of local PDFs of the same type, because the WDS server omits `Content-Length` for most files. Treat the estimate as ±30%.

## 4. Metadata completeness and availability checks

| Check | Result |
|---|---|
| HTTP HEAD on all 382 PDF URLs | 382 × HTTP 200, `application/pdf` (after the documents1 redirect) |
| Every project has PAD, first ISR, latest ISR and latest PP | 70/70 for each |
| First ISR differs from latest ISR | 70/70 |
| Duplicate document IDs or final URLs in the manifest | 0 / 0 |
| Document date / PDF URL / GUID blank | 0 / 0 / 0 |
| Disclosure date blank | 4 |
| Version type blank | 76 (ISR 60, PP 14, ICRR 2). Blanks are expected for these types; the version is held in the ISR sequence or the plan date instead. |
| Report number blank | 31 (PP 27, ISR 4) |
| Language blank or non-English | P125151 ICRR (blank; English title) and P148062 earliest PP (French; Burkina Faso). No English alternative for that plan is listed. |
| Exact file size known | 18 of 382 |
| Closed projects without an ICR in WDS | P148850, P173776, P173858 |
| Closed projects without an ICRR | P148850, P164336, P162042, P170329, P118518, P173776, P173858 |
| Income group with no FY2026 entry | The 7 Ethiopian projects (FY2025: L) |
| Gold other-country supplier share blank | All 7 West Bank and Gaza projects. Supplier country does not resolve to a country code for this economy; to be investigated as a data gap, with no change now. |
| Gold IFB median window blank (no IFB notices) | 12 projects: P175325, P122841, P129861, P170329, P175669, P159929, P106685, P144269, P144103, P121019, P174059, P173858 |

Issues across all 7,931 listed documents (in `DOCUMENT_ISSUES_v2.csv`):

- 795 duplicate records;
- 3,225 records with some missing metadata;
- 56 with no PDF URL (none of them selected);
- 12 PAD translations;
- 11 superseded ISR re-uploads;
- 1 additional PAD not selected.

## 5. Within-country comparison questions

Each question compares projects in the same country, so they share a borrower and a national procurement framework. Each names the documents that would answer it and the Gold facts that frame it.

1. **Sri Lanka: plan versus award.** In P132833 (transport), how many earliest- and latest-plan activities match signed contracts (20 already verified), and where do the planned method or date differ from the award? How does this compare with P156019 (agriculture, 1,354 contracts, almost all RFQ)? *Evidence:* PP_earliest, PP_latest, DS00005.
2. **Moldova: reliance on direct selection.** Why do P118518 (agriculture, 44% direct selection) and P173776 (COVID-19, 43%) rely on direct selection when P127388 (education) uses it for 4% of contracts? Do the PAD procurement sections and the ICR justify it, for example by emergency procedures? *Evidence:* PAD, ICR, latest ISR.
3. **Ethiopia: international suppliers.** P176731 (power sector, 73% of contracts to suppliers from other countries) and P148850 (expressway, 35%) contrast with P164336 (lowlands livelihoods, 0.3%). What do the PADs say about market capacity and international competition, and do the ISR procurement ratings differ? *Evidence:* PAD, first and latest ISR.
4. **Indonesia: prior review.** Prior Bank review covers 41% of contracts in P165543, 40% in P180811 and 31% in P169548, but 4% in P157599. Do the procurement plans' review thresholds or risk ratings explain the difference? *Evidence:* PP_latest, PAD risk section.
5. **Senegal: rating consistency.** For the closed projects with both an ICR and an ICRR (P125565, P167681, P124018, P122841), does the IEG outcome rating differ from the ICR's self-rating and from the latest ISR? P162042 has an ICR but no ICRR, which is a known gap. *Evidence:* ICR, ICRR, latest ISR.
6. **Burkina Faso: response windows.** Median IFB response windows run from 1 day in P124015 to 45–47 days in P166785 and P178598. Do the plans' methods and market approaches explain this, and is the 1-day figure a notice-type artefact that the plan would contradict? *Evidence:* PP_latest, PAD.
7. **Kenya: plan evolution at scale.** P152394 (health, 971 contracts, 780 plan versions) can be compared with P174059 (education, 14 contracts, 5 versions). How do the methods and package values change between the earliest and latest plans? *Evidence:* PP_earliest and PP_latest for P152394; PP_latest for P174059.
8. **West Bank and Gaza: planned versus actual in an active project.** For P500392 (solid waste, 16 verified matches), do planned and awarded contract dates agree? How does its procurement compare with P148600 (electricity, 29% direct selection, closed with ICR and ICRR)? *Evidence:* PP_latest, ICR, ICRR, DS00005.

Questions 1, 3 (P148850), 8 (P500392) and the Senegal project P175325 can be answered with ID-level evidence from day one. The others become ID-verifiable only if the downloaded plans show STEP references that match.

## 6. First 10 PDFs for manual inspection

These cover 3 countries (Sri Lanka, Senegal, Ethiopia), all from projects with verified plan-to-contract matches. They include every document type, one closed project with a full set, one active project and one closed project without an ICR. All 10 are new downloads.

| # | Country | Project | WDS ID | Role | Date | Est. size | Purpose |
|---|---|---|---|---|---|---|---|
| 1 | LK | P132833 | 26256705 | PAD_original | 2016-04-18 | 0.9 MB | procurement section, implementing agency, packaging vs DS00005 contracts |
| 2 | LK | P132833 | 26733306 | ISR_first | 2016-08-26 | 0.2 MB | first implementation ratings and procurement rating wording |
| 3 | LK | P132833 | 34098960 | ISR_latest | 2023-06-21 | 0.2 MB | final ratings; compare with ICR outcome |
| 4 | LK | P132833 | 34345370 | ICR | 2024-06-19 | 1.2 MB | results/outcome narrative and procurement lessons |
| 5 | LK | P132833 | 34407877 | ICRR | 2024-10-18 | 0.2 MB | IEG rating; check conflicts with ICR and latest ISR |
| 6 | LK | P132833 | 29493164 | PP_earliest | 2018-01-09 | 0.2 MB | earliest plan version; STEP references vs 20 verified contract matches |
| 7 | SN | P175325 | 33725374 | PAD_original | 2022-02-03 | 0.9 MB | active project; planned procurement methods vs awarded |
| 8 | SN | P175325 | 40125794 | ISR_latest | 2026-09-04 | 0.2 MB | latest ratings for an active project; procurement rating |
| 9 | ET | P148850 | 24440102 | PAD_original | 2015-04-27 | 0.9 MB | closed project without ICR; direct-selection and foreign-supplier context |
| 10 | ET | P148850 | 25821899 | PP_earliest | 2015-09-22 | 0.03 MB (exact) | earliest plan version; activity table layout for 12 verified matches |

## 7. Comparison with the previous selection (`selection_70/`)

| Measure | Previous 70 | Revised 70 |
|---|---|---|
| Countries | 64 countries + 6 regional projects (70 codes, 1 project each) | 10 countries, 7 projects each |
| Regional or multi-country projects | 6 | 0 |
| Closed / active | 45 / 25 | 48 / 22 |
| Sectors | 14 | 11 (7 per country) |
| Contracts in scope | 7,619 (median 44.5 per project) | 9,454 (median 80) |
| Documents | 510 (Tier 1: 380, Tier 2: 130) | 382 (single tier) |
| Mix | PAD 76, ISR 203, ICR 46, ICRR 45, PP 140 | PAD 70, ISR 140, ICR 46, ICRR 42, PP 84 |
| Projects with probe plan-to-contract matches | 10 (P181385 26, P175325 22, P132833 20, P500392 16, P173367 15, P148850 12, P165782 12, P175659 9, P176948 8, P145534 1) | 4 (P175325, P132833, P500392, P148850) |
| Shared projects | | 6 (P121019, P132833, P148850, P150357, P175325, P500392) |

**What the revision gains:**

- Within-country comparisons become possible. Projects share a borrower, a legal framework and implementing-agency patterns, so differences in method, review or timing can be attributed to the project rather than to the country.
- Country-level RAG answers can draw on 7 projects instead of 1.
- Every project is single-country with a valid country code, so there are no ambiguous regional records.
- Projects are larger (median 80 contracts instead of 44.5), which gives more procurement evidence per document.
- There are fewer PDFs, because only the first and latest ISR are kept and there is no Tier 2.
- Each country passed explicit viability thresholds (at least 2 ICRs, at least 2 active and at least 2 closed projects).

**Biases and losses to keep in mind:**

- **10 of 128 countries.** Results generalise to well-documented, high-volume borrowers, not to the whole portfolio. The score rewards many eligible projects, ICR coverage and STEP-format references, so it favours countries with mature procurement records.
- **Narrow income and regional mix.** There are no high-income borrowers and only one country is low income on FY2026 data (Burkina Faso). Six regions have 1–2 countries each, and South Asia rests on Sri Lanka alone.
- **Eligibility excludes smaller projects.** Requiring at least 10 contracts, 10 notices and 2 ISRs drops small, very new and very old projects, and only 7 projects are from FY2024 or later.
- **Fewer sectors.** 11 instead of 14 sectors overall.
- **Thinner version history.** Most projects have only their latest plan and the first and latest ISRs, so mid-course plan revisions are not covered except through 14 earliest-plan extras.
- **Language.** Taking English documents first under-represents French-language originals (Senegal, Burkina Faso). One French plan is included.
- **Selection used Gold procurement facts** (sector, method diversity and contract counts) but no outcome ratings, so ICR and ICRR ratings remain unbiased by the selection.
- **Fewer proven links.** Six previously verified projects drop out because they are regional or in countries not selected (for example P181385, South Sudan, 26 matches). Only 4 projects have verified plan-to-contract matches. For the other 66 the "expected" label is a country-level proxy until the PDFs are parsed.

## 8. Module 1 coverage and gaps

**Covered by this plan:**

- **Identification:** project, country, region, income, sector, FY and status for all 70 projects, joined to Gold by `project_id`.
- **Design intent:** the PAD procurement and implementation sections (70 PADs).
- **Implementation monitoring:** first and latest ISR (140), including procurement and overall ratings.
- **Completion and independent evaluation:** 46 ICRs and 42 ICRRs.
- **Planned procurement:** 84 procurement plans, with activity references that can be linked to DS00005 contracts.
- **Provenance:** WDS document ID, GUID, URLs, dates, version, report number and language preserved for every document.
- **Access:** confirmed by HEAD checks on every URL.

**Gaps:**

1. 22 active projects have no ICR or ICRR yet, and 4 closed projects lack an ICRR without lacking an ICR (P164336, P162042, P170329, P118518). Three closed projects lack both (P148850, P173776, P173858).
2. Bid evaluation reports, contract documents and complaints are not published in WDS, so award rationale is only available through notices, plans and ICR narrative.
3. Plan-to-contract linking is verified for 4 projects only. Older, pre-STEP plans (FY2016 and earlier) may not carry matchable references.
4. Only 18 of 382 file sizes are exact; the rest are estimates.
5. Supplier country is missing in Gold for West Bank and Gaza, so question 8 cannot use a foreign-supplier comparison.
6. Ethiopia has no FY2026 income classification.
7. Mid-life plan versions are excluded by design; they can be added per project if a question needs them.
8. Contract amendments and procurement post-reviews are not part of this corpus.

## 9. Decisions needed before any download

1. **Approve the 10 countries and 70 projects** (`PROJECT_SELECTION_MANIFEST_v2.csv`).
2. **Approve 382 documents (377 new downloads, about 162 MB)**, or cap at 380 by dropping the two earliest-plan extras listed in the summary.
3. **Approve the first batch of 10** (`FIRST_BATCH_10_v2.csv`) as the first download and manual inspection step, before the remaining 367.
4. **Decide on the French earliest plan** for P148062: keep it or drop it.
5. **Download location:** confirm the target folder for the PDFs. The proposal is a new folder `rag/corpus_v2/` with SHA-256 per file and the existing access log; existing local PDFs and `selection_70/` stay untouched.
