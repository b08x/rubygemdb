"""Automated tests for TxtaiAgent GraphRAG integration, chat, and transcript export."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubygemdb.agent import TxtaiAgent


@pytest.fixture
def mock_agent(tmp_path: Path):
    db_file = tmp_path / "test.sqlite"
    txtai_dir = tmp_path / "txtai"

    # Avoid heavy embedding building in tests
    with patch.object(TxtaiAgent, "_initialize_index"):
        agent = TxtaiAgent(db_path=str(db_file), txtai_dir=str(txtai_dir))
        return agent


def test_set_graphrag_context(mock_agent: TxtaiAgent):
    mock_service = MagicMock()
    mock_service.nodes_by_id = {"node1": {}, "node2": {}}
    mock_service.build_or_load_index.return_value = {
        "version": "1.0",
        "nodes": [{}, {}],
        "edges": [{}],
    }
    mock_agent.graphrag_service = mock_service

    status = mock_agent.set_graphrag_context(["Thor", "drydock"], intention="Build CLI")

    assert status["gems"] == ["Thor", "drydock"]
    assert status["intention"] == "Build CLI"
    assert status["nodes"] == 2
    assert status["edges"] == 1
    mock_service.build_or_load_index.assert_called_once_with(
        ["Thor", "drydock"], progress_callback=None
    )


def test_chat_with_graphrag(mock_agent: TxtaiAgent):
    mock_service = MagicMock()
    mock_service.nodes_by_id = {"node1": {}}
    mock_service.retrieve.return_value = {"primary_nodes": [], "expanded_nodes": [], "edges": []}
    mock_service.format_grounded_context.return_value = "### Grounded API\n- Thor.start"
    mock_agent.graphrag_service = mock_service
    mock_agent._graphrag_gems = ["thor"]
    mock_agent._graphrag_intention = "CLI pipeline"

    fake_model = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = "Use Thor.start(ARGV) to run the CLI."
    fake_model.return_value = fake_resp
    mock_agent.model = fake_model

    answer = mock_agent.chat("How do I start a CLI?")

    assert "Thor.start(ARGV)" in answer
    mock_service.retrieve.assert_called_once_with("How do I start a CLI?", top_k=5)
    mock_service.format_grounded_context.assert_called_once()
    assert len(mock_agent._chat_history) == 2
    assert mock_agent._chat_history[0]["role"] == "user"
    assert mock_agent._chat_history[1]["role"] == "agent"


def test_chat_without_graphrag_falls_back_to_run(mock_agent: TxtaiAgent):
    mock_agent._graphrag_gems = []
    with patch.object(mock_agent, "run", return_value="General fallback answer") as mock_run:
        ans = mock_agent.chat("General question?")
        assert ans == "General fallback answer"
        mock_run.assert_called_once_with("General question?")


def test_export_transcript(mock_agent: TxtaiAgent, tmp_path: Path):
    mock_service = MagicMock()
    mock_service.nodes_by_id = {"n1": {}, "n2": {}}
    mock_service.graph_data = {"edges": [{}]}
    mock_agent.graphrag_service = mock_service

    mock_agent._graphrag_gems = ["thor", "drydock"]
    mock_agent._graphrag_intention = "Build awesome CLI"
    mock_agent._chat_history = [
        {"role": "user", "content": "How do I invoke CLI?"},
        {"role": "agent", "content": "Use Thor.start."},
    ]

    out_dir = tmp_path / "exports"
    md_file, json_file = mock_agent.export_transcript(out_dir=out_dir)

    assert md_file.exists()
    assert json_file.exists()

    md_text = md_file.read_text(encoding="utf-8")
    assert "# RubyGemDB Contextual GraphRAG Transcript" in md_text
    assert "Build awesome CLI" in md_text
    assert "thor, drydock" in md_text
    assert "How do I invoke CLI?" in md_text
    assert "Use Thor.start." in md_text

    json_data = json.loads(json_file.read_text(encoding="utf-8"))
    assert json_data["project_intention"] == "Build awesome CLI"
    assert json_data["indexed_gems"] == ["thor", "drydock"]
    assert len(json_data["messages"]) == 2
    assert json_data["index_summary"]["nodes_count"] == 2


def test_clear_chat(mock_agent: TxtaiAgent):
    mock_agent._chat_history = [{"role": "user", "content": "Hello"}]
    mock_agent.clear_chat()
    assert mock_agent._chat_history == []
