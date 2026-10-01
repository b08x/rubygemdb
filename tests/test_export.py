from unittest.mock import Mock
from rubygemdb.models.gem import GemEntry, GemClassification, GemRisks, GemSignals

# Create sample gem for export tests
def create_test_gem(name, source="https://example.com", desc="Test gem", c7_id=None):
    return GemEntry(
        name=name,
        classification=GemClassification(primary="cli_libraries"),
        description=desc,
        source_code_uri=source,
        context7_id=c7_id,
        dependencies=["rails"],
        role={"description": "Test"},
        capabilities=[],
        risks=GemRisks(),
        signals=GemSignals()
    )


def test_export_gemfile_format():
    # We need to import after setting up test data
    from rubygemdb.ui.tui import GemApp
    
    # Create a mock app
    app = Mock(spec=GemApp)
    app.all_gems = [
        create_test_gem("rails"),
        create_test_gem("sidekiq"),
        create_test_gem("pg")
    ]
    
    # Call the export method directly
    result = GemApp._export_gemfile(app, ["rails", "sidekiq"])
    
    # Verify format
    assert "source 'https://rubygems.org'" in result
    assert "gem 'rails'" in result
    assert "gem 'sidekiq'" in result
    assert "gem 'pg'" not in result  # Not in selected list


def test_export_csv_format():
    from rubygemdb.ui.tui import GemApp
    
    app = Mock(spec=GemApp)
    app.all_gems = [
        create_test_gem("rails", "https://github.com/rails/rails", "Web framework"),
        create_test_gem("pg", "https://github.com/ged/ruby-pg", 'PostgreSQL "libpq" client'),
    ]
    
    result = GemApp._export_csv(app, ["rails", "pg"])
    
    # Verify CSV format
    lines = result.split("\n")
    assert lines[0] == "Name,Category,Source URI,Context7 ID,Description"
    assert '"rails","cli_libraries","https://github.com/rails/rails","","Web framework"' in result
    assert '"pg","cli_libraries","https://github.com/ged/ruby-pg","","PostgreSQL ""libpq"" client"' in result


def test_export_json_format():
    from rubygemdb.ui.tui import GemApp
    import json
    
    app = Mock(spec=GemApp)
    app.all_gems = [
        create_test_gem("rails", c7_id="/rails/rails")
    ]
    
    result = GemApp._export_json(app, ["rails"])
    
    # Verify JSON is valid and contains expected data
    data = json.loads(result)
    assert len(data) == 1
    assert data[0]["name"] == "rails"
    assert data[0]["context7_id"] == "/rails/rails"
    assert data[0]["classification"]["primary"] == "cli_libraries"


def test_export_markdown_format():
    from rubygemdb.ui.tui import GemApp
    
    app = Mock(spec=GemApp)
    app.all_gems = [
        create_test_gem("rails", "https://github.com/rails/rails", "Ruby on Rails"),
        create_test_gem("pg", "https://github.com/ged/ruby-pg", "PostgreSQL client"),
    ]
    
    result = GemApp._export_markdown(app, ["rails", "pg"])
    
    # Verify markdown format
    lines = result.split("\n")
    assert lines[0].startswith("| Name |")
    assert lines[1].startswith("|---")
    assert "| rails | cli_libraries | https://github.com/rails/rails" in result
    assert "| pg | cli_libraries | https://github.com/ged/ruby-pg" in result
