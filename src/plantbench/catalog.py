"""Immutable, indexed task catalog consumed by every evaluation adapter.

EpisodeSpec contains provenance and is never itself sent to a model. Only
PlantEnv's public observation crosses that boundary; gold is stored separately.
"""
import hashlib
import json
import sqlite3
import zlib
from pathlib import Path
from .contracts import EpisodeSpec, logical_hash
from .preparation.taskpack import PackedMediaResolver


def pack(value):
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode(), 3)


def unpack(value):
    return json.loads(zlib.decompress(value))


def readonly(path):
    # immutable is allowed only after the producer has finished; callers must
    # first check its completion receipt, or the final build lock.
    db=sqlite3.connect('file:' + str(Path(path).resolve()) + '?mode=ro&immutable=1', uri=True)
    db.execute('PRAGMA cache_size=-65536')
    return db


class Catalog:
    def __init__(self, root, *, require_lock=True):
        self.root = Path(root)
        if require_lock and not (self.root/'build.lock.json').is_file():
            raise ValueError('A frozen catalog is required for full evaluation')
        self.lock = json.loads((self.root/'build.lock.json').read_text()) if require_lock else None
        self.db = readonly(self.root/'private/catalog.sqlite')
        self.db.row_factory = sqlite3.Row
        self.has_independent_group='independent_group' in {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}

    def iter_metadata(self, *, shard_index=0, shard_count=1, include_components=True):
        if not 0 <= shard_index < shard_count:
            raise ValueError('Invalid exhaustive shard assignment')
        sql = 'SELECT id,source,primary_p,template,subject,case_group,split,role,kind,basis,subtype,media_db,episode_hash FROM tasks'
        if self.has_independent_group:sql=sql.replace(' FROM tasks',',independent_group FROM tasks')
        if not include_components:
            sql += " WHERE kind!='component'"
        for row in self.db.execute(sql + ' ORDER BY id'):
            # Sharding changes placement, never task eligibility or denominator.
            if int(hashlib.sha256(row['id'].encode()).hexdigest()[:16], 16) % shard_count == shard_index:
                yield dict(row)

    def episode(self, ident):
        fields='episode,episode_hash,case_group,split,exposure'+(',independent_group' if self.has_independent_group else '')
        row = self.db.execute('SELECT '+fields+' FROM tasks WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        ep = EpisodeSpec.model_validate(unpack(row['episode']))
        if logical_hash(ep) != row['episode_hash']:
            raise ValueError('Frozen episode changed')
        if (ep.identity.leakage_group_id, ep.identity.split, ep.agentic.exposure_status) != (row['case_group'], row['split'], row['exposure']):
            raise ValueError('Catalog group and episode disagree')
        if self.has_independent_group and ep.identity.inference_group_id!=row['independent_group']:
            raise ValueError('Independent statistical group and episode disagree')
        return ep

    def resolver(self, episode):
        row = self.db.execute('SELECT media_db FROM tasks WHERE id=?', (episode.episode_id,)).fetchone()
        paths=json.loads(row[0]) if row[0].startswith('[') else row[0]
        return PackedMediaResolver(paths, episode)

    def close(self):
        self.db.close()


class GoldCatalog:
    """Offline scorer input; never passed into the model-neutral harness."""
    def __init__(self, root):
        self.db = readonly(Path(root)/'private/gold.sqlite')

    def get(self, ident):
        row = self.db.execute('SELECT gold,gold_hash FROM gold WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        value = unpack(row[0])
        if logical_hash(value) != row[1]:
            raise ValueError('Frozen gold changed')
        return value

    def close(self):
        self.db.close()
