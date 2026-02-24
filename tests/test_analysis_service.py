"""Tests for app/services/analysis_service.py — Athena query service."""
import time
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_athena_service():
    """Return a fresh AthenaQueryService with a mocked athena client."""
    from app.services.analysis_service import AthenaQueryService

    svc = AthenaQueryService()
    svc._athena_client = MagicMock()
    return svc


def _make_paginator(pages):
    """
    Build a mock paginator whose paginate() returns an iterable of pages.
    Each page is a dict shaped like Athena's get_query_results response.
    """
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    return paginator


def _make_results_page(headers, rows, is_first_page=True):
    """
    Build a single page of Athena get_query_results response data.
    headers: list[str]
    rows: list[list[str]]  — each inner list is a row of values
    """
    header_row = {"Data": [{"VarCharValue": h} for h in headers]}
    data_rows = [
        {"Data": [{"VarCharValue": v} for v in row]} for row in rows
    ]
    all_rows = ([header_row] + data_rows) if is_first_page else data_rows
    return {"ResultSet": {"Rows": all_rows}}


# ---------------------------------------------------------------------------
# AthenaQueryService.execute_query
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    def test_calls_start_query_execution_with_correct_params(self):
        svc = _make_athena_service()
        svc._athena_client.start_query_execution.return_value = {
            "QueryExecutionId": "qid-123"
        }
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
        }

        svc.execute_query("SELECT 1", wait=False)

        svc._athena_client.start_query_execution.assert_called_once_with(
            QueryString="SELECT 1",
            QueryExecutionContext={"Database": svc.database},
            ResultConfiguration={"OutputLocation": svc.output_location},
        )

    def test_returns_query_execution_id(self):
        svc = _make_athena_service()
        svc._athena_client.start_query_execution.return_value = {
            "QueryExecutionId": "qid-abc"
        }

        result = svc.execute_query("SELECT 1", wait=False)
        assert result == "qid-abc"

    def test_wait_true_calls_wait_for_query(self):
        svc = _make_athena_service()
        svc._athena_client.start_query_execution.return_value = {
            "QueryExecutionId": "qid-wait"
        }

        with patch.object(svc, "_wait_for_query") as mock_wait:
            svc.execute_query("SELECT 1", wait=True)

        mock_wait.assert_called_once_with("qid-wait")

    def test_wait_false_does_not_call_wait_for_query(self):
        svc = _make_athena_service()
        svc._athena_client.start_query_execution.return_value = {
            "QueryExecutionId": "qid-no-wait"
        }

        with patch.object(svc, "_wait_for_query") as mock_wait:
            svc.execute_query("SELECT 1", wait=False)

        mock_wait.assert_not_called()

    def test_re_raises_exception_on_failure(self):
        svc = _make_athena_service()
        svc._athena_client.start_query_execution.side_effect = Exception("Athena error")

        with pytest.raises(Exception, match="Athena error"):
            svc.execute_query("SELECT 1", wait=False)


# ---------------------------------------------------------------------------
# AthenaQueryService._wait_for_query
# ---------------------------------------------------------------------------


class TestWaitForQuery:
    def test_succeeds_on_first_poll(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
        }

        result = svc._wait_for_query("qid-1")
        assert result == "SUCCEEDED"

    def test_polls_until_succeeded(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.side_effect = [
            {"QueryExecution": {"Status": {"State": "RUNNING"}}},
            {"QueryExecution": {"Status": {"State": "RUNNING"}}},
            {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
        ]

        with patch("app.services.analysis_service.time.sleep"):
            result = svc._wait_for_query("qid-2")

        assert result == "SUCCEEDED"
        assert svc._athena_client.get_query_execution.call_count == 3

    def test_raises_on_failed_status(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {
                "Status": {
                    "State": "FAILED",
                    "StateChangeReason": "Syntax error",
                }
            }
        }

        with pytest.raises(Exception, match="FAILED"):
            svc._wait_for_query("qid-fail")

    def test_raises_on_cancelled_status(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {
                "Status": {
                    "State": "CANCELLED",
                    "StateChangeReason": "User cancelled",
                }
            }
        }

        with pytest.raises(Exception, match="CANCELLED"):
            svc._wait_for_query("qid-cancel")

    def test_raises_timeout_error_when_max_wait_exceeded(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "RUNNING"}}
        }

        with patch("app.services.analysis_service.time.sleep"):
            with patch("app.services.analysis_service.time.time") as mock_time:
                # First call: start_time = 0; subsequent calls advance past max_wait=1
                mock_time.side_effect = [0, 0, 2]
                with pytest.raises(TimeoutError, match="timed out"):
                    svc._wait_for_query("qid-timeout", max_wait=1)

    def test_reason_included_in_exception_message(self):
        svc = _make_athena_service()
        svc._athena_client.get_query_execution.return_value = {
            "QueryExecution": {
                "Status": {
                    "State": "FAILED",
                    "StateChangeReason": "Table not found",
                }
            }
        }

        with pytest.raises(Exception, match="Table not found"):
            svc._wait_for_query("qid-reason")


