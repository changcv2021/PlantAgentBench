"""Portable storage adapter for the frozen PlantAgentBench public release."""
import hashlib
import json
import os
import re
import sqlite3
import zlib
from pathlib import Path


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


class MediaResolver:
    def __init__(self, root, episode):
        self.root = Path(root).resolve()
        self.db = readonly(self.root / 'media/index.sqlite')
        self.episode_id = episode.episode_id
        self.allowed = {e.payload.media_id: e.payload.media_sha256
                        for e in episode.evidence if e.payload.kind == 'media'}

    def read(self, episode_id, media_id, expected_sha256):
        from plantbench.environment import ResolverError
        if episode_id != self.episode_id or self.allowed.get(media_id) != expected_sha256:
            raise ResolverError('MEDIA_UNAVAILABLE')
        row = self.db.execute('SELECT archive,offset,length,sha256 FROM media WHERE media_id=?',
                              (media_id,)).fetchone()
        if row is None or row[3] != expected_sha256:
            raise ResolverError('MEDIA_UNAVAILABLE')
        if not re.fullmatch(r'media/images-[0-9]{5}\.tar', row[0]):
            raise ResolverError('MEDIA_UNAVAILABLE')
        # HF snapshot files can be symlinks to its content-addressed blob cache.
        # Only a fixed archive filename under the selected snapshot is allowed;
        # the requested episode image still requires its exact frozen digest.
        path = self.root / row[0]
        with path.open('rb') as stream:
            stream.seek(row[1]); data = stream.read(row[2])
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ResolverError('MEDIA_INTEGRITY_FAILURE')
        return data

    def close(self):
        self.db.close()


class Dataset:
    """Gold is opened only by the explicit offline gold()/score() methods.

    Use one release root per process. Do not pass episode specifications or Gold
    directly to a model; PlantEnv exposes only the allowed public observation.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.release = json.loads((self.root / 'RELEASE.json').read_text())
        os.environ['PAB_RELEASE_ROOT'] = str(self.root)
        self.db = readonly(self.root / 'catalog.sqlite')
        self.gold_db = None

    def ids(self, *, include_components=False):
        sql = 'SELECT id FROM tasks'
        if not include_components:
            sql += " WHERE kind!='component'"
        yield from (r[0] for r in self.db.execute(sql + ' ORDER BY id'))

    def episode(self, ident, *, formal=True):
        from plantbench.contracts import EpisodeSpec, logical_hash
        from plantbench.care_protocol import revise_episode
        row = self.db.execute('SELECT episode,episode_hash FROM tasks WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        episode = EpisodeSpec.model_validate(json.loads(zlib.decompress(row[0])))
        if logical_hash(episode) != row[1]:
            raise ValueError('FROZEN_EPISODE_CHANGED')
        return revise_episode(episode) if formal else episode

    def resolver(self, episode):
        return MediaResolver(self.root, episode)

    def environment(self, ident, *, seed=0, result_origin='model_run'):
        from plantbench.environment import PlantEnv, ResetOptions
        class ScopedPlantEnv(PlantEnv):
            def close(self):
                super().close()
                self._resolver.close()
        episode = self.episode(ident)
        env = ScopedPlantEnv(episode, self.resolver(episode), result_origin=result_origin)
        observation, info = env.reset(seed=seed, options=ResetOptions(
            episode_handle=episode.task.neutral_episode_handle, variant=episode.identity.variant_id))
        return env, observation, info

    def run(self, ident, adapter, *, run_id, environment_seed=0):
        from plantbench.agent_harness import run_episode
        episode = self.episode(ident)
        resolver = self.resolver(episode)
        try:
            return run_episode(episode, resolver, adapter,
                               run_id=run_id, environment_seed=environment_seed)
        finally:
            resolver.close()

    def gold(self, ident):
        from plantbench.contracts import logical_hash
        if self.gold_db is None:
            self.gold_db = readonly(self.root / 'gold/gold.sqlite')
        row = self.gold_db.execute('SELECT gold,gold_hash FROM gold WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        result = json.loads(zlib.decompress(row[0]))
        if logical_hash(result) != row[1]:
            raise ValueError('FROZEN_GOLD_CHANGED')
        return result

    def score(self, ident, trace):
        from plantbench.agent_scoring import score_agent_trace
        return score_agent_trace(self.episode(ident), self.gold(ident), trace)

    def close(self):
        self.db.close()
        if self.gold_db is not None:
            self.gold_db.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--episode')
    args = parser.parse_args()
    ds = Dataset(args.root)
    if args.episode:
        env, observation, _ = ds.environment(args.episode, result_origin='scripted_validation')
        view = observation.model_dump(mode='json')
        for evidence in view['evidence']:
            if evidence['payload']['kind'] == 'media':
                evidence['payload']['media_base64'] = '<image bytes omitted from terminal display>'
        print(json.dumps(view, ensure_ascii=False, indent=2))
        env.close()
    else:
        print(json.dumps(ds.release, ensure_ascii=False, indent=2))
    ds.close()
