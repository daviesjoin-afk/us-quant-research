from PySide6.QtCore import Qt

import pytest

from us_quant.table_models import ImmutableRowsTableModel


def test_rows_are_atomic_validated_and_sort_numeric_values() -> None:
    model = ImmutableRowsTableModel(("symbol", "pnl"))
    model.set_rows((("BBB", "$10.00"), ("AAA", "$2.00")))

    assert model.rowCount() == 2
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "BBB"
    assert model.data(model.index(0, 1), Qt.ToolTipRole) == "$10.00"
    model.sort(1, Qt.AscendingOrder)
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "AAA"

    with pytest.raises(ValueError, match="width"):
        model.set_rows((("only-one-column",),))
