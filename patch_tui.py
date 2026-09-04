import sys

def patch():
    with open("src/rubygemdb/ui/tui.py", "r") as f:
        content = f.read()

    # 1. Add import for TxtaiAgent
    if "from rubygemdb.agent import TxtaiAgent" not in content:
        content = content.replace(
            "from rubygemdb.models.gem import GemEntry",
            "from rubygemdb.models.gem import GemEntry\nfrom rubygemdb.agent import TxtaiAgent"
        )
    
    # 2. Add ChatScreen class before GemApp
    chat_screen_code = """
class AgentChatScreen(ModalScreen):
    \"\"\"A chat screen for interacting with the TxtaiAgent.\"\"\"
    
    def __init__(self, agent_loader_callback):
        super().__init__()
        self.agent_loader_callback = agent_loader_callback
        self.agent = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chat-dialog", classes="dialog"):
            yield Label("RubyGemDB Agent (Txtai/Ollama)", id="chat-title")
            yield RichLog(id="chat-log", markup=True, wrap=True)
            yield Input(placeholder="Ask the agent a question...", id="chat-input")
            yield Label("Press ESC to close.", id="chat-hint")

    def on_mount(self) -> None:
        self.log_widget = self.query_one("#chat-log", RichLog)
        self.log_widget.write("[bold green]Agent:[/bold green] System ready. Loading embeddings index...")
        self.load_agent()
        
    @work(thread=True)
    def load_agent(self):
        try:
            self.agent = self.agent_loader_callback()
            self.call_from_thread(self.log_widget.write, "[bold green]Agent:[/bold green] Ready! How can I help you today?")
        except Exception as e:
            self.call_from_thread(self.log_widget.write, f"[bold red]System Error:[/bold red] Failed to load agent: {e}")

    @on(Input.Submitted, "#chat-input")
    def submit_query(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
            
        event.input.value = ""
        self.log_widget.write(f"\\n[bold blue]You:[/bold blue] {query}")
        
        if not self.agent:
            self.log_widget.write("[bold red]Agent:[/bold red] Still loading... please wait.")
            return
            
        self.process_query(query)

    @work(thread=True)
    def process_query(self, query: str) -> None:
        self.call_from_thread(self.log_widget.write, "[dim]Agent is thinking...[/dim]")
        try:
            response = self.agent.run(query)
            self.call_from_thread(self.log_widget.write, f"\\n[bold green]Agent:[/bold green] {response}")
        except Exception as e:
            self.call_from_thread(self.log_widget.write, f"\\n[bold red]Agent Error:[/bold red] {e}")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.app.pop_screen()
"""
    if "class AgentChatScreen" not in content:
        content = content.replace("class GemApp(App):", chat_screen_code + "\nclass GemApp(App):")

    # 3. Add to BINDINGS
    if '("c", "open_chat", "Agent Chat")' not in content:
        content = content.replace(
            '("r", "refresh", "Refresh"),',
            '("r", "refresh", "Refresh"),\n        ("c", "open_chat", "Agent Chat"),'
        )

    # 4. Add CSS for chat-dialog
    if "chat-dialog" not in content:
        content = content.replace(
            "height: auto;",
            "height: auto;\n}\n\n#chat-dialog {\n    width: 80%;\n    height: 80%;\n    border: solid green;\n    background: $surface;\n    padding: 1 2;\n}\n\n#chat-log {\n    height: 1fr;\n    border: solid #333;\n    margin: 1 0;\n}\n\n#chat-title {\n    text-align: center;\n    text-style: bold;\n}"
        )

    # 5. Add lazy loader and action to GemApp
    action_code = """
    def get_txtai_agent(self):
        if not hasattr(self, '_txtai_agent') or self._txtai_agent is None:
            self._txtai_agent = TxtaiAgent()
        return self._txtai_agent

    def action_open_chat(self):
        self.push_screen(AgentChatScreen(self.get_txtai_agent))
"""
    if "def action_open_chat(self):" not in content:
        content = content.replace(
            "def action_refresh(self):",
            action_code + "\n    def action_refresh(self):"
        )
        
    with open("src/rubygemdb/ui/tui.py", "w") as f:
        f.write(content)
        
patch()