# ---------------------------------------------------------------------------
# AthenaQueryService.get_query_results
# ---------------------------------------------------------------------------


class TestGetQueryResults:
    def test_returns_empty_list_when_no_rows(self):
        svc = _make_athena_service()
        # Header row only — no data rows
        page = _make_results_page(["col1", "col2"], [])
        paginator = _make_paginator([page])
        svc._athena_client.get_paginator.return_value = paginator

        result = svc.get_query_results("qid-empty")
        assert result == []

    def test_maps_headers_to_values_correctly(self):
        svc = _make_athena_service()
        page = _make_results_page(
            ["location", "temperature_c"],
            [["London", "12.0"], ["Paris", "15.5"]],
        )
        paginator = _make_paginator([page])
        svc._athena_client.get_paginator.return_value = paginator

        result = svc.get_query_results("qid-rows")
        assert len(result) == 2
        assert result[0] == {"location": "London", "temperature_c": "12.0"}
        assert result[1] == {"location": "Paris", "temperature_c": "15.5"}

    def test_handles_missing_varchar_value_as_none(self):
        svc = _make_athena_service()
        header_row = {"Data": [{"VarCharValue": "col1"}]}
        # A cell with no VarCharValue key at all
        data_row = {"Data": [{}]}
        page = {"ResultSet": {"Rows": [header_row, data_row]}}
        paginator = _make_paginator([page])
        svc._athena_client.get_paginator.return_value = paginator

        result = svc.get_query_results("qid-null")
        assert result[0]["col1"] is None

    def test_re_raises_exception_on_failure(self):
        svc = _make_athena_service()
        svc._athena_client.get_paginator.side_effect = Exception("Access denied")

        with pytest.raises(Exception, match="Access denied"):
            svc.get_query_results("qid-err")


# ---------------------------------------------------------------------------
# query_weather_by_temperature — SQL construction
# ---------------------------------------------------------------------------


