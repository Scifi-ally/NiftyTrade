"""Unit tests for DhanHQ Adapter and Dhan TickStream."""
import pytest
from unittest.mock import MagicMock, patch
from app.adapter.dhan import DhanAdapter
from app.core.exceptions import AuthenticationError

def test_dhan_adapter_missing_credentials():
    adapter = DhanAdapter(client_id="", access_token="")
    with pytest.raises(AuthenticationError):
        adapter.login()

def test_dhan_adapter_mock_login_success():
    adapter = DhanAdapter(client_id="1000000000", access_token="mock_token_abc")
    with patch("app.adapter.dhan.dhanhq") as mock_dhan_cls:
        mock_instance = MagicMock()
        mock_instance.get_fund_limits.return_value = {
            "status": "success",
            "data": {"availabelBalance": 250000.0}
        }
        mock_dhan_cls.return_value = mock_instance

        assert adapter.login() is True
        assert adapter.is_logged_in() is True
        assert adapter.get_funds() == 250000.0

def test_dhan_adapter_totp_auto_login_success():
    adapter = DhanAdapter(
        client_id="1000000000",
        access_token="",
        pin="123456",
        totp_secret="JBSWY3DPEHPK3PXP"
    )
    with patch("app.adapter.dhan.DhanLogin") as mock_login_cls, \
         patch("app.adapter.dhan.dhanhq") as mock_dhan_cls:
        
        mock_login_inst = MagicMock()
        mock_login_inst.generate_token.return_value = {
            "status": "success",
            "accessToken": "auto_generated_jwt_token_123"
        }
        mock_login_cls.return_value = mock_login_inst

        mock_dhan_inst = MagicMock()
        mock_dhan_inst.get_fund_limits.return_value = {
            "status": "success",
            "data": {"availabelBalance": 180000.0}
        }
        mock_dhan_cls.return_value = mock_dhan_inst

        assert adapter.login() is True
        assert adapter.access_token == "auto_generated_jwt_token_123"
        assert adapter.is_logged_in() is True
        assert adapter.get_funds() == 180000.0

def test_dhan_adapter_get_candles_parsing():
    adapter = DhanAdapter(client_id="1000000000", access_token="mock_token_abc")
    adapter._is_logged_in = True
    adapter._client = MagicMock()

    # Mock Dhan intraday_minute_data response
    adapter._client.intraday_minute_data.return_value = {
        "status": "success",
        "data": {
            "open": [25000.0, 25010.0],
            "high": [25020.0, 25030.0],
            "low": [24990.0, 25005.0],
            "close": [25010.0, 25025.0],
            "volume": [1000, 1500],
            "start_Time": [1700000000, 1700000060]
        }
    }

    candles = adapter.get_candles(
        exchange="NSE_FNO",
        symbol_token="35008",
        interval="ONE_MINUTE",
        from_date="2026-10-01",
        to_date="2026-10-09"
    )

    assert len(candles) == 2
    assert candles[0]["open"] == 25000.0
    assert candles[0]["close"] == 25010.0
    assert candles[1]["high"] == 25030.0
    assert candles[1]["volume"] == 1500.0
