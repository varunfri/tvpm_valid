import unittest
import os
import io
import pandas as pd
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the FastAPI application
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.main import app

class TestTVPMValidation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
        # Create a mock Excel file in memory
        df = pd.DataFrame({
            "TVPM_ID_COL": ["TVPM-1001", "TVPM-1002", "TVPM-1003", "TVPM-1004"],
            "Other_Col": ["A", "B", "C", "D"]
        })
        self.excel_data = io.BytesIO()
        with pd.ExcelWriter(self.excel_data, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
        self.excel_data.seek(0)

    @patch("httpx.AsyncClient.get")
    def test_fields_preview_endpoint(self, mock_get):
        # Configure mock responses for JIRA API
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "key": "TVPM-1001",
            "fields": {
                "summary": "Implement feature A",
                "customfield_10100": "O",
                "customfield_10101": {"value": "THE", "id": "123"}
            }
        }
        mock_get.return_value = mock_response

        # Execute request
        files = {
            "file": ("test.xlsx", self.excel_data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        data = {
            "column_name": "TVPM_ID_COL",
            "jira_base_url": "https://mockjira.example.com",
            "jira_pat": "mock_pat"
        }
        
        response = self.client.post("/api/fields-preview", files=files, data=data)
        
        self.assertEqual(response.status_code, 200)
        res_json = response.json()
        self.assertEqual(res_json["tvpm_id_used"], "TVPM-1001")
        self.assertIn("fields.customfield_10100", res_json["fields"])
        self.assertEqual(res_json["fields"]["fields.customfield_10100"]["value"], "O")
        self.assertEqual(res_json["fields"]["fields.customfield_10101"]["value"], "THE")

    @patch("httpx.AsyncClient.get")
    def test_validate_endpoint(self, mock_get):
        # Setup mock issue payloads for each issue key
        def mock_jira_responses(url, *args, **kwargs):
            key = url.split("/")[-1]
            mock_res = MagicMock()
            mock_res.status_code = 200
            
            if key == "TVPM-1001":
                mock_res.json.return_value = {
                    "key": key, "fields": {"customfield_10100": "O"}
                }
            elif key == "TVPM-1002":
                mock_res.json.return_value = {
                    "key": key, "fields": {"customfield_10100": "X"}
                }
            elif key == "TVPM-1003":
                mock_res.json.return_value = {
                    "key": key, "fields": {"customfield_10100": "THE"}
                }
            elif key == "TVPM-1004":
                # Test default/unknown status
                mock_res.json.return_value = {
                    "key": key, "fields": {"customfield_10100": "Unknown"}
                }
            return mock_res

        mock_get.side_effect = mock_jira_responses

        # Execute validation request
        files = {
            "file": ("test.xlsx", self.excel_data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        data = {
            "column_name": "TVPM_ID_COL",
            "soc_field": "fields.customfield_10100",
            "jira_base_url": "https://mockjira.example.com",
            "jira_pat": "mock_pat"
        }
        
        response = self.client.post("/api/validate", files=files, data=data)
        self.assertEqual(response.status_code, 200)
        
        # Load returned Excel stream
        res_excel = io.BytesIO(response.content)
        res_df = pd.read_excel(res_excel)
        
        # Assert column headers
        self.assertEqual(list(res_df.columns), ["S.No", "TVPM ID", "SoC Details", "Status"])
        
        # Assert mapped statuses
        # TVPM-1001 ('O') => Applicable
        self.assertEqual(res_df.iloc[0]["TVPM ID"], "TVPM-1001")
        self.assertEqual(res_df.iloc[0]["Status"], "Applicable")
        
        # TVPM-1002 ('X') => Not Applicable
        self.assertEqual(res_df.iloc[1]["TVPM ID"], "TVPM-1002")
        self.assertEqual(res_df.iloc[1]["Status"], "Not Applicable")
        
        # TVPM-1003 ('THE') => Applicable
        self.assertEqual(res_df.iloc[2]["TVPM ID"], "TVPM-1003")
        self.assertEqual(res_df.iloc[2]["Status"], "Applicable")
        
        # TVPM-1004 ('Unknown') => Not Applicable
        self.assertEqual(res_df.iloc[3]["TVPM ID"], "TVPM-1004")
        self.assertEqual(res_df.iloc[3]["Status"], "Not Applicable")

    @patch("httpx.AsyncClient.get")
    def test_validate_endpoint_with_table(self, mock_get):
        # Mock a JIRA response with a table in customfield_10200
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "key": "TVPM-1001",
            "fields": {
                "customfield_10200": (
                    "LG Gallery+ 앱을 지원하는 모델에만 해당됨\n"
                    "||OS||SOC||Year|| ||\n"
                    "|webOS26|as26l|Y-2026|X|\n"
                    "|webOS26|k24|Y-2026|O|\n"
                    "|webOS26|o26|Y-2026|THE|\n"
                )
            }
        }
        mock_get.return_value = mock_response

        # Execute validation request with soc_model as 'o26'
        files = {
            "file": ("test.xlsx", self.excel_data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        data = {
            "column_name": "TVPM_ID_COL",
            "soc_field": "fields.customfield_10200",
            "soc_model": "o26",
            "jira_base_url": "https://mockjira.example.com",
            "jira_pat": "mock_pat"
        }
        
        response = self.client.post("/api/validate", files=files, data=data)
        self.assertEqual(response.status_code, 200)
        
        res_excel = io.BytesIO(response.content)
        res_df = pd.read_excel(res_excel)
        
        # S.No 1 (TVPM-1001) should have extracted 'THE' from the row matching 'o26' -> status: Applicable
        self.assertEqual(res_df.iloc[0]["TVPM ID"], "TVPM-1001")
        self.assertEqual(res_df.iloc[0]["SoC Details"], "THE (Extracted for 'o26')")
        self.assertEqual(res_df.iloc[0]["Status"], "Applicable")
        
        # Test with an invalid/non-existent soc_model (should return Not Applicable)
        data["soc_model"] = "nonexistent"
        self.excel_data.seek(0)
        files = {
            "file": ("test.xlsx", self.excel_data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        response = self.client.post("/api/validate", files=files, data=data)
        res_df = pd.read_excel(io.BytesIO(response.content))
        self.assertEqual(res_df.iloc[0]["Status"], "Not Applicable")
        self.assertEqual(res_df.iloc[0]["SoC Details"], "Not Found (SOC Model 'nonexistent' not in table)")

        # Test validation auto-detection (when soc_field is omitted/empty)
        # We reuse the mock response setup from the previous test
        data["soc_field"] = ""
        data["soc_model"] = "k24"
        self.excel_data.seek(0)
        files = {
            "file": ("test.xlsx", self.excel_data.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        }
        response = self.client.post("/api/validate", files=files, data=data)
        self.assertEqual(response.status_code, 200)
        res_df = pd.read_excel(io.BytesIO(response.content))
        # TVPM-1001 should have automatically detected customfield_10200 as table, mapped k24 to 'O' -> Applicable
        self.assertEqual(res_df.iloc[0]["Status"], "Applicable")
        self.assertEqual(res_df.iloc[0]["SoC Details"], "O (Extracted for 'k24')")


if __name__ == "__main__":
    unittest.main()

