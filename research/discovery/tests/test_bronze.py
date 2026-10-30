import gzip
import io

from wbdisc.bronze import iter_csv_records, parse_record, select_csv, select_jsonl

KEEP = {"P000001", "P000003"}


def test_iter_csv_records_keeps_quoted_line_breaks_and_crlf():
    text = 'a,b\r\n1,"x\r\ny"\r\n2,"he said ""hi"""\r\n3,z'
    recs = list(iter_csv_records(io.StringIO(text, newline="")))
    assert recs == ["a,b\r\n", '1,"x\r\ny"\r\n', '2,"he said ""hi"""\r\n', "3,z"]
    assert "".join(recs) == text
    assert parse_record(recs[1]) == ["1", "x\r\ny"]


def test_select_csv_is_byte_preserving(tmp_path):
    src = tmp_path / "src.csv"
    raw = ("\ufeffAs of Date,Project ID,Desc\r\n"
           '10/20/2026,P000001,"multi\r\nline, quoted"\r\n'
           "10/20/2026,P000002,other\r\n"
           "10/20/2026,P000001,  spaced  \r\n"          # exact duplicate key kept
           "10/20/2026, P000003,near\r\n"              # near match: not selected
           "10/20/2026,89101556,bad\r\n"
           "10/20/2026,P000003,last\r\n")
    src.write_bytes(raw.encode("utf-8"))
    dst = tmp_path / "out.csv"
    res = select_csv(src, dst, "Project ID", KEEP)
    out = dst.read_bytes().decode("utf-8")
    assert out == ("\ufeffAs of Date,Project ID,Desc\r\n"
                   '10/20/2026,P000001,"multi\r\nline, quoted"\r\n'
                   "10/20/2026,P000001,  spaced  \r\n"
                   "10/20/2026,P000003,last\r\n")
    assert res["stats"] == {"source_records": 6, "selected_records": 3, "other_project": 1,
                            "near_match_not_selected": 1, "key_not_a_project_id": 1}
    assert res["selected_keys"] == {"P000001": 2, "P000003": 1}
    assert res["utf8_bom"] and res["line_terminator"] == "CRLF"


def test_select_jsonl_copies_raw_lines_from_gzip(tmp_path):
    lines = [b'{"id": "P000001", "x": [1, 2.50], "s": "caf\\u00e9"}\n', b'{"id": "P000002"}\n',
             b'{"id":"P000003","nested":{"a":null}}\n']
    src = tmp_path / "src.jsonl.gz"
    with gzip.open(src, "wb") as f:
        f.writelines(lines)
    dst = tmp_path / "out.jsonl"
    res = select_jsonl(src, dst, "id", KEEP)
    assert dst.read_bytes() == lines[0] + lines[2]
    assert res["stats"]["selected_records"] == 2 and res["stats"]["source_records"] == 3
