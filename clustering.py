"""
Clustering utilities for weekly-paper-report.
"""

from dataclasses import dataclass
from typing import Any, Literal, Optional

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.cluster import KMeans, HDBSCAN
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize


@dataclass
class ClusteringResult:
    method: Literal["kmeans", "hdbscan"]
    labels: np.ndarray  # shape (n_samples,), noise labeled as -1 where applicable
    metrics: dict[str, float | int | None]
    cluster_terms: dict[
        int, list[str]
    ]  # top terms per cluster (noise excluded by default)
    meta: dict[str, Any]


# Vectorization
def vectorize_titles_tfidf(
    df: pd.DataFrame,
    *,
    title_col: str = "title",
    max_features: int = 800,
    ngram_range: tuple[int, int] = (1, 2),
    min_df: int = 2,
    stop_words: str | Any = "english",
) -> tuple[csr_matrix, TfidfVectorizer]:
    """
    Vectorize paper titles using TF-IDF.

    Parameters
    ----------
    df:
        Cleaned DataFrame containing a 'title' column.
    title_col:
        Column in df containing the title.
    max_features:
        Maximum number of TF-IDF features to keep.
    ngram_range:
        N-gram range. (1, 2) is recommended for short texts like titles.
    min_df:
        Ignore terms that appear in fewer than `min_df` documents.
        Helps reduce noise from rare words.
    stop_words:
        Stop-word list. Use "english" or custom stop words.

    Returns
    -------
    X:
        TF-IDF feature matrix (documents x features).
    vectorizer:
        Fitted TfidfVectorizer instance.
    """
    if title_col not in df.columns:
        raise KeyError(f"Column '{title_col}' not found in df.")

    titles = df[title_col].fillna("").astype(str).str.strip().tolist()

    vectorizer = TfidfVectorizer(
        max_features=max_features,
        ngram_range=ngram_range,
        min_df=min_df,
        stop_words=stop_words,
    )
    X = vectorizer.fit_transform(titles)
    return X, vectorizer


# Evaluation
def _cluster_size_stats(labels: np.ndarray) -> tuple[int, int, float, float]:
    """
    Returns:
      - n_clusters (excluding noise -1)
      - min_cluster_size (excluding noise)
      - max_cluster_share (excluding noise)
      - noise_ratio
    """
    labels = np.asarray(labels)
    noise_mask = labels == -1
    noise_ratio = float(noise_mask.mean())

    core = labels[~noise_mask]
    if core.size == 0:
        return 0, 0, 0.0, noise_ratio

    uniq, counts = np.unique(core, return_counts=True)
    n_clusters = int(uniq.size)
    min_cluster_size = int(counts.min())
    max_cluster_share = float(counts.max() / core.size)
    return n_clusters, min_cluster_size, max_cluster_share, noise_ratio


def eval_clustering(
    X_for_metrics: np.ndarray,
    labels: np.ndarray,
    *,
    metric: Literal["cosine"] = "cosine",
) -> dict[str, float | int | None]:
    """
    Unified clustering evaluation.

    Notes:
    - For density-based methods, noise is labeled as -1. Compute silhouette on non-noise points only.
    - Silhouette requires >=2 clusters and enough sufficient samples.
    """
    labels = np.asarray(labels)
    n_clusters, min_sz, max_share, noise_ratio = _cluster_size_stats(labels)

    # silhouette on core points only
    noise_mask = labels == -1
    core_mask = ~noise_mask
    core_labels = labels[core_mask]

    sil: Optional[float] = None
    try:
        if core_mask.sum() >= 3 and len(np.unique(core_labels)) >= 2:
            sil = float(
                silhouette_score(X_for_metrics[core_mask], core_labels, metric=metric)
            )
    except Exception:
        sil = None

    return {
        "silhouette_cosine": sil,
        "n_clusters": n_clusters,
        "noise_ratio": noise_ratio,
        "min_cluster_size": min_sz,
        "max_cluster_share": max_share,
        "n_core": int(core_mask.sum()),
        "n_total": int(labels.size),
    }


