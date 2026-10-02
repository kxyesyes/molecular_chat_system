"""Offline evaluation for reverse-target ranking.

Similarity scores are not activity probabilities.  These metrics therefore
require explicit benchmark labels and are never calculated from the live
similarity threshold alone.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Iterable, Mapping

import pandas as pd


def evaluate_ranked_predictions(rows: Iterable[Mapping], top_k: int = 10) -> dict:
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    groups = defaultdict(list)
    for row in rows:
        query_id = str(row.get("query_id", ""))
        try:
            score = float(row["score"])
            label = int(row["label"])
        except (KeyError, TypeError, ValueError):
            continue
        if not query_id or not math.isfinite(score) or label not in (0, 1):
            continue
        groups[query_id].append((score, label))
    if not groups:
        return {
            "query_count": 0, "top_k_recall": 0.0, "mrr": 0.0,
            "enrichment_factor": 0.0, "bedroc": 0.0, "pr_auc": 0.0,
            "calibration": {"ece": 0.0, "brier": 0.0, "bins": []},
        }

    recalls = []
    reciprocal_ranks = []
    top_hits = 0
    total_positives = 0
    flattened = []
    for values in groups.values():
        ranked = sorted(values, key=lambda item: item[0], reverse=True)
        positives = sum(label for _, label in ranked)
        total_positives += positives
        top = ranked[:top_k]
        recalls.append(sum(label for _, label in top) / positives if positives else 0.0)
        rank = next((index for index, (_, label) in enumerate(ranked, 1) if label), None)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        top_hits += sum(label for _, label in top)
        flattened.extend(ranked)

    total_rows = len(flattened)
    overall_rate = total_positives / total_rows if total_rows else 0.0
    sampled_top = sum(min(top_k, len(values)) for values in groups.values())
    enrichment = (top_hits / sampled_top) / overall_rate if sampled_top and overall_rate else 0.0
    return {
        "query_count": len(groups),
        "top_k_recall": round(sum(recalls) / len(recalls), 6),
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
        "enrichment_factor": round(enrichment, 6),
        "bedroc": round(_bedroc(flattened), 6),
        "pr_auc": round(_average_precision(flattened), 6),
        "calibration": _calibration(flattened),
    }


def _average_precision(values):
    ranked = sorted(values, key=lambda item: item[0], reverse=True)
    positives = sum(label for _, label in ranked)
    if not positives:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for index, (_, label) in enumerate(ranked, 1):
        if label:
            hits += 1
            precision_sum += hits / index
    return precision_sum / positives


def _bedroc(values, alpha=20.0):
    ranked = sorted(values, key=lambda item: item[0], reverse=True)
    positives = [index for index, (_, label) in enumerate(ranked, 1) if label]
    n = len(ranked)
    if not positives or n == len(positives):
        return 1.0 if positives else 0.0
    observed = sum(math.exp(-alpha * rank / n) for rank in positives) / len(positives)
    random_expectation = (1 - math.exp(-alpha)) / (alpha * (1 - 1 / n))
    best = math.exp(-alpha / n)
    denominator = best - random_expectation
    return max(0.0, min(1.0, (observed - random_expectation) / denominator)) if denominator else 0.0


def _calibration(values, bins=10):
    buckets = [[] for _ in range(bins)]
    for score, label in values:
        index = min(bins - 1, max(0, int(score * bins)))
        buckets[index].append((score, label))
    total = len(values)
    ece = 0.0
    brier = 0.0
    output = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        predicted = sum(score for score, _ in bucket) / len(bucket)
        observed = sum(label for _, label in bucket) / len(bucket)
        ece += len(bucket) / total * abs(predicted - observed)
        brier += sum((score - label) ** 2 for score, label in bucket)
        output.append({"bin": index, "count": len(bucket), "mean_score": round(predicted, 6), "positive_rate": round(observed, 6)})
    return {"ece": round(ece, 6), "brier": round(brier / total, 6) if total else 0.0, "bins": output}


def scaffold_split(frame: pd.DataFrame, smiles_column: str = "canonical_smiles", fractions=(0.8, 0.1, 0.1), seed: int = 42):
    if len(fractions) != 3 or not math.isclose(sum(fractions), 1.0, rel_tol=0, abs_tol=1e-6):
        raise ValueError("fractions must sum to 1")
    try:
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except ImportError as exc:
        raise RuntimeError("RDKit is required for scaffold split") from exc
    groups = defaultdict(list)
    for index, smiles in frame[smiles_column].items():
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(smiles=str(smiles), includeChirality=False)
        groups[scaffold].append(index)
    rng = random.Random(seed)
    group_values = list(groups.values())
    rng.shuffle(group_values)
    targets = [len(frame) * fraction for fraction in fractions]
    partitions = [[], [], []]
    sizes = [0, 0, 0]
    for group in sorted(group_values, key=len, reverse=True):
        destination = min(range(3), key=lambda position: (sizes[position] / max(targets[position], 1), position))
        partitions[destination].extend(group)
        sizes[destination] += len(group)
    return tuple(frame.loc[indices].sort_index() for indices in partitions)
