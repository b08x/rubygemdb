import sys

with open("src/rubygemdb/agent.py", "r") as f:
    content = f.read()

# Add rebuild capability
content = content.replace("def _initialize_index(self):", "def _initialize_index(self, force_rebuild: bool = False):")
content = content.replace(
    "if os.path.exists(self.index_path):",
    "if os.path.exists(self.index_path) and not force_rebuild:"
)

# And add build_index method to expose it cleanly
build_code = """
    def build_index(self):
        \"\"\"Forces a rebuild of the txtai index.\"\"\"
        self._initialize_index(force_rebuild=True)

"""

if "def build_index(self):" not in content:
    content = content.replace("def run(self, query: str) -> str:", build_code + "    def run(self, query: str) -> str:")

with open("src/rubygemdb/agent.py", "w") as f:
    f.write(content)