# Top terms (for naming clusters)
def top_terms_by_cluster(
    labels: np.ndarray,
    X_tfidf: csr_matrix,
    vectorizer: TfidfVectorizer,
    *,
    top_n: int = 10,
    include_noise: bool = False,
) -> dict[int, list[str]]:
    """
    Compute top TF-IDF terms per cluster using mean TF-IDF within that cluster.

    This always uses the ORIGINAL TF-IDF space (X_tfidf), even if clustering is done on
    an SVD-reduced space. That way the cluster naming stays interpretable.
    """
    labels = np.asarray(labels)
    terms = vectorizer.get_feature_names_out()
    out: dict[int, list[str]] = {}

    uniq = np.unique(labels)
    for c in sorted(int(x) for x in uniq):
        if c == -1 and not include_noise:
            continue

        idx = np.where(labels == c)[0]
        if idx.size == 0:
            continue

        # mean over sparse rows -> 1 x n_features
        mean_tfidf = X_tfidf[idx].mean(axis=0)
        # convert to flat array
        vec = np.asarray(mean_tfidf).ravel()
        top_idx = vec.argsort()[::-1][:top_n]
        out[c] = [str(terms[i]) for i in top_idx]

    return out


# Optional reduction (TF-IDF -> SVD)
def reduce_svd(
    X_tfidf: csr_matrix,
    *,
    n_components: int = 100,
    random_state: int = 42,
    l2_normalize: bool = True,
    var_target: float | None = None,    # e.g., 0.85 or 0.9, None means fixed n_components
    max_components: int = 100,
) -> tuple[np.ndarray, TruncatedSVD, dict[str, Any]]:
    """
    Reduce sparse TF-IDF to dense low-dimensional vectors via TruncatedSVD.

    Recommended for density-based clustering (HDBSCAN) on text.

    If var_target is provided, automatically pick the smallest k such that
    cumulative explained_variance_ratio_ >= var_target, up to max_components.

    Returns (X_red, svd, meta)
    """
    n_samples, n_features = X_tfidf.shape

    # Hard upper bound of rank-ish limits
    hard_max = max(2, min(n_samples - 1, n_features - 1))

    if var_target is None:
        # fixed components
        k = max(2, min(n_components, hard_max))
        svd = TruncatedSVD(n_components=k, random_state=random_state)
        X_red = svd.fit_transform(X_tfidf)
        evr = svd.explained_variance_ratio_
        cum = float(np.cumsum(evr)[-1]) if evr is not None and evr.size else 0.0
        meta = {
            "svd_mode": "fixed",
            "svd_components": int(svd.n_components),
            "svd_explained_var": cum,
        }
    else:
        # auto mode by explained variance
        max_k = max(2, min(int(max_components), hard_max))
        svd0 = TruncatedSVD(n_components=max_k, random_state=random_state)
        X0 = svd0.fit_transform(X_tfidf)

        evr0 = svd0.explained_variance_ratio_
        if evr0 is None or evr0.size == 0:
            # fallback
            svd = svd0
            X_red = X0
            meta = {
                "svd_mode": "auto",
                "svd_components": int(svd.n_components),
                "svd_explained_var": None,
                "svd_var_target": float(var_target),
            }
        else:
            cum0 = np.cumsum(evr0)
            k = int(np.searchsorted(cum0, var_target) + 1)
            k = max(2, min(k, max_k))

            if k < max_k:
                svd = TruncatedSVD(n_components=k, random_state=random_state)
                X_red = svd.fit_transform(X_tfidf)
                evr = svd.explained_variance_ratio_
                cum = float(np.cumsum(evr)[-1]) if evr is not None and evr.size else float(cum0[k - 1])
            else:
                svd = svd0
                X_red = X0
                cum = float(cum0[-1])

            meta = {
                "svd_mode": "auto",
                "svd_components": int(svd.n_components),
                "svd_explained_var": float(cum),
                "svd_var_target": float(var_target),
                "svd_max_components": int(max_k),
            }

    if l2_normalize:
        X_red = normalize(X_red, norm="l2", axis=1)

    return X_red, svd, meta


# KMeans
def choose_k_with_silhouette(
    X_for_kmeans: np.ndarray,
    k_values: list[int],
    *,
    random_state: int = 42,
    delta: float = 0.02,
) -> tuple[int, dict[int, float]]:
    """
    Select k using cosine silhouette with a preference for smaller k.

    Among k whose score is within 'delta' of the best score, choose the smallest k.
    """
    scores: dict[int, float] = {}

    for k in k_values:
        if k < 2 or k >= X_for_kmeans.shape[0]:
            continue

        km = KMeans(n_clusters=k, n_init="auto", random_state=random_state)
        labels = km.fit_predict(X_for_kmeans)

        try:
            score = float(silhouette_score(X_for_kmeans, labels, metric="cosine"))
        except Exception:
            score = -1.0

        scores[k] = score

    if not scores:
        raise ValueError("No valid k values evaluated.")

    best_score = max(scores.values())

    # candidates within margin
    candidate_ks = [k for k, s in scores.items() if s >= best_score - delta]

    best_k = min(candidate_ks)
    return best_k, scores


