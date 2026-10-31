import pytest
import os
import sys
import json
import httpx
from unittest.mock import patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOOLS_LIBRARY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules", "tools", "library")

TEST_CALENDAR_FILE = "test_calendar_events.json"


def execute_tool(tool_name, args):
    """Helper function to execute a tool with given arguments."""
    tool_path = os.path.join(TOOLS_LIBRARY_DIR, f"{tool_name}.py")
    if not os.path.exists(tool_path):
        raise FileNotFoundError(f"Tool {tool_name} not found at {tool_path}")
    
    with open(tool_path, "r", encoding="utf-8") as f:
        code = f.read()
    
    local_scope = {"args": args, "result": None, "json": json, "httpx": httpx}
    exec(code, local_scope)  # hygiene: allow tool execution in test
    return local_scope.get("result")


class TestCalculator:
    """Tests for Calculator tool."""
    
    def test_basic_addition(self):
        result = execute_tool("Calculator", {"expression": "2 + 3"})
        assert result == "5"
    
    def test_basic_subtraction(self):
        result = execute_tool("Calculator", {"expression": "10 - 4"})
        assert result == "6"
    
    def test_basic_multiplication(self):
        result = execute_tool("Calculator", {"expression": "6 * 7"})
        assert result == "42"
    
    def test_basic_division(self):
        result = execute_tool("Calculator", {"expression": "20 / 4"})
        assert result == "5.0"
    
    def test_power_operator(self):
        result = execute_tool("Calculator", {"expression": "2 ^ 3"})
        assert result == "8"
    
    def test_math_functions(self):
        result = execute_tool("Calculator", {"expression": "sqrt(16)"})
        assert result == "4.0"
    
    def test_math_constants(self):
        result = execute_tool("Calculator", {"expression": "pi"})
        assert str(result) == str(3.141592653589793)
    
    def test_complex_expression(self):
        result = execute_tool("Calculator", {"expression": "(10 + 5) * 2"})
        assert result == "30"
    
    def test_empty_expression(self):
        result = execute_tool("Calculator", {"expression": ""})
        assert "Error" in result
    
    def test_forbidden_keywords(self):
        result = execute_tool("Calculator", {"expression": "__import__('os').system('ls')"})
        assert "Error" in result
        assert "Unsafe" in result


class TestConversionCalculator:
    """Tests for ConversionCalculator tool."""
    
    def test_celsius_to_fahrenheit(self):
        result = execute_tool("ConversionCalculator", {"value": 0, "from_unit": "celsius", "to_unit": "fahrenheit"})
        assert "32" in result
    
    def test_fahrenheit_to_celsius(self):
        result = execute_tool("ConversionCalculator", {"value": 212, "from_unit": "fahrenheit", "to_unit": "celsius"})
        assert "100" in result
    
    def test_meters_to_feet(self):
        result = execute_tool("ConversionCalculator", {"value": 1, "from_unit": "meters", "to_unit": "feet"})
        assert "3.2808" in result
    
    def test_kilometers_to_miles(self):
        result = execute_tool("ConversionCalculator", {"value": 1, "from_unit": "km", "to_unit": "mi"})
        assert "0.6214" in result
    
    def test_kg_to_pounds(self):
        result = execute_tool("ConversionCalculator", {"value": 1, "from_unit": "kg", "to_unit": "lb"})
        assert "2.2046" in result
    
    def test_liters_to_gallons(self):
        result = execute_tool("ConversionCalculator", {"value": 1, "from_unit": "liters", "to_unit": "gallons"})
        assert "0.264" in result
    
    def test_unsupported_conversion(self):
        result = execute_tool("ConversionCalculator", {"value": 100, "from_unit": "celsius", "to_unit": "meters"})
        assert "Unsupported" in result
    
    def test_invalid_value(self):
        result = execute_tool("ConversionCalculator", {"value": "abc", "from_unit": "celsius", "to_unit": "fahrenheit"})
        assert "Invalid" in result


