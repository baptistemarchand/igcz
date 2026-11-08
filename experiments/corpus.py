"""Access to the local copy of the Open IGC Database (one .igc.gz per flight).

The corpus is split once and for all by a hash of the file name:
- "train" (1% of flights) is the only data make_primer.py may learn from,
- "test" (the other 99%) is what bench.py measures.
A hash split is stable as the corpus grows and spreads both halves over every year.
"""

import gzip
import hashlib
import os
import random
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[2] / "open_igc_database" / "igc_gz"
CORPUS = Path(os.environ.get("IGC_CORPUS", DEFAULT))
TRAIN_PERCENT = 1


def read_flight(path):
    """Bytes of an .igc file, transparently gunzipping .gz files."""
    data = Path(path).read_bytes()
    return gzip.decompress(data) if str(path).endswith(".gz") else data


def is_train(path):
    digest = hashlib.sha1(Path(path).name.encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100 < TRAIN_PERCENT


def paths(split, sample=None, seed=1):
    """Corpus files of one split ("train", "test" or "all"), optionally a fixed random sample of them."""
    files = sorted(p for p in CORPUS.iterdir() if p.name.endswith(".igc.gz"))
    if split != "all":
        files = [p for p in files if is_train(p) == (split == "train")]
    if sample is not None and sample < len(files):
        files = sorted(random.Random(seed).sample(files, sample))
    return files
