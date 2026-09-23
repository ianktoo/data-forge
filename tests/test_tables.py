"""#69: HTML tables as structured rows."""
from __future__ import annotations

import csv
import io

from dataforge.collectors.tables import extract_tables

# The shape of https://www.husd.us/schools/middle-schools: a header row of
# bold <td> cells (no <th>), multi-line cells, non-breaking spaces.
HUSD_LIKE = """<table border="1"><tbody>
<tr><td><p><strong>Middle Schools</strong></p></td><td><p><strong>&nbsp;Address</strong></p></td>
    <td><p><strong>Phone</strong></p></td><td><p><strong>&nbsp;Fax</strong></p></td></tr>
<tr><td><p><a href="/fs/pages/2620">Anthony Ochoa</a></p></td>
    <td><p>2121 Depot Road</p><p>Hayward, CA&nbsp;94545-2428</p></td>
    <td><p>&nbsp;(510) 723-3130</p></td><td><p>&nbsp;(510) 786-0559</p></td></tr>
<tr><td><p><a href="/fs/pages/2783">Bret Harte</a></p></td>
    <td><p>1047 E Street</p><p>Hayward, CA 94544-5210</p></td>
    <td><p>(510) 723-3100</p></td><td><p>(510)&nbsp;781-6120</p></td></tr>
</tbody></table>"""


def test_bold_td_header_row_like_husd():
    (t,) = extract_tables(HUSD_LIKE)
    assert t.headers == ["Middle Schools", "Address", "Phone", "Fax"]
    assert t.rows == [
        ["Anthony Ochoa", "2121 Depot Road Hayward, CA 94545-2428", "(510) 723-3130", "(510) 786-0559"],
        ["Bret Harte", "1047 E Street Hayward, CA 94544-5210", "(510) 723-3100", "(510) 781-6120"],
    ]
    assert t.records()[1]["Phone"] == "(510) 723-3100"


def test_th_header_and_csv_round_trip():
    (t,) = extract_tables("""<table><caption>Prices</caption>
        <thead><tr><th>Item</th><th>Price, USD</th></tr></thead>
        <tbody><tr><td>Tea</td><td>3</td></tr><tr><td>"Cake", big</td><td>5</td></tr></tbody></table>""")
    assert t.caption == "Prices"
    rows = list(csv.reader(io.StringIO(t.to_csv())))
    assert rows == [["Item", "Price, USD"], ["Tea", "3"], ['"Cake", big', "5"]]


def test_colspan_and_rowspan_fill_every_column():
    (t,) = extract_tables("""<table>
        <tr><th>Region</th><th>City</th><th>Q1</th><th>Q2</th></tr>
        <tr><td rowspan="2">West</td><td>Hayward</td><td colspan="2">closed</td></tr>
        <tr><td>Fremont</td><td>1</td><td>2</td></tr>
        </table>""")
    assert t.rows == [["West", "Hayward", "closed", "closed"], ["West", "Fremont", "1", "2"]]


def test_no_header_gets_numbered_columns():
    (t,) = extract_tables("<table><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></table>")
    assert t.headers == []
    assert t.records() == [{"column_1": "a", "column_2": "1"}, {"column_1": "b", "column_2": "2"}]


def test_all_bold_table_has_no_header():
    (t,) = extract_tables("<table><tr><td><b>a</b></td><td><b>1</b></td></tr>"
                          "<tr><td><b>b</b></td><td><b>2</b></td></tr></table>")
    assert t.headers == [] and len(t.rows) == 2


def test_duplicate_and_empty_header_names_are_made_unique():
    (t,) = extract_tables("<table><tr><th>Phone</th><th>Phone</th><th></th></tr>"
                          "<tr><td>1</td><td>2</td><td>3</td></tr></table>")
    assert t.headers == ["Phone", "Phone 2", "column_3"]


def test_nested_tables_are_separate():
    tables = extract_tables("""<table>
        <tr><th>Name</th><th>Details</th></tr>
        <tr><td>A</td><td><table><tr><th>k</th><th>v</th></tr><tr><td>x</td><td>1</td></tr></table></td></tr>
        <tr><td>B</td><td>plain</td></tr></table>""")
    outer, inner = tables
    assert outer.headers == ["Name", "Details"]
    assert outer.rows == [["A", ""], ["B", "plain"]]
    assert inner.headers == ["k", "v"] and inner.rows == [["x", "1"]]


def test_layout_tables_are_skipped():
    assert extract_tables("<table><tr><td>Just one cell of layout</td></tr></table>") == []
    assert extract_tables("<table><tr><td>a</td><td>b</td></tr></table>") == []   # one row, no header
    assert extract_tables("<table><tr><td>x</td></tr><tr><td>y</td></tr></table>") == []  # one column


def test_ragged_rows_are_padded_and_spans_capped():
    (t,) = extract_tables('<table><tr><th>a</th><th>b</th><th>c</th></tr>'
                          '<tr><td>1</td></tr><tr><td colspan="99999">wide</td></tr></table>')
    assert t.rows[0] == ["1", "", ""]
    assert len(t.rows[1]) <= 100
