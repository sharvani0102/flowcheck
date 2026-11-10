import json

from flowcheck.cli import main

from conftest import FIXTURES


def test_exit_code_clean(capsys):
    assert main([str(FIXTURES / "fc002" / "clean_both_branches.yaml")]) == 0


def test_exit_code_warnings_only_is_zero(capsys):
    assert main([str(FIXTURES / "fc002" / "buggy_if_one_branch.yaml")]) == 0


def test_exit_code_errors(capsys):
    assert main([str(FIXTURES / "fc001" / "buggy_undefined.yaml")]) == 1
    out = capsys.readouterr().out
    assert "error FC001 [write_report (task)] var 'summary'" in out
    assert "hint:" in out


def test_exit_code_parse_failure_wins(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("workflow: [unclosed")
    code = main([str(bad), str(FIXTURES / "fc001" / "buggy_undefined.yaml")])
    assert code == 2
    assert "parse error" in capsys.readouterr().out


def test_json_output(capsys):
    main([str(FIXTURES / "fc003" / "buggy_parallel.yaml"), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    f = data["files"][0]
    assert f["verdict"] == "fail"
    assert {x["variable"] for x in f["findings"]} == {"pros", "cons"}
    assert data["summary"]["errors"] == 2


def test_directory_argument(capsys):
    code = main([str(FIXTURES / "fc002")])
    assert code == 0
    assert "file(s)" in capsys.readouterr().out
