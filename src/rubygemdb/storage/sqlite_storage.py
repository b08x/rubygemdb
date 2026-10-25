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
    def __init__(
        self,
        rubygems_service: Optional[RubyGemsService] = None,
        context7_service: Optional[Context7Service] = None,
        read_only: bool = False,
        db_path: Optional[str] = None,
    ):
        self.db_path = str(db_path) if db_path else str(settings.sqlite_db_file)
        self.read_only = read_only
        self.rubygems = rubygems_service or RubyGemsService()
        self.context7 = context7_service or Context7Service()
        if not self.read_only:
            self._init_db()

    @property
    def is_read_only(self) -> bool:
        return bool(getattr(self, "read_only", False))

    def _get_connection(self) -> sqlite3.Connection:
        if self.is_read_only:
            return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        return sqlite3.connect(self.db_path, uri=True)

    def _init_db(self):
        if self.is_read_only:
            return
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
                    verified INTEGER DEFAULT 0,
                    genai_uses TEXT
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
                    genai_uses TEXT,
                    FOREIGN KEY(name) REFERENCES inventory(name)
                )
            """)

            # Preferred flag lives on inventory; migrate older databases.
            columns = {row[1] for row in cursor.execute("PRAGMA table_info(inventory)")}
            if "preferred" not in columns:
                cursor.execute("ALTER TABLE inventory ADD COLUMN preferred INTEGER DEFAULT 0")
            if "genai_uses" not in columns:
                cursor.execute("ALTER TABLE inventory ADD COLUMN genai_uses TEXT")

            classified_columns = {row[1] for row in cursor.execute("PRAGMA table_info(classified_gems)")}
            if "sub_categories" not in classified_columns:
                cursor.execute("ALTER TABLE classified_gems ADD COLUMN sub_categories TEXT DEFAULT '[]'")
            if "genai_uses" not in classified_columns:
                cursor.execute("ALTER TABLE classified_gems ADD COLUMN genai_uses TEXT")

            conn.commit()

    def load_inventory(self, path: str) -> List[GemInventoryItem]:
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
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
                cursor.execute("SELECT verified, homepage, source_code_uri, context7_id, genai_uses FROM inventory WHERE name = ?", (name,))
                db_row = cursor.fetchone()
                
                if db_row and db_row[0] == 1 and db_row[2] is not None:
                    homepage = db_row[1]
                    source_code_uri = db_row[2]
                    context7_id = db_row[3]
                    genai_uses = db_row[4] or row.get("genai_uses")
                else:
                    logger.info(f"Verifying {name}...")
                    # Verify homepage and source_code_uri from RubyGems API
                    gem_info = self.rubygems.fetch_gem_info(name)
                    homepage = gem_info.get("homepage_uri") if gem_info else row.get("homepage")
                    source_code_uri = gem_info.get("source_code_uri") if gem_info else row.get("source_code_uri")
                    
                    # Verify context7_id
                    context7_id = self.context7.verify_library(name, source_code_uri=source_code_uri, homepage_uri=homepage)
                    genai_uses = row.get("genai_uses")
                    
                    # Insert or update
                    cursor.execute("""
                        INSERT INTO inventory (name, version, category, description, homepage, source_code_uri, context7_id, genai_uses, verified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(name) DO UPDATE SET
                            version=excluded.version,
                            category=excluded.category,
                            description=excluded.description,
                            homepage=excluded.homepage,
                            source_code_uri=excluded.source_code_uri,
                            context7_id=excluded.context7_id,
                            genai_uses=COALESCE(excluded.genai_uses, inventory.genai_uses),
                            verified=1
                    """, (name, row.get("version"), row.get("category"), row.get("description"), homepage, source_code_uri, context7_id, genai_uses))
                
                items.append(GemInventoryItem(
                    name=name,
                    version=row.get("version"),
                    category=row.get("category"),
                    description=row.get("description"),
                    homepage=homepage,
                    source_code_uri=source_code_uri,
                    context7_id=context7_id,
                    genai_uses=genai_uses
                ))
            conn.commit()
        return items

    def save_classified_gems(self, gems: List[GemEntry]):
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for gem in gems:
                cursor.execute("""
                    INSERT INTO classified_gems (
                        name, classification, role, capabilities, risks, signals, dependencies, description, sub_categories, genai_uses
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        classification=excluded.classification,
                        role=excluded.role,
                        capabilities=excluded.capabilities,
                        risks=excluded.risks,
                        signals=excluded.signals,
                        dependencies=excluded.dependencies,
                        description=excluded.description,
                        sub_categories=excluded.sub_categories,
                        genai_uses=COALESCE(excluded.genai_uses, classified_gems.genai_uses)
                """, (
                    gem.name,
                    json.dumps(gem.classification.model_dump()),
                    json.dumps(gem.role),
                    json.dumps(gem.capabilities),
                    json.dumps(gem.risks.model_dump()),
                    json.dumps(gem.signals.model_dump()),
                    json.dumps(gem.dependencies),
                    gem.description,
                    json.dumps(gem.classification.sub_categories),
                    gem.genai_uses,
                ))
            conn.commit()

    def update_gem_metadata(self, name: str, context7_id: Optional[str] = None, source_code_uri: Optional[str] = None):
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
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
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM classified_gems WHERE name = ?", (name,))
            cursor.execute("DELETE FROM inventory WHERE name = ?", (name,))
            conn.commit()
            logger.info(f"Deleted gem {name} from storage")

    def set_preferred(self, names: List[str], preferred: bool):
        """Mark (or unmark) gems as preferred picks."""
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for name in names:
                cursor.execute("UPDATE inventory SET preferred = ? WHERE name = ?", (1 if preferred else 0, name))
            conn.commit()

    def get_preferred(self) -> List[dict]:
        """All preferred gems: name, description, category, layer."""
        from rubygemdb.models.categories import CATEGORY_BY_SLUG
        from rubygemdb.services.stack import guess_category

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT i.name, i.description, c.classification
                FROM inventory i
                LEFT JOIN classified_gems c ON i.name = c.name
                WHERE i.preferred = 1
                ORDER BY i.name
                """
            ).fetchall()
        gems = []
        for row in rows:
            category = None
            if row["classification"]:
                try:
                    category = (json.loads(row["classification"]) or {}).get("primary")
                except (ValueError, TypeError):
                    category = None
            if category not in CATEGORY_BY_SLUG:
                category = guess_category(row["name"])
            gems.append({
                "name": row["name"],
                "description": row["description"] or "",
                "category": category,
                "layer": CATEGORY_BY_SLUG[category].layer,
            })
        return gems

    def wipe_classified_gems(self):
        """Delete every classified row (fresh start for reclassification).

        Inventory rows are preserved; only classification output is wiped.
        """
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM classified_gems")
            conn.commit()
            logger.info("Wiped classified_gems table")

    def update_gem_classification(self, name: str, primary_category: str):
        """Manually update the primary classification of a gem."""
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
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
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
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
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Join with inventory to get metadata
            cls_cols = {r[1] for r in cursor.execute("PRAGMA table_info(classified_gems)")}
            inv_cols = {r[1] for r in cursor.execute("PRAGMA table_info(inventory)")}
            if "genai_uses" in cls_cols and "genai_uses" in inv_cols:
                genai_select = "COALESCE(c.genai_uses, i.genai_uses) AS resolved_genai_uses"
            elif "genai_uses" in cls_cols:
                genai_select = "c.genai_uses AS resolved_genai_uses"
            elif "genai_uses" in inv_cols:
                genai_select = "i.genai_uses AS resolved_genai_uses"
            else:
                genai_select = "NULL AS resolved_genai_uses"

            cursor.execute(f"""
                SELECT c.*, i.homepage, i.source_code_uri, i.context7_id, i.preferred,
                       {genai_select}
                FROM classified_gems c
                LEFT JOIN inventory i ON c.name = i.name
            """)
            rows = cursor.fetchall()
            for row in rows:
                cls_data = json.loads(row["classification"])
                if not cls_data.get("sub_categories") and "sub_categories" in row.keys() and row["sub_categories"]:
                    try:
                        cls_data["sub_categories"] = json.loads(row["sub_categories"])
                    except Exception:
                        pass
                gems.append(GemEntry(
                    name=row["name"],
                    classification=GemClassification(**cls_data),
                    role=json.loads(row["role"]),
                    capabilities=json.loads(row["capabilities"]),
                    risks=GemRisks(**json.loads(row["risks"])),
                    signals=GemSignals(**json.loads(row["signals"])),
                    dependencies=json.loads(row["dependencies"]),
                    description=row["description"],
                    homepage=row["homepage"],
                    source_code_uri=row["source_code_uri"],
                    context7_id=row["context7_id"],
                    preferred=bool(row["preferred"]),
                    genai_uses=row["resolved_genai_uses"],
                ))
        return gems

    def get_classified_gem(self, name: str) -> Optional[GemEntry]:
        """Fetch a single classified gem by name, or None if not found."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cls_cols = {r[1] for r in cursor.execute("PRAGMA table_info(classified_gems)")}
            inv_cols = {r[1] for r in cursor.execute("PRAGMA table_info(inventory)")}
            if "genai_uses" in cls_cols and "genai_uses" in inv_cols:
                genai_select = "COALESCE(c.genai_uses, i.genai_uses) AS resolved_genai_uses"
            elif "genai_uses" in cls_cols:
                genai_select = "c.genai_uses AS resolved_genai_uses"
            elif "genai_uses" in inv_cols:
                genai_select = "i.genai_uses AS resolved_genai_uses"
            else:
                genai_select = "NULL AS resolved_genai_uses"

            cursor.execute(f"""
                SELECT c.*, i.homepage, i.source_code_uri, i.context7_id, i.preferred,
                       {genai_select}
                FROM classified_gems c
                LEFT JOIN inventory i ON c.name = i.name
                WHERE c.name = ?
            """, (name,))
            row = cursor.fetchone()
            if not row:
                return None
            cls_data = json.loads(row["classification"])
            if not cls_data.get("sub_categories") and "sub_categories" in row.keys() and row["sub_categories"]:
                try:
                    cls_data["sub_categories"] = json.loads(row["sub_categories"])
                except Exception:
                    pass
            return GemEntry(
                name=row["name"],
                classification=GemClassification(**cls_data),
                role=json.loads(row["role"]),
                capabilities=json.loads(row["capabilities"]),
                risks=GemRisks(**json.loads(row["risks"])),
                signals=GemSignals(**json.loads(row["signals"])),
                dependencies=json.loads(row["dependencies"]),
                description=row["description"],
                homepage=row["homepage"],
                source_code_uri=row["source_code_uri"],
                context7_id=row["context7_id"],
                preferred=bool(row["preferred"]),
                genai_uses=row["resolved_genai_uses"],
            )

    def get_inventory_gem(self, name: str) -> Optional[dict]:
        """Fetch a single inventory record by name, or None if not found."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name, version, category, description, homepage, source_code_uri, context7_id, preferred, genai_uses FROM inventory WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_inventory_gems(self) -> List[dict]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name, version, category, description, homepage, source_code_uri, context7_id, preferred, genai_uses FROM inventory")
            return [dict(row) for row in cursor.fetchall()]

    def upsert_inventory_gem(
        self,
        name: str,
        version: Optional[str] = None,
        category: Optional[str] = None,
        description: Optional[str] = None,
        homepage: Optional[str] = None,
        source_code_uri: Optional[str] = None,
        context7_id: Optional[str] = None,
        genai_uses: Optional[str] = None,
        verified: int = 1,
    ):
        """Insert or update a gem inventory record."""
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO inventory (name, version, category, description, homepage, source_code_uri, context7_id, genai_uses, verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version=COALESCE(excluded.version, inventory.version),
                    category=COALESCE(excluded.category, inventory.category),
                    description=COALESCE(excluded.description, inventory.description),
                    homepage=COALESCE(excluded.homepage, inventory.homepage),
                    source_code_uri=COALESCE(excluded.source_code_uri, inventory.source_code_uri),
                    context7_id=COALESCE(excluded.context7_id, inventory.context7_id),
                    genai_uses=COALESCE(excluded.genai_uses, inventory.genai_uses),
                    verified=MAX(excluded.verified, inventory.verified)
            """, (name, version, category, description, homepage, source_code_uri, context7_id, genai_uses, verified))
            conn.commit()

    def enrich_inventory_gem(
        self,
        name: str,
        homepage: Optional[str] = None,
        source_code_uri: Optional[str] = None,
        description: Optional[str] = None,
        context7_id: Optional[str] = None,
        genai_uses: Optional[str] = None,
    ) -> bool:
        """Backfill missing fields for an existing inventory gem without overwriting existing data.
        
        Returns True if any field was updated, False otherwise.
        """
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT homepage, source_code_uri, description, context7_id, genai_uses FROM inventory WHERE name = ?", (name,))
            row = cursor.fetchone()
            if not row:
                return False
            
            cur_home, cur_source, cur_desc, cur_c7, cur_genai = row
            updates = {}
            if not cur_home and homepage:
                updates["homepage"] = homepage
            if not cur_source and source_code_uri:
                updates["source_code_uri"] = source_code_uri
            if not cur_desc and description:
                updates["description"] = description
            if not cur_c7 and context7_id:
                updates["context7_id"] = context7_id
            if not cur_genai and genai_uses:
                updates["genai_uses"] = genai_uses

            if not updates:
                return False

            set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
            values = list(updates.values()) + [name]
            cursor.execute(f"UPDATE inventory SET {set_clause} WHERE name = ?", values)
            conn.commit()
            return True

    def update_gem_verification(
        self,
        name: str,
        homepage: str,
        source_code_uri: str,
        description: str,
        context7_id: str,
        genai_uses: Optional[str] = None,
    ):
        if self.is_read_only:
            raise PermissionError("Storage is in read-only mode")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if genai_uses is not None:
                cursor.execute("""
                    UPDATE inventory 
                    SET homepage = ?, source_code_uri = ?, description = ?, context7_id = ?, genai_uses = ?, verified = 1 
                    WHERE name = ?
                """, (homepage, source_code_uri, description, context7_id, genai_uses, name))
                cursor.execute("""
                    UPDATE classified_gems
                    SET description = ?, genai_uses = ?
                    WHERE name = ?
                """, (description, genai_uses, name))
            else:
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
