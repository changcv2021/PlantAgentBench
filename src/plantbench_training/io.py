"""Small trajectory I/O separated from the original laboratory scheduler."""
import gzip
import json


def read_json_gz(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)
