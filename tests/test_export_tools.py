"""
Tests for export_tools module - SARIF export and findings management
"""

import json
import pytest
from unittest.mock import Mock, patch

from src.tools.export_tools import (
    FindingsParser,
    SARIFBuilder,
    CWE_MAP,
    SEVERITY_ORDER,
    CONFIDENCE_ORDER,
)


class TestFindingsParser:
    """Test the FindingsParser class"""

    def test_parse_taint_flow_text_command_injection(self):
        """Test parsing command injection taint flow"""
        text = """
--- Flow 1 ---
Source: getenv("CMD")
  Location: main.c:10
  Node ID: 123

Sink: system(cmd)
  Location: main.c:42
  Node ID: 456

Found 1 taint flow(s):

--- Flow 1 ---
Source: getenv("CMD")
  Location: main.c:10 in main()

Path (2 intermediate steps):
  [L15] cmd = s
           in main() at main.c
  [L20] system(cmd)
           in main() at main.c

Sink: system(cmd)
  Location: main.c:42 in main()

Path length: 4 nodes
"""
        findings = FindingsParser.parse_taint_flow_text(text, "test_hash")

        assert len(findings) > 0
        finding = findings[0]
        assert finding["finding_type"] == "taint_flow"
        assert finding["confidence"] == "high"
        assert finding["codebase_hash"] == "test_hash"
        assert "command_injection" in finding["rule_id"]

    def test_parse_sql_injection_flow(self):
        """Test parsing SQL injection taint flow"""
        text = """
--- Flow 1 ---
Source: request.get("id")
  Location: app.py:20
  Node ID: 100

Sink: executeQuery(query)
  Location: app.py:50
  Node ID: 200

Found 1 taint flow(s):

--- Flow 1 ---
Source: request.get("id")
  Location: app.py:20 in handle_request()

Path (1 intermediate step):
  [L30] query = f"SELECT * FROM users WHERE id={id}"
           in handle_request() at app.py

Sink: executeQuery(query)
  Location: app.py:50 in handle_request()

Path length: 3 nodes
"""
        findings = FindingsParser.parse_taint_flow_text(text, "test_hash")

        assert len(findings) > 0
        finding = findings[0]
        assert finding["severity"] == "critical"
        assert finding["rule_id"] == "sql_injection"
        assert finding["cwe_id"] == CWE_MAP["sql_injection"]

    def test_parse_uaf_text(self):
        """Test parsing use-after-free findings"""
        text = """
Found 1 potential UAF issue(s):

--- Issue 1 ---
Free Site: free(ptr)
  Location: main.c:42 in process_data()
  Freed Pointer: ptr

Post-Free Usage(s):
  [main.c:50] print_data(ptr)
"""
        findings = FindingsParser.parse_uaf_text(text, "test_hash")

        assert len(findings) > 0
        finding = findings[0]
        assert finding["finding_type"] == "use_after_free"
        assert finding["severity"] == "high"
        assert finding["confidence"] == "high"
        assert finding["cwe_id"] == CWE_MAP["use_after_free"]

    def test_parse_double_free_text(self):
        """Test parsing double-free findings"""
        text = """
Found 2 potential Double-Free issue(s):

--- Issue 1 ---
Pointer: ptr
Location: main.c in process_data()

First Free:  [main.c:42] free(ptr)
Second Free: [main.c:55] free(ptr)

--- Issue 2 ---
Pointer: buf
Location: utils.c in cleanup()

First Free:  [utils.c:30] free(buf)
Second Free: [utils.c:45] free(alias_buf)
"""
        findings = FindingsParser.parse_double_free_text(text, "test_hash")

        assert len(findings) >= 1
        finding = findings[0]
        assert finding["finding_type"] == "double_free"
        assert finding["severity"] == "high"
        assert finding["confidence"] == "high"
        assert finding["cwe_id"] == CWE_MAP["double_free"]

    def test_determine_severity_from_flow_xss(self):
        """Test severity determination for XSS flows"""
        flow_text = "Sink: innerHTML(data)"
        severity, vuln_type = FindingsParser._determine_severity_from_flow(flow_text, "test.js")

        assert severity == "medium"
        assert vuln_type == "xss"


class TestSARIFBuilder:
    """Test the SARIFBuilder class"""

    def test_build_sarif_basic(self):
        """Test building basic SARIF document"""
        findings = [
            {
                "codebase_hash": "test",
                "finding_type": "taint_flow",
                "severity": "high",
                "confidence": "high",
                "filename": "main.c",
                "line_number": 42,
                "message": "Command injection vulnerability",
                "description": "User input flows to system call",
                "cwe_id": 78,
                "rule_id": "command_injection",
                "flow_data": {
                    "source": {"file": "main.c", "line": 10},
                    "sink": {"file": "main.c", "line": 42},
                },
            }
        ]

        sarif = SARIFBuilder.build_sarif(findings)

        # Verify SARIF structure
        assert sarif["version"] == "2.1.0"
        assert "$schema" in sarif
        assert "runs" in sarif
        assert len(sarif["runs"]) == 1

        # Verify run structure
        run = sarif["runs"][0]
        assert "tool" in run
        assert "driver" in run["tool"]
        assert run["tool"]["driver"]["name"] == "CodeBadger"

        # Verify results
        assert "results" in run
        assert len(run["results"]) == 1

        result = run["results"][0]
        assert result["ruleId"] == "command_injection"
        assert result["level"] == "error"  # high severity maps to error

    def test_sarif_rules_generation(self):
        """Test that SARIF rules are correctly generated"""
        findings = [
            {
                "codebase_hash": "test",
                "finding_type": "taint_flow",
                "severity": "critical",
                "confidence": "high",
                "filename": "test.c",
                "line_number": 10,
                "message": "SQL Injection",
                "description": "User input in SQL query",
                "cwe_id": 89,
                "rule_id": "sql_injection",
            },
            {
                "codebase_hash": "test",
                "finding_type": "use_after_free",
                "severity": "high",
                "confidence": "high",
                "filename": "test.c",
                "line_number": 20,
                "message": "Use-after-free",
                "description": "Memory accessed after free",
                "cwe_id": 416,
                "rule_id": "use_after_free",
            },
        ]

        sarif = SARIFBuilder.build_sarif(findings)
        rules = sarif["runs"][0]["tool"]["driver"]["rules"]

        # Should have 2 rules
        assert len(rules) == 2

        # Check rule IDs
        rule_ids = {rule["id"] for rule in rules}
        assert "sql_injection" in rule_ids
        assert "use_after_free" in rule_ids

    def test_severity_to_level_mapping(self):
        """Test severity to SARIF level mapping"""
        assert SARIFBuilder._map_severity_to_level("critical") == "error"
        assert SARIFBuilder._map_severity_to_level("high") == "error"
        assert SARIFBuilder._map_severity_to_level("medium") == "warning"
        assert SARIFBuilder._map_severity_to_level("low") == "note"
        assert SARIFBuilder._map_severity_to_level("unknown") == "warning"

    def test_severity_to_score_mapping(self):
        """Test severity to CVSS score mapping"""
        assert SARIFBuilder._map_severity_to_score("critical") == "9.9"
        assert SARIFBuilder._map_severity_to_score("high") == "7.5"
        assert SARIFBuilder._map_severity_to_score("medium") == "5.0"
        assert SARIFBuilder._map_severity_to_score("low") == "2.5"

    def test_build_flow_locations(self):
        """Test building flow locations for SARIF"""
        flow_data = {
            "source": {"file": "main.c", "line": 10},
            "sink": {"file": "main.c", "line": 42},
            "steps": ["Variable assignment", "Function call"],
        }

        locations = SARIFBuilder._build_flow_locations(flow_data)

        # Should have source + steps + sink
        assert len(locations) >= 2

        # Check first location is source
        assert "acquire" in locations[0]["kinds"]

        # Check last location is sink
        assert "release" in locations[-1]["kinds"]

    def test_remediation_text_available(self):
        """Test that remediation text is available for rules"""
        for rule_id in CWE_MAP.keys():
            text = SARIFBuilder._get_remediation_text(rule_id)
            assert len(text) > 0
            assert isinstance(text, str)


class TestIntegration:
    """Integration tests for export tools"""

    def test_end_to_end_flow(self):
        """Test end-to-end: parse -> build SARIF"""
        # Sample taint flow text
        flow_text = """
--- Flow 1 ---
Source: getenv("PATH")
  Location: shell.c:20
  Node ID: 100

Sink: system(cmd)
  Location: shell.c:50
  Node ID: 200

Found 1 taint flow(s):

--- Flow 1 ---
Source: getenv("PATH")
  Location: shell.c:20 in main()

Path (1 intermediate step):
  [L30] system(cmd)
           in main() at shell.c

Sink: system(cmd)
  Location: shell.c:50 in main()

Path length: 3 nodes
"""

        # Parse findings
        findings = FindingsParser.parse_taint_flow_text(flow_text, "test_hash")
        assert len(findings) > 0

        # Build SARIF
        sarif = SARIFBuilder.build_sarif(findings)

        # Verify SARIF is valid
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) > 0

        # Verify result contains finding
        result = sarif["runs"][0]["results"][0]
        assert "message" in result
        assert "locations" in result


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_parse_empty_text(self):
        """Test parsing empty text"""
        findings = FindingsParser.parse_taint_flow_text("", "test_hash")
        assert len(findings) == 0

    def test_parse_malformed_text(self):
        """Test parsing malformed text"""
        text = "This is not valid flow output"
        findings = FindingsParser.parse_taint_flow_text(text, "test_hash")
        assert len(findings) == 0

    def test_build_sarif_empty_findings(self):
        """Test building SARIF with empty findings"""
        sarif = SARIFBuilder.build_sarif([])

        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"][0]["results"]) == 0

    def test_multiple_findings_same_type(self):
        """Test handling multiple findings of same type"""
        findings = [
            {
                "codebase_hash": "test",
                "finding_type": "taint_flow",
                "severity": "high",
                "confidence": "high",
                "filename": f"file{i}.c",
                "line_number": 10 + i,
                "message": f"Injection {i}",
                "description": "Test finding",
                "cwe_id": 78,
                "rule_id": "command_injection",
            }
            for i in range(5)
        ]

        sarif = SARIFBuilder.build_sarif(findings)

        # Should have 5 results
        assert len(sarif["runs"][0]["results"]) == 5

        # But only 1 rule (same CWE)
        assert len(sarif["runs"][0]["tool"]["driver"]["rules"]) == 1
