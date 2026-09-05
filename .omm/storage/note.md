The SQLite database stores complex fields (classification, role, risks, signals, dependencies) as JSON strings, which are deserialized back to Pydantic models on load.
