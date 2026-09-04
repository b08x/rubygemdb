import sys

# Patch config.py
with open("src/rubygemdb/core/config.py", "r") as f:
    config_content = f.read()

if "rubygemdb_agent_model" not in config_content:
    config_content = config_content.replace(
        'rubygemdb_model: str = "devstral-small"',
        'rubygemdb_model: str = "devstral-small"\n    rubygemdb_agent_model: str = "api://devstral-small"'
    )

with open("src/rubygemdb/core/config.py", "w") as f:
    f.write(config_content)

# Patch agent.py
with open("src/rubygemdb/agent.py", "r") as f:
    agent_content = f.read()

if "from rubygemdb.core.config import settings" not in agent_content:
    agent_content = agent_content.replace(
        "from rubygemdb.services.rubygems import RubyGemsService",
        "from rubygemdb.services.rubygems import RubyGemsService\nfrom rubygemdb.core.config import settings"
    )

agent_content = agent_content.replace(
    'llm="api://devstral", # Utilizing Devstral API / LiteLLM proxy per design',
    'llm=settings.rubygemdb_agent_model, # Driven by config'
)

with open("src/rubygemdb/agent.py", "w") as f:
    f.write(agent_content)

