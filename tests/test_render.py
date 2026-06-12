from datetime import datetime
from zoneinfo import ZoneInfo

from aggregate import Cell
from render import _format_cell

KST = ZoneInfo("Asia/Seoul")


def test_format_cell_vacation():
    cell = Cell(vacation=True)
    assert _format_cell(cell) == "휴가"


def test_format_cell_checkin_and_checkout():
    cell = Cell(
        checkin=datetime(2026, 6, 1, 9, 0, tzinfo=KST),
        checkout=datetime(2026, 6, 1, 18, 30, tzinfo=KST),
    )
    assert _format_cell(cell) == "09:00–18:30"


def test_format_cell_checkin_with_virtual_checkout():
    cell = Cell(
        checkin=datetime(2026, 6, 1, 9, 0, tzinfo=KST),
        virtual_checkout=True,
    )
    assert _format_cell(cell) == "09:00–21:00"


def test_format_cell_empty():
    cell = Cell()
    assert _format_cell(cell) == ""
