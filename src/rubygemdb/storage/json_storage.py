import json
import csv
from typing import List
from rubygemdb.storage.base import StorageBase
from rubygemdb.models.gem import GemEntry, GemInventoryItem
from rubygemdb.core.config import settings

class JSONStorage(StorageBase):
    def load_inventory(self, path: str) -> List[GemInventoryItem]:
        items = []
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("gem") or row.get("name")
                if name:
                    items.append(GemInventoryItem(
                        name=name,
                        version=row.get("version"),
                        category=row.get("category"),
                        description=row.get("description"),
                        homepage=row.get("homepage"),
                        context7_id=row.get("context7_id")
                    ))
        return items

    def save_classified_gems(self, gems: List[GemEntry]):
        data = [gem.model_dump() for gem in gems]
        with open(settings.classified_gems_file, "w") as f:
            json.dump(data, f, indent=2)

    def load_classified_gems(self) -> List[GemEntry]:
        if not settings.classified_gems_file.exists():
            return []
        with open(settings.classified_gems_file, "r") as f:
            data = json.load(f)
            return [GemEntry(**item) for item in data]
