def test_balance_sheet_balances(parsed_filing):
    assert parsed_filing["total_assets"] == (
        parsed_filing["total_liabilities"] + parsed_filing["total_equity"]
    )

def test_no_negative_revenue(parsed_filing):
    assert parsed_filing["revenue"] >= 0