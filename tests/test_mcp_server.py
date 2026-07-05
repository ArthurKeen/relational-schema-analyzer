from __future__ import annotations

import pytest

from relational_schema_analyzer import mcp_server as srv


class TestBearerToken:
    def test_no_token_configured_allows(self):
        assert srv._bearer_token_valid(None, "") is True

    def test_valid_token(self):
        assert srv._bearer_token_valid("Bearer s3cret", "s3cret") is True

    def test_invalid_token(self):
        assert srv._bearer_token_valid("Bearer nope", "s3cret") is False

    def test_missing_header_when_token_required(self):
        assert srv._bearer_token_valid(None, "s3cret") is False

    def test_malformed_header(self):
        assert srv._bearer_token_valid("Token s3cret", "s3cret") is False


class TestTypedRequest:
    def test_drops_none_fields(self):
        req = srv._typed_request("analyze", source={"type": "csv"}, input=None, owl=None)
        assert req == {"contractVersion": "1", "operation": "analyze",
                       "source": {"type": "csv"}}


class TestArgs:
    def test_defaults_to_stdio(self):
        args = srv._parse_args([])
        assert args.transport == "stdio"
        assert args.host == srv.DEFAULT_MCP_HOST
        assert args.port == srv.DEFAULT_MCP_PORT

    def test_remote_transport(self):
        args = srv._parse_args(["--transport", "sse", "--port", "9001"])
        assert args.transport == "sse"
        assert args.port == 9001


class TestBuildApp:
    def test_build_app_registers_tools(self):
        pytest.importorskip("mcp")
        app = srv.build_app()
        assert app is not None
        assert hasattr(app, "run")