def run_kmeans(
    X_tfidf: csr_matrix,
    vectorizer: TfidfVectorizer,
    *,
    k_values: Optional[list[int]] = None,
    use_svd: bool = False,
    svd_components: int = 100,
    svd_var_target: float | None = 0.6,    # None -> fixed svd_components, 0.00-1.00 -> auto
    svd_max_components: int = 80,
    random_state: int = 42,
    top_n_terms: int = 10,
) -> ClusteringResult:
    """
    Run KMeans clustering with automatic k selection.

    - Default: cluster in TF-IDF space (but converted to dense vectors for metric scoring).
    - Optionally: do SVD reduction before KMeans (often improves stability on short titles).
    """
    if k_values is None:
        # sensible default for ~100 papers
        k_values = list(range(3, 8))

    if use_svd:
        X_vec, svd, meta_svd2 = reduce_svd(
            X_tfidf,
            n_components=svd_components,
            random_state=random_state,
            l2_normalize=True,
            var_target=svd_var_target,
            max_components=svd_max_components,
        )
        meta_svd: dict[str, Any] = {"use_svd": True, **meta_svd2}
    else:
        X_vec = X_tfidf.toarray()
        X_vec = normalize(X_vec, norm="l2", axis=1)
        meta_svd = {"use_svd": False}

    best_k, k_scores = choose_k_with_silhouette(
        X_vec, k_values, random_state=random_state
    )

    model = KMeans(n_clusters=best_k, n_init="auto", random_state=random_state)
    labels = model.fit_predict(X_vec)

    metrics = eval_clustering(X_vec, labels, metric="cosine")
    metrics["best_k"] = int(best_k)

    terms = top_terms_by_cluster(
        labels, X_tfidf, vectorizer, top_n=top_n_terms, include_noise=False
    )

    return ClusteringResult(
        method="kmeans",
        labels=np.asarray(labels, dtype=int),
        metrics=metrics,
        cluster_terms=terms,
        meta={
            "k_values": k_values,
            "k_silhouette_scores": k_scores,
            "random_state": random_state,
            **meta_svd,
        },
    )


# HDBSCAN
def run_hdbscan(
    X_tfidf: csr_matrix,
    vectorizer: TfidfVectorizer,
    *,
    min_cluster_size: int = 5,
    min_samples: Optional[int] = None,
    cluster_selection_method: Literal["eom", "leaf"] = "eom",
    allow_single_cluster: bool = False,
    use_svd: bool = True,
    svd_components: int = 100,
    svd_var_target: float | None = 0.6,    # None -> fixed svd_components, 0.00-1.00 -> auto
    svd_max_components: int = 80,
    random_state: int = 42,
    top_n_terms: int = 10,
) -> ClusteringResult:
    """
    Run HDBSCAN clustering.

    Practical default for text:
    - use_svd=True (TF-IDF -> TruncatedSVD -> L2 normalize) then HDBSCAN.
    This tends to be much more stable than running HDBSCAN directly on sparse TF-IDF.
    """
    if use_svd:
        X_vec, svd, meta_svd2 = reduce_svd(
            X_tfidf,
            n_components=svd_components,
            random_state=random_state,
            l2_normalize=True,
            var_target=svd_var_target,
            max_components=svd_max_components,
        )
        meta_svd: dict[str, Any] = {"use_svd": True, **meta_svd2}
    else:
        X_vec = X_tfidf.toarray()
        X_vec = normalize(X_vec, norm="l2", axis=1)
        meta_svd = {"use_svd": False}

    model = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_method=cluster_selection_method,
        allow_single_cluster=allow_single_cluster,
        copy=True,
    )
    labels = model.fit_predict(X_vec)

    metrics = eval_clustering(X_vec, labels, metric="cosine")
    terms = top_terms_by_cluster(
        labels, X_tfidf, vectorizer, top_n=top_n_terms, include_noise=False
    )

    return ClusteringResult(
        method="hdbscan",
        labels=np.asarray(labels, dtype=int),
        metrics=metrics,
        cluster_terms=terms,
        meta={
            "min_cluster_size": min_cluster_size,
            "min_samples": min_samples,
            "cluster_selection_method": cluster_selection_method,
            "allow_single_cluster": allow_single_cluster,
            "random_state": random_state,
            **meta_svd,
        },
    )


