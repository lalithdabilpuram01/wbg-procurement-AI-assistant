import pandas as pd
import pytest

from wbdisc.procurement_plan import header_key, merge_header_variants, normalize_plan_status


def test_header_key_ignores_injected_spaces():
    assert header_key("Pro ces s Sta tus") == header_key("Process Status") == "processstatus"


def test_merge_header_variants_takes_first_non_empty_and_cleanest_name():
    df = pd.DataFrame({"Process Status": ["Signed", None, ""], "Process St atus": [None, "Canceled", ""],
                       "Method": ["RFB", "RFQ", "DIR"]})
    m = merge_header_variants(df)
    assert list(m.columns) == ["Process Status", "Method"]
    assert m["Process Status"].tolist() == ["Signed", "Canceled", None]


@pytest.mark.parametrize("raw,label", [
    ("Canceled", "cancelled"), ("Annulé", "cancelled"), ("Cancelado", "cancelled"),
    ("Signé", "signed"), ("Assinado", "signed"), ("Firmado", "signed"), ("Sign ed", "signed"),
    ("Completed", "completed"), ("Achevé", "completed"), ("Concluído", "completed"),
    ("Terminated", "terminated"), ("Résilié", "terminated"),
    ("Under Implement ation", "under/pending implementation"), ("En attente d'exécution", "under/pending implementation"),
    ("Under Review", "under review"),
    ("", "blank"), (None, "blank"), (float("nan"), "blank"),
    ("Eunti oanttente d'exéc", "unclassified"),  # overlapping-glyph garble: never guessed
    ("Edn'e xaéttceunttioen", "unclassified"),
])
def test_normalize_plan_status(raw, label):
    assert normalize_plan_status(raw) == label
