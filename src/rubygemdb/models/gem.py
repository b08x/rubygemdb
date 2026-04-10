from pydantic import BaseModel, Field
from typing import Optional, List, Dict

class GemSignals(BaseModel):
    rails: bool = False
    external_io: bool = False
    native_ext: bool = False

class GemClassification(BaseModel):
    primary: str
    secondary: Optional[str] = None
    sub_categories: List[str] = Field(default_factory=list)
    confidence: float = 0.0

class GemRisks(BaseModel):
    invasiveness: int = 1
    coupling: int = 1
    abstraction_leak: str = "low"

class GemEntry(BaseModel):
    name: str
    classification: GemClassification
    role: Dict[str, str] = Field(default_factory=dict)
    capabilities: List[str] = Field(default_factory=list)
    risks: GemRisks = Field(default_factory=GemRisks)
    signals: GemSignals = Field(default_factory=GemSignals)
    dependencies: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    homepage: Optional[str] = None
    source_code_uri: Optional[str] = None
    context7_id: Optional[str] = None

class GemInventoryItem(BaseModel):
    name: str
    version: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    source_code_uri: Optional[str] = None
    context7_id: Optional[str] = None