# Compare & choose best
def choose_best_result(
    results: list[ClusteringResult],
    *,
    max_noise_ratio: float = 0.30,
    silhouette_margin: float = 0.03,
) -> ClusteringResult:
    """
    Choose best clustering result using a pragmatic, report-friendly rule:

    1) Disqualify results with too much noise (HDBSCAN can mark noise as -1).
    2) Prefer higher cosine silhouette. HDBSCAN must beat KMeans by a small margin to win,
       otherwise default to KMeans (more stable and assigns every paper).

    This keeps weekly reports consistent and avoids selecting an overly "noisy" clustering.
    """
    # keep only "valid" candidates
    candidates: list[ClusteringResult] = []
    for r in results:
        noise_ratio = float(r.metrics.get("noise_ratio") or 0.0)
        if noise_ratio <= max_noise_ratio:
            candidates.append(r)

    if not candidates:
        # if everything is too noisy, fall back to the first result (usually kmeans)
        return results[0]

    # separate by method for the margin rule
    km = next((r for r in candidates if r.method == "kmeans"), None)
    hd = next((r for r in candidates if r.method == "hdbscan"), None)

    if km is None and hd is None:
        return candidates[0]
    if km is None:
        return hd  # type: ignore[return-value]
    if hd is None:
        return km

    km_sil = km.metrics.get("silhouette_cosine")
    hd_sil = hd.metrics.get("silhouette_cosine")

    # If silhouette is missing, fall back to kmeans
    if km_sil is None and hd_sil is None:
        return km
    if km_sil is None:
        return hd
    if hd_sil is None:
        return km

    # Margin rule: HDBSCAN needs to be clearly better to win
    if float(hd_sil) >= float(km_sil) + silhouette_margin:
        return hd
    return km


def run_and_compare(
    df: pd.DataFrame,
    *,
    title_col: str = "title",
    tfidf_max_features: int = 800,
    tfidf_ngram_range: tuple[int, int] = (1, 2),
    tfidf_min_df: int = 2,
    tfidf_stop_words: str | Any = "english",
    # KMeans
    k_values: Optional[list[int]] = None,
    kmeans_use_svd: bool = False,
    kmeans_svd_components: int = 100,
    # HDBSCAN
    hdbscan_min_cluster_size: int = 5,
    hdbscan_min_samples: Optional[int] = None,
    hdbscan_cluster_selection_method: Literal["eom", "leaf"] = "eom",
    hdbscan_allow_single_cluster: bool = False,
    hdbscan_use_svd: bool = True,
    hdbscan_svd_components: int = 100,
    # SVD
    svd_var_target: float | None = 0.5, # None -> fixed svd_components, 0.00-1.00 -> auto
    svd_max_components: int = 80,
    # selection
    max_noise_ratio: float = 0.30,
    silhouette_margin: float = 0.03,
    # misc
    random_state: int = 42,
    top_n_terms: int = 10,
) -> tuple[ClusteringResult, list[ClusteringResult], csr_matrix, TfidfVectorizer]:
    """
    Convenience entrypoint:
    - vectorize TF-IDF
    - run KMeans + HDBSCAN
    - choose best result
    - return (best, all_results, X_tfidf, vectorizer)
    """
    X_tfidf, vectorizer = vectorize_titles_tfidf(
        df,
        title_col=title_col,
        max_features=tfidf_max_features,
        ngram_range=tfidf_ngram_range,
        min_df=tfidf_min_df,
        stop_words=tfidf_stop_words,
    )

    res_km = run_kmeans(
        X_tfidf,
        vectorizer,
        k_values=k_values,
        use_svd=kmeans_use_svd,
        svd_components=kmeans_svd_components,
        svd_var_target=svd_var_target,
        svd_max_components=svd_max_components,
        random_state=random_state,
        top_n_terms=top_n_terms,
    )

    res_hd = run_hdbscan(
        X_tfidf,
        vectorizer,
        min_cluster_size=hdbscan_min_cluster_size,
        min_samples=hdbscan_min_samples,
        cluster_selection_method=hdbscan_cluster_selection_method,
        allow_single_cluster=hdbscan_allow_single_cluster,
        use_svd=hdbscan_use_svd,
        svd_components=hdbscan_svd_components,
        svd_var_target=svd_var_target,
        svd_max_components=svd_max_components,
        random_state=random_state,
        top_n_terms=top_n_terms,
    )

    all_results = [res_km, res_hd]
    best = choose_best_result(
        all_results,
        max_noise_ratio=max_noise_ratio,
        silhouette_margin=silhouette_margin,
    )
    return best, all_results, X_tfidf, vectorizer
