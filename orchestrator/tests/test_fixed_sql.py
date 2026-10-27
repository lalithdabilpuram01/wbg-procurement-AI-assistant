"""Every fixed SQL template of the ML and planning workers passes the Structured Data Agent's validator for the app
principal, with the row cap `gateway_sql` uses (offline: registry metadata only, nothing is executed)."""
from __future__ import annotations

import pytest

from ml.serving.gold import INCOME_SQL, PRIOR_TENDERS_SQL, PROJECT_SQL, REGION_SQL
from structured_agent.config import Settings
from structured_agent.service import RequestContext, StructuredDataService
from wbg_orchestrator.runtime import REGISTRY_PRINCIPAL, WORKSPACE
from wbg_orchestrator.workers.planning import _SCOPE, CANDIDATES_SQL, GOVERNANCE_SQL, TEMPLATES
from wbg_orchestrator.workers.prediction import GATEWAY_MAX_ROWS

COUNTRY = {"country_code": "ET"}
CASES = [("project", PROJECT_SQL, {"project_id": "P148850"}), ("income", INCOME_SQL, COUNTRY),
         ("prior_tenders", PRIOR_TENDERS_SQL, {"project_id": "P148850", "tender_date": "2026-12-01"}),
         ("region", REGION_SQL, COUNTRY), ("candidates", CANDIDATES_SQL, {"region_name": "Eastern and Southern Africa"}),
         ("governance", GOVERNANCE_SQL, COUNTRY)]
CASES += [(f"{name}/{scope}", template.format(scope=_SCOPE[scope]),
           {**({"country_code": "ET"} if scope == "country" else {"region_name": "Eastern and Southern Africa"}),
            "category": "Works", "since_fy": 2016})
          for name, template in TEMPLATES.items() for scope in _SCOPE]


class NoExecutor:
    def check_credential(self):
        raise AssertionError("validation only")

    def execute(self, *args, **kwargs):
        raise AssertionError("validation only")


@pytest.fixture(scope="module")
def service() -> StructuredDataService:
    return StructuredDataService.from_environment(Settings(), executor=NoExecutor())


@pytest.mark.parametrize("name, sql, parameters", CASES, ids=[c[0] for c in CASES])
def test_fixed_sql_is_approved_for_the_app_principal(service, name, sql, parameters):
    principal = service._policy.resolve(REGISTRY_PRINCIPAL, WORKSPACE)        # as the gateway resolves the app principal
    report = service._validator.validate(sql, parameters, principal, GATEWAY_MAX_ROWS)
    assert report.approved, [e.message for e in report.errors]