class TestQueryWeatherByTemperature:
    def test_sql_contains_min_temp_filter(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            query_weather_by_temperature(min_temp=20.0)

        sql = mock_q.call_args[0][0]
        assert "20.0" in sql

    def test_sql_uses_default_min_temp_15(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            query_weather_by_temperature()

        sql = mock_q.call_args[0][0]
        assert "15.0" in sql

    def test_sql_includes_date_filter_when_date_provided(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            query_weather_by_temperature(date="2025-06-15")

        sql = mock_q.call_args[0][0]
        assert "2025-06-15" in sql

    def test_sql_excludes_date_filter_when_date_none(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            query_weather_by_temperature(date=None)

        sql = mock_q.call_args[0][0]
        assert "AND dt =" not in sql

    def test_returns_results_list(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        expected = [{"location": "London", "temperature_c": "22.0"}]
        with patch.object(athena_service, "query_and_get_results", return_value=expected):
            result = query_weather_by_temperature()

        assert result == expected

    def test_re_raises_on_exception(self):
        from app.services.analysis_service import athena_service, query_weather_by_temperature

        with patch.object(athena_service, "query_and_get_results", side_effect=Exception("fail")):
            with pytest.raises(Exception, match="fail"):
                query_weather_by_temperature()


# ---------------------------------------------------------------------------
# get_location_weather_trend — SQL construction
# ---------------------------------------------------------------------------


class TestGetLocationWeatherTrend:
    def test_sql_contains_location_name(self):
        from app.services.analysis_service import athena_service, get_location_weather_trend

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_location_weather_trend("Tokyo")

        sql = mock_q.call_args[0][0]
        assert "Tokyo" in sql

    def test_sql_contains_date_range(self):
        from app.services.analysis_service import athena_service, get_location_weather_trend
        from datetime import datetime, timedelta

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_location_weather_trend("London", days=7)

        sql = mock_q.call_args[0][0]
        assert "BETWEEN" in sql

    def test_returns_results_list(self):
        from app.services.analysis_service import athena_service, get_location_weather_trend

        expected = [{"date": "2025-06-01", "avg_temp_c": "18.5"}]
        with patch.object(athena_service, "query_and_get_results", return_value=expected):
            result = get_location_weather_trend("Berlin")

        assert result == expected

    def test_re_raises_on_exception(self):
        from app.services.analysis_service import athena_service, get_location_weather_trend

        with patch.object(athena_service, "query_and_get_results", side_effect=Exception("db error")):
            with pytest.raises(Exception, match="db error"):
                get_location_weather_trend("Paris")


# ---------------------------------------------------------------------------
# get_weather_analytics_summary — SQL construction
# ---------------------------------------------------------------------------


class TestGetWeatherAnalyticsSummary:
    def test_uses_today_date_when_none_provided(self):
        from app.services.analysis_service import athena_service, get_weather_analytics_summary
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_weather_analytics_summary()

        sql = mock_q.call_args[0][0]
        assert today in sql

    def test_uses_provided_date_in_sql(self):
        from app.services.analysis_service import athena_service, get_weather_analytics_summary

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_weather_analytics_summary(date="2025-01-15")

        sql = mock_q.call_args[0][0]
        assert "2025-01-15" in sql

    def test_returns_first_result_as_summary_dict(self):
        from app.services.analysis_service import athena_service, get_weather_analytics_summary

        row = {"unique_locations": "5", "avg_temperature": "18.2"}
        with patch.object(athena_service, "query_and_get_results", return_value=[row]):
            result = get_weather_analytics_summary()

        assert result == row

    def test_returns_empty_dict_when_no_results(self):
        from app.services.analysis_service import athena_service, get_weather_analytics_summary

        with patch.object(athena_service, "query_and_get_results", return_value=[]):
            result = get_weather_analytics_summary()

        assert result == {}

    def test_re_raises_on_exception(self):
        from app.services.analysis_service import athena_service, get_weather_analytics_summary

        with patch.object(athena_service, "query_and_get_results", side_effect=Exception("timeout")):
            with pytest.raises(Exception, match="timeout"):
                get_weather_analytics_summary()


# ---------------------------------------------------------------------------
# get_weather_by_condition — SQL construction
# ---------------------------------------------------------------------------


class TestGetWeatherByCondition:
    def test_sql_contains_condition_filter(self):
        from app.services.analysis_service import athena_service, get_weather_by_condition

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_weather_by_condition("Rain")

        sql = mock_q.call_args[0][0]
        assert "Rain" in sql

    def test_sql_uses_case_insensitive_like(self):
        from app.services.analysis_service import athena_service, get_weather_by_condition

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_weather_by_condition("Rain")

        sql = mock_q.call_args[0][0]
        assert "LOWER" in sql

    def test_sql_includes_date_filter_when_provided(self):
        from app.services.analysis_service import athena_service, get_weather_by_condition

        with patch.object(athena_service, "query_and_get_results", return_value=[]) as mock_q:
            get_weather_by_condition("Clear", date="2025-03-10")

        sql = mock_q.call_args[0][0]
        assert "2025-03-10" in sql

    def test_returns_results_list(self):
        from app.services.analysis_service import athena_service, get_weather_by_condition

        expected = [{"location": "London", "condition": "Rain"}]
        with patch.object(athena_service, "query_and_get_results", return_value=expected):
            result = get_weather_by_condition("Rain")

        assert result == expected

    def test_re_raises_on_exception(self):
        from app.services.analysis_service import athena_service, get_weather_by_condition

        with patch.object(athena_service, "query_and_get_results", side_effect=Exception("err")):
            with pytest.raises(Exception, match="err"):
                get_weather_by_condition("Rain")


# ---------------------------------------------------------------------------
# AthenaQueryService — lazy client initialisation
# ---------------------------------------------------------------------------


class TestAthenaClientLazyLoad:
    def test_athena_client_not_created_until_accessed(self):
        from app.services.analysis_service import AthenaQueryService

        svc = AthenaQueryService()
        assert svc._athena_client is None

    def test_athena_client_created_on_first_access(self):
        from app.services.analysis_service import AthenaQueryService

        svc = AthenaQueryService()
        with patch("boto3.client", return_value=MagicMock()) as mock_boto:
            _ = svc.athena_client
            mock_boto.assert_called_once_with("athena")

    def test_s3_client_created_on_first_access(self):
        from app.services.analysis_service import AthenaQueryService

        svc = AthenaQueryService()
        with patch("boto3.client", return_value=MagicMock()) as mock_boto:
            _ = svc.s3_client
            mock_boto.assert_called_once_with("s3")
