"""The documentation checker must actually catch drift, and the README must pass it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import doc_check  # noqa: E402

SAMPLE = """
| 自动化测试 | **12 项全部通过** |
本机最新结果：**12 passed**。
| `tests/test_alpha.py` | 7 | covers alpha |
| `tests/test_beta.py` | 5 | covers beta |
"""


def test_the_table_and_total_are_parsed():
    rows, totals = doc_check.parse_readme(SAMPLE)
    assert rows == {'test_alpha.py': 7, 'test_beta.py': 5}
    assert totals == [12]


def test_a_matching_table_reports_nothing():
    rows, totals = doc_check.parse_readme(SAMPLE)
    actual = {'test_alpha.py': 7, 'test_beta.py': 5}
    assert doc_check.compare(rows, actual, totals) == []


def test_a_wrong_count_is_caught():
    rows, totals = doc_check.parse_readme(SAMPLE)
    problems = doc_check.compare(rows, {'test_alpha.py': 9, 'test_beta.py': 5}, totals)
    assert any('test_alpha.py: README says 7, pytest collects 9' in row for row in problems)
    assert any('total' in row for row in problems), 'the total is wrong too'


def test_a_missing_row_is_caught():
    rows, totals = doc_check.parse_readme(SAMPLE)
    problems = doc_check.compare(rows, {'test_alpha.py': 7, 'test_beta.py': 5, 'test_gamma.py': 1},
                                [13])
    assert any('test_gamma.py: missing from the README table' in row for row in problems)


def test_a_row_for_a_file_that_does_not_exist_is_caught():
    rows, totals = doc_check.parse_readme(SAMPLE)
    problems = doc_check.compare(rows, {'test_alpha.py': 7}, totals)
    assert any('no such test file exists' in row for row in problems)


def test_a_missing_total_is_caught():
    rows, _ = doc_check.parse_readme("| `tests/test_alpha.py` | 7 | x |")
    problems = doc_check.compare(rows, {'test_alpha.py': 7}, [])
    assert any('states no total' in row for row in problems)


def test_the_real_readme_matches_the_real_suite():
    """The guard that matters: run the actual comparison."""
    declared, totals = doc_check.parse_readme(doc_check.README.read_text(encoding='utf-8'))
    actual = doc_check.collect_actual()
    assert actual, 'pytest collection produced nothing'
    assert doc_check.compare(declared, actual, totals) == []
