import sqlite3
import json
import csv
import logging
from typing import List, Optional
from rubygemdb.storage.base import StorageBase
from rubygemdb.models.gem import GemEntry, GemInventoryItem, GemClassification, GemRisks, GemSignals
from rubygemdb.core.config import settings
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.context7 import Context7Service

logger = logging.getLogger(__name__)

class SQLiteStorage(StorageBase):
    def __init__(self, rubygems_service: Optional[RubyGemsService] = None, context7_service: Optional[Context7Service] = None):
        self.db_path = settings.sqlite_db_file
        self.rubygems = rubygems_service or RubyGemsService()
        self.context7 = context7_service or Context7Service()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Inventory table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    name TEXT PRIMARY KEY,
                    version TEXT,
                    category TEXT,
                    description TEXT,
                    homepage TEXT,
                    source_code_uri TEXT,
                    context7_id TEXT,
                    verified INTEGER DEFAULT 0
                )
            """)
            
            # Classified gems table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS classified_gems (
                    name TEXT PRIMARY KEY,
                    classification TEXT,
                    role TEXT,
                    capabilities TEXT,
                    risks TEXT,
                    signals TEXT,
                    dependencies TEXT,
                    description TEXT,
                    FOREIGN KEY(name) REFERENCES inventory(name)
                )
            """)
            conn.commit()

    def load_inventory(self, path: str) -> List[GemInventoryItem]:
        items = []
        with open(path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for row in rows:
                name = row.get("gem") or row.get("name")
                if not name:
                    continue
                
                # Check if already verified in DB
                cursor.execute("SELECT verified, homepage, source_code_uri, context7_id FROM inventory WHERE name = ?", (name,))
                db_row = cursor.fetchone()
                
                if db_row and db_row[0] == 1 and db_row[2] is not None:
                    homepage = db_row[1]
                    source_code_uri = db_row[2]
                    context7_id = db_row[3]
                else:
                    logger.info(f"Verifying {name}...")
                    # Verify homepage and source_code_uri from RubyGems API
                    gem_info = self.rubygems.fetch_gem_info(name)
                    homepage = gem_info.get("homepage_uri") if gem_info else row.get("homepage")
                    source_code_uri = gem_info.get("source_code_uri") if gem_info else row.get("source_code_uri")
                    
                    # Verify context7_id
                    context7_id = self.context7.verify_library(name)
                    
                    # Insert or update
                    cursor.execute("""
                        INSERT INTO inventory (name, version, category, description, homepage, source_code_uri, context7_id, verified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(name) DO UPDATE SET
                            version=excluded.version,
                            category=excluded.category,
                            description=excluded.description,
                            homepage=excluded.homepage,
                            source_code_uri=excluded.source_code_uri,
                            context7_id=excluded.context7_id,
                            verified=1
                    """, (name, row.get("version"), row.get("category"), row.get("description"), homepage, source_code_uri, context7_id))
                
                items.append(GemInventoryItem(
                    name=name,
                    version=row.get("version"),
                    category=row.get("category"),
                    description=row.get("description"),
                    homepage=homepage,
                    source_code_uri=source_code_uri,
                    context7_id=context7_id
                ))
            conn.commit()
        return items

    def save_classified_gems(self, gems: List[GemEntry]):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for gem in gems:
                cursor.execute("""
                    INSERT INTO classified_gems (
                        name, classification, role, capabilities, risks, signals, dependencies, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        classification=excluded.classification,
                        role=excluded.role,
                        capabilities=excluded.capabilities,
                        risks=excluded.risks,
                        signals=excluded.signals,
                        dependencies=excluded.dependencies,
                        description=excluded.description
                """, (
                    gem.name,
                    json.dumps(gem.classification.model_dump()),
                    json.dumps(gem.role),
                    json.dumps(gem.capabilities),
                    json.dumps(gem.risks.model_dump()),
                    json.dumps(gem.signals.model_dump()),
                    json.dumps(gem.dependencies),
                    gem.description
                ))
            conn.commit()

    def update_gem_metadata(self, name: str, context7_id: Optional[str] = None, source_code_uri: Optional[str] = None):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if context7_id is not None and source_code_uri is not None:
                cursor.execute("UPDATE inventory SET context7_id = ?, source_code_uri = ? WHERE name = ?", (context7_id, source_code_uri, name))
            elif context7_id is not None:
                cursor.execute("UPDATE inventory SET context7_id = ? WHERE name = ?", (context7_id, name))
            elif source_code_uri is not None:
                cursor.execute("UPDATE inventory SET source_code_uri = ? WHERE name = ?", (source_code_uri, name))
            conn.commit()

    def delete_gem(self, name: str):
        """Remove a gem from both inventory and classified_gems tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM classified_gems WHERE name = ?", (name,))
            cursor.execute("DELETE FROM inventory WHERE name = ?", (name,))
            conn.commit()
            logger.info(f"Deleted gem {name} from storage")

    def wipe_classified_gems(self):
        """Delete every classified row (fresh start for reclassification).

        Inventory rows are preserved; only classification output is wiped.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM classified_gems")
            conn.commit()
            logger.info("Wiped classified_gems table")

    def update_gem_classification(self, name: str, primary_category: str):
        """Manually update the primary classification of a gem."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Fetch existing classification to preserve other fields
            cursor.execute("SELECT classification FROM classified_gems WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                cls_data = json.loads(row[0])
                cls_data["primary"] = primary_category
                # Reset confidence to 1.0 for manual override
                cls_data["confidence"] = 1.0
                cursor.execute("UPDATE classified_gems SET classification = ? WHERE name = ?", (json.dumps(cls_data), name))
                conn.commit()
                logger.info(f"Updated classification for {name} to {primary_category}")

    def add_gem_to_inventory(self, name: str):
        """Add a gem name to inventory for subsequent classification."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # If it already exists in inventory but not classified, we don't need to do much.
            # If it doesn't exist, insert it.
            cursor.execute("INSERT OR IGNORE INTO inventory (name, verified) VALUES (?, 0)", (name,))
            # Force verified=0 if we want it to be re-checked or processed if it was somehow stuck
            cursor.execute("UPDATE inventory SET verified = 0 WHERE name = ? AND name NOT IN (SELECT name FROM classified_gems)", (name,))
            conn.commit()
            logger.info(f"Added gem {name} to inventory")

    def load_classified_gems(self) -> List[GemEntry]:
        gems = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Join with inventory to get metadata
            cursor.execute("""
                SELECT c.*, i.homepage, i.source_code_uri, i.context7_id 
                FROM classified_gems c
                LEFT JOIN inventory i ON c.name = i.name
            """)
            rows = cursor.fetchall()
            for row in rows:
                gems.append(GemEntry(
                    name=row["name"],
                    classification=GemClassification(**json.loads(row["classification"])),
                    role=json.loads(row["role"]),
                    capabilities=json.loads(row["capabilities"]),
                    risks=GemRisks(**json.loads(row["risks"])),
                    signals=GemSignals(**json.loads(row["signals"])),
                    dependencies=json.loads(row["dependencies"]),
                    description=row["description"],
                    homepage=row["homepage"],
                    source_code_uri=row["source_code_uri"],
                    context7_id=row["context7_id"]
                ))
        return gems

    def get_all_inventory_gems(self) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name, homepage, source_code_uri, description, context7_id FROM inventory")
            return [dict(row) for row in cursor.fetchall()]

    def update_gem_verification(self, name: str, homepage: str, source_code_uri: str, description: str, context7_id: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE inventory 
                SET homepage = ?, source_code_uri = ?, description = ?, context7_id = ?, verified = 1 
                WHERE name = ?
            """, (homepage, source_code_uri, description, context7_id, name))
            
            cursor.execute("""
                UPDATE classified_gems
                SET description = ?
                WHERE name = ?
            """, (description, name))
            
            conn.commit()
