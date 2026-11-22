from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import psutil
from sentence_transformers import SentenceTransformer

from common import LOG_DIR, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--model", type=Path, required=True)
args = parser.parse_args()

passages = [
    "passage: סטודיו נועה נמצא בחיפה ומציע שיעורי יוגה בקבוצות קטנות.",
    "passage: NovaSec מסייעת לצוותי IT למפות הרשאות בארגונים בינוניים.",
    "passage: Luma הוא בית קפה שכונתי עם קלייה מקומית.",
    "passage: The product helps IT teams map permissions.",
]
queries = [
    "query: איפה נמצא סטודיו היוגה?",
    "query: איזה מותג עוזר למפות הרשאות?",
    "query: Which product helps IT teams understand permissions?",
]
expected = [0, 1, 3]
process = psutil.Process()
rss_before = process.memory_info().rss / 1024 / 1024
load_started = time.time()
model = SentenceTransformer(str(args.model), device="cpu", local_files_only=True)
load_seconds = time.time() - load_started
embed_started = time.time()
passage_vectors = model.encode(passages, normalize_embeddings=True)
query_vectors = model.encode(queries, normalize_embeddings=True)
embed_seconds = time.time() - embed_started
scores = np.asarray(query_vectors) @ np.asarray(passage_vectors).T
rankings = np.argsort(-scores, axis=1)
results = []
for index, query in enumerate(queries):
    results.append({
        "query": query,
        "expected_top_index": expected[index],
        "actual_top_index": int(rankings[index, 0]),
        "correct": int(rankings[index, 0]) == expected[index],
        "scores": [float(value) for value in scores[index]],
    })
payload = {
    "model": str(args.model), "device": "cpu", "load_seconds": load_seconds,
    "embed_seconds": embed_seconds, "texts_encoded": len(passages) + len(queries),
    "texts_per_second": (len(passages) + len(queries)) / embed_seconds,
    "rss_before_mib": rss_before, "rss_after_mib": process.memory_info().rss / 1024 / 1024,
    "all_top1_correct": all(result["correct"] for result in results), "results": results,
}
path = LOG_DIR / "multilingual-e5-base-benchmark.json"
write_json(path, payload)
print(path)