class TestCurrencyConverter:
    """Tests for CurrencyConverter tool."""
    
    @patch('httpx.get')
    def test_same_currency(self, mock_get):
        result = execute_tool("CurrencyConverter", {"amount": 100, "from_currency": "USD", "to_currency": "USD"})
        assert "100.0 USD is 100.0 USD" in result
        mock_get.assert_not_called()
    
    @patch('httpx.get')
    def test_successful_conversion(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"rates": {"EUR": 0.85}, "date": "2024-01-01"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("CurrencyConverter", {"amount": 100, "from_currency": "USD", "to_currency": "EUR"})
        assert "85" in result
    
    @patch('httpx.get')
    def test_missing_arguments(self, mock_get):
        result = execute_tool("CurrencyConverter", {"amount": 100, "from_currency": "USD"})
        assert "Error" in result
    
    @patch('httpx.get')
    def test_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        
        result = execute_tool("CurrencyConverter", {"amount": 100, "from_currency": "USD", "to_currency": "EUR"})
        assert "Error" in result or "failed" in result.lower()


class TestTimeZoneConverter:
    """Tests for TimeZoneConverter tool."""
    
    def test_current_time_conversion(self):
        result = execute_tool("TimeZoneConverter", {
            "source_tz": "America/New_York",
            "target_tz": "Europe/London"
        })
        assert "America/New_York" in result
        assert "Europe/London" in result
    
    def test_specific_time_conversion(self):
        result = execute_tool("TimeZoneConverter", {
            "time_string": "2024-01-01T12:00:00",
            "source_tz": "UTC",
            "target_tz": "America/New_York"
        })
        assert "UTC" in result
        assert "America/New_York" in result
    
    def test_missing_source_tz(self):
        result = execute_tool("TimeZoneConverter", {
            "target_tz": "Europe/London"
        })
        assert "Error" in result
    
    def test_missing_target_tz(self):
        result = execute_tool("TimeZoneConverter", {
            "source_tz": "America/New_York"
        })
        assert "Error" in result


class TestFetchURL:
    """Tests for FetchURL tool."""
    
    @patch('httpx.get')
    def test_successful_fetch(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("FetchURL", {"url": "https://example.com"})
        assert "Hello World" in result
    
    @patch('httpx.get')
    def test_missing_url(self, mock_get):
        result = execute_tool("FetchURL", {})
        assert "Error" in result
    
    @patch('httpx.get')
    def test_html_stripping(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><head><style>body{color:red;}</style></head><body><script>alert('x')</script><p>Test</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("FetchURL", {"url": "https://example.com"})
        assert "Test" in result
        assert "<script>" not in result
        assert "<style>" not in result
    
    @patch('httpx.get')
    def test_fetch_error(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        
        result = execute_tool("FetchURL", {"url": "https://example.com"})
        assert "Error" in result


class TestSystemTime:
    """Tests for SystemTime tool."""
    
    def test_returns_current_time(self):
        result = execute_tool("SystemTime", {})
        assert "Current System Time:" in result
        current_year = str(datetime.now().year)
        assert current_year in result


class TestWeather:
    """Tests for Weather tool."""
    
    @patch('httpx.get')
    def test_successful_weather_fetch(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "current_condition": [{
                "temp_C": "25",
                "weatherDesc": [{"value": "Sunny"}],
                "FeelsLikeC": "27"
            }]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("Weather", {"location": "London"})
        assert "25" in result
        assert "Sunny" in result
    
    @patch('httpx.get')
    def test_missing_location(self, mock_get):
        result = execute_tool("Weather", {})
        assert "Please specify" in result
    
    @patch('httpx.get')
    def test_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        
        result = execute_tool("Weather", {"location": "London"})
        assert "error" in result.lower()


class TestSendEmail:
    """Tests for SendEmail tool."""
    
    @patch.dict(os.environ, {"SMTP_EMAIL": "test@example.com", "SMTP_PASSWORD": "testpass"})
    @patch('smtplib.SMTP')
    def test_successful_send(self, mock_smtp):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
        
        result = execute_tool("SendEmail", {
            "to_email": "recipient@example.com",
            "subject": "Test Subject",
            "body": "Test Body"
        })
        assert "Success" in result
        mock_server.send_message.assert_called_once()
    
    @patch.dict(os.environ, {}, clear=False)
    def test_missing_credentials(self):
        env_backup = os.environ.get("SMTP_EMAIL"), os.environ.get("SMTP_PASSWORD")
        os.environ.pop("SMTP_EMAIL", None)
        os.environ.pop("SMTP_PASSWORD", None)
        
        try:
            result = execute_tool("SendEmail", {
                "to_email": "recipient@example.com",
                "subject": "Test",
                "body": "Test"
            })
            assert "Error" in result
            assert "credentials" in result.lower()
        finally:
            if env_backup[0]:
                os.environ.update({"SMTP_EMAIL": env_backup[0]})
            if env_backup[1]:
                os.environ.update({"SMTP_PASSWORD": env_backup[1]})
    
    @patch.dict(os.environ, {"SMTP_EMAIL": "test@example.com", "SMTP_PASSWORD": "testpass"})
    @patch('smtplib.SMTP')
    def test_missing_arguments(self, mock_smtp):
        result = execute_tool("SendEmail", {"to_email": "recipient@example.com"})
        assert "Error" in result


class TestSaveReminder:
    """Tests for SaveReminder tool."""
    
    def test_save_reminder_with_time(self, monkeypatch):
        import sys
        import os
        
        test_events = []
        
        class MockEventManager:
            def add_event(self, title, time_str):
                event = {"id": "1", "title": title, "start_time": time_str}
                test_events.append(event)
                return event
        
        monkeypatch.setattr("modules.calendar.events.event_manager", MockEventManager())
        
        result = execute_tool("SaveReminder", {
            "title": "Test Reminder",
            "time": "2024-12-25 10:00"
        })
        assert "Test Reminder" in result
        assert "saved" in result.lower()
    
    def test_save_reminder_without_time(self, monkeypatch):
        class MockEventManager:
            def add_event(self, title, time_str):
                return {"id": "1", "title": title, "start_time": time_str}
        
        monkeypatch.setattr("modules.calendar.events.event_manager", MockEventManager())
        
        result = execute_tool("SaveReminder", {"title": "Test Reminder"})
        assert "Test Reminder" in result
    
    def test_save_reminder_missing_title(self):
        result = execute_tool("SaveReminder", {"time": "2024-12-25 10:00"})
        assert "Error" in result


class TestCheckCalendar:
    """Tests for CheckCalendar tool."""
    
    def test_get_upcoming_events(self, monkeypatch):
        class MockEventManager:
            def get_upcoming(self, limit=10):
                return [
                    {"title": "Meeting 1", "start_time": "2024-12-25 10:00"},
                    {"title": "Meeting 2", "start_time": "2024-12-26 14:00"}
                ]
        
        monkeypatch.setattr("modules.calendar.events.event_manager", MockEventManager())
        
        result = execute_tool("CheckCalendar", {})
        assert "Meeting 1" in result
        assert "Meeting 2" in result
    
    def test_get_events_by_date(self, monkeypatch):
        class MockEventManager:
            def get_events_by_date(self, date_str):
                return [{"title": "Meeting", "start_time": f"{date_str} 10:00"}]
        
        monkeypatch.setattr("modules.calendar.events.event_manager", MockEventManager())
        
        result = execute_tool("CheckCalendar", {"date": "2024-12-25"})
        assert "Meeting" in result
    
    def test_no_events_found(self, monkeypatch):
        class MockEventManager:
            def get_upcoming(self, limit=10):
                return []
        
        monkeypatch.setattr("modules.calendar.events.event_manager", MockEventManager())
        
        result = execute_tool("CheckCalendar", {})
        assert "No upcoming events" in result


class TestYouTubeTranscript:
    """Tests for YouTubeTranscript tool."""
    
    def test_missing_url(self):
        result = execute_tool("YouTubeTranscript", {})
        assert "Error" in result
    
    def test_invalid_url_no_video_id(self):
        result = execute_tool("YouTubeTranscript", {"url": "https://invalid-url.com"})
        assert "Error" in result
        assert "Could not extract" in result
    
    def test_youtube_watch_url_parsing(self):
        result = execute_tool("YouTubeTranscript", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert "Error" in result or "transcript" in result.lower() or "import" in result.lower()
    
    def test_youtu_be_url_parsing(self):
        result = execute_tool("YouTubeTranscript", {"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert "Error" in result or "transcript" in result.lower() or "import" in result.lower()


class TestWikipediaLookup:
    """Tests for WikipediaLookup tool."""
    
    @patch('httpx.get')
    def test_successful_summary_lookup(self, mock_get):
        mock_response_search = MagicMock()
        mock_response_search.json.return_value = {
            "query": {
                "search": [{"title": "Python (programming language)", "pageid": 1}]
            }
        }
        
        mock_response_content = MagicMock()
        mock_response_content.json.return_value = {
            "query": {
                "pages": {
                    "1": {"extract": "Python is a high-level programming language."}
                }
            }
        }
        
        mock_get.side_effect = [mock_response_search, mock_response_content]
        
        result = execute_tool("WikipediaLookup", {"query": "Python programming"})
        assert "Python" in result
        assert "programming language" in result
    
    @patch('httpx.get')
    def test_full_article_mode(self, mock_get):
        mock_response_search = MagicMock()
        mock_response_search.json.return_value = {
            "query": {
                "search": [{"title": "Test", "pageid": 1}]
            }
        }
        
        mock_response_content = MagicMock()
        mock_response_content.json.return_value = {
            "query": {
                "pages": {
                    "1": {"extract": "Full article content here."}
                }
            }
        }
        
        mock_get.side_effect = [mock_response_search, mock_response_content]
        
        result = execute_tool("WikipediaLookup", {"query": "Test", "mode": "full"})
        assert "Full article content" in result
    
    def test_missing_query(self):
        result = execute_tool("WikipediaLookup", {})
        assert "Error" in result
        assert "query" in result.lower()
    
    @patch('httpx.get')
    def test_no_results_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"query": {"search": []}}
        
        mock_get.return_value = mock_response
        
        result = execute_tool("WikipediaLookup", {"query": "NonexistentTopicXYZ123"})
        assert "No Wikipedia articles found" in result
    
    @patch('httpx.get')
    def test_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        
        result = execute_tool("WikipediaLookup", {"query": "Test"})
        assert "Error" in result


class TestArXivSearch:
    """Tests for ArXivSearch tool."""
    
    @patch('httpx.get')
    def test_successful_search(self, mock_get):
        xml_response = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>Deep Learning for Natural Language Processing</title>
                <summary>This is a paper about deep learning.</summary>
                <published>2024-01-15T00:00:00Z</published>
                <author><name>John Doe</name></author>
                <link title="pdf" href="https://arxiv.org/pdf/2401.00001.pdf"/>
            </entry>
        </feed>'''
        
        mock_response = MagicMock()
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("ArXivSearch", {"query": "deep learning"})
        assert "Deep Learning" in result
        assert "John Doe" in result
        assert "arxiv.org/pdf" in result
    
    def test_missing_query(self):
        result = execute_tool("ArXivSearch", {})
        assert "Error" in result
        assert "query" in result.lower()
    
    @patch('httpx.get')
    def test_no_results_found(self, mock_get):
        xml_response = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
        </feed>'''
        
        mock_response = MagicMock()
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("ArXivSearch", {"query": "xyznonexistent123"})
        assert "No papers found" in result
    
    @patch('httpx.get')
    def test_custom_max_results(self, mock_get):
        xml_response = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>Test Paper</title>
                <summary>Summary</summary>
                <published>2024-01-15T00:00:00Z</published>
                <author><name>Author</name></author>
            </entry>
        </feed>'''
        
        mock_response = MagicMock()
        mock_response.text = xml_response
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        
        result = execute_tool("ArXivSearch", {"query": "test", "max_results": 10})
        assert "Test Paper" in result
        mock_get.assert_called_once()
    
    @patch('httpx.get')
    def test_api_error(self, mock_get):
        mock_get.side_effect = Exception("Connection timeout")
        
        result = execute_tool("ArXivSearch", {"query": "test"})
        assert "Error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
