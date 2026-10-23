"""Automated tests for FastMCP GraphRAG tools and prompts."""

from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.mcp_server import mcp


@pytest.mark.asyncio
async def test_build_gem_graphrag_tool():
    mock_service = MagicMock()
    mock_service.active_gems = ["thor"]
    mock_service.active_hash = "abcdef1234567890"
    mock_service.nodes_by_id = {"node1": {}, "node2": {}}
    mock_service.build_or_load_index.return_value = {"edges": [{}, {}]}

    with patch("rubygemdb.mcp_server._get_graphrag_service", return_value=mock_service):
        result = await mcp.call_tool(
            "build_gem_graphrag",
            {"gems": ["thor"], "intention": "CLI tool"},
        )
        data = result[1]["result"]
        assert data["gems"] == ["thor"]
        assert data["hash"] == "abcdef1234567890"
        assert data["nodes_count"] == 2
        assert data["edges_count"] == 2
        assert data["status"] == "ready"
        mock_service.build_or_load_index.assert_called_once_with(["thor"])


@pytest.mark.asyncio
async def test_query_gem_graphrag_tool():
    mock_service = MagicMock()
    mock_service.active_gems = ["thor"]
    mock_service.retrieve.return_value = {
        "primary_nodes": [{"id": "method:Thor#start"}],
        "expanded_nodes": [{"id": "doc_tag:Thor#start:param"}],
        "edges": [{"source": "method:Thor#start", "target": "doc_tag:Thor#start:param"}],
    }
    mock_service.format_grounded_context.return_value = "### Grounded API\n- def start()"

    with patch("rubygemdb.mcp_server._get_graphrag_service", return_value=mock_service):
        result = await mcp.call_tool(
            "query_gem_graphrag",
            {
                "gems": ["thor"],
                "query": "How to invoke CLI?",
                "intention": "CLI app",
            },
        )
        data = result[1]["result"]
        assert data["gems"] == ["thor"]
        assert data["query"] == "How to invoke CLI?"
        assert data["intention"] == "CLI app"
        assert len(data["primary_nodes"]) == 1
        assert len(data["expanded_nodes"]) == 1
        assert "def start()" in data["grounded_context"]
        mock_service.build_or_load_index.assert_called_once_with(["thor"])
        mock_service.retrieve.assert_called_once_with("How to invoke CLI?", top_k=5)


@pytest.mark.asyncio
async def test_gem_architect_rag_prompt():
    prompt = await mcp.get_prompt(
        "gem-architect-rag",
        {
            "gems": ["thor", "drydock"],
            "intention": "Build asynchronous CLI tool",
            "task": "Design command parsing layer",
        },
    )
    # FastMCP prompt results contain messages
    content = prompt.messages[0].content.text
    assert "Principal Ruby Systems Architect" in content
    assert "`thor`, `drydock`" in content
    assert "Build asynchronous CLI tool" in content
    assert "Design command parsing layer" in content
    assert "query_gem_graphrag" in content
