from abc import ABC, abstractmethod
from typing import List, Optional
from rubygemdb.models.gem import GemEntry, GemInventoryItem

class StorageBase(ABC):
    @abstractmethod
    def load_inventory(self, path: str) -> List[GemInventoryItem]:
        pass

    @abstractmethod
    def save_classified_gems(self, gems: List[GemEntry]):
        pass

    @abstractmethod
    def load_classified_gems(self) -> List[GemEntry]:
        pass
