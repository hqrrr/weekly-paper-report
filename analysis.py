"""
Analyse papers collected from Crossref
"""

from datetime import date, timedelta
from pathlib import Path

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
import pandas as pd

from get_data import get_data_by_orcid
from stop_words import DOMAIN_STOP_WORDS
from clustering import run_and_compare
from plot import plot_clusters_interactive, plot_publisher_interactive
from util import map_article_type, load_followed_authors, add_followed_author_flags


def cluster_analysis(df: pd.DataFrame, path: str = "./html/clusters.html"):
    """
    Performs clustering on a dataframe and saves an interactive plot as html.

    Returns:
        df_out: copy of df with columns ['cluster', 'cluster_legend']
        best: ClusteringResult (selected best result)
        all_results: list[ClusteringResult] (kmeans + hdbscan, including metrics)
    """
    print(f"\nClustering on {len(df)} papers")
    df_out = df.copy()

    # same stop-word handling as before
    stop_words = list(ENGLISH_STOP_WORDS.union(DOMAIN_STOP_WORDS))

    # Run both clusterers and choose the best one using your unified evaluation rules
    best, all_results, X_tfidf, vectorizer = run_and_compare(
        df_out,
        title_col="title",
        tfidf_ngram_range=(1, 2),
        tfidf_stop_words=stop_words,
        # KMeans
        k_values=list(range(3, 8)),
        kmeans_use_svd=False,
        kmeans_svd_components=100,
        # HDBSCAN
        hdbscan_min_cluster_size=5,
        hdbscan_use_svd=True,
        hdbscan_svd_components=100,
        # SVD
        svd_var_target=0.5, # 50% explained var
        svd_max_components=80,
        # selection rule
        max_noise_ratio=0.40,
        silhouette_margin=0.03,
        random_state=42,
        top_n_terms=4,  # top picks for each cluster
    )

    # Print comparison metrics (helpful in Actions log too)
    print("\tClustering candidates:")
    for r in all_results:
        m = r.metrics
        print(
            f"\t- {r.method:7s} | "
            f"sil={m.get('silhouette_cosine')} | "
            f"clusters={m.get('n_clusters')} | "
            f"noise={m.get('noise_ratio')} | "
            f"min_sz={m.get('min_cluster_size')} | "
            f"max_share={m.get('max_cluster_share')}"
        )
        if r.method == "kmeans":
            print(f"\t  best_k={m.get('best_k')}")
    print(f"\tSelected: {best.method}")

    # Apply best labels
    df_out["cluster"] = best.labels

    # Cluster terms for legend (handle noise cluster -1)
    cluster_terms = best.cluster_terms

    def _legend_text(c: int) -> str:
        if c == -1:
            return "Noise: (unassigned)"
        terms = cluster_terms.get(int(c), [])
        return f"Cluster {int(c)}: " + ", ".join(terms)

    df_out["cluster_legend"] = df_out["cluster"].map(_legend_text)

    # Plot: keep using TF-IDF for plotting function
    fig = plot_clusters_interactive(df_out, X_tfidf)

    # post_script = """
    # var plot = document.getElementsByClassName('plotly-graph-div')[0];
    # if (plot) {
    #   plot.on('plotly_click', function(e) {
    #     try {
    #       var url = e.points[0].customdata[6]; // paper_link
    #       if (url && String(url).trim().length > 0) {
    #         window.open(url, '_blank');
    #       }
    #     } catch (err) {
    #       // do nothing
    #     }
    #   });
    # }
    # """

    post_script = r"""
    (function () {
      var plot = document.getElementsByClassName('plotly-graph-div')[0];
      if (!plot) return;

      var isTouch =
        ('ontouchstart' in window) ||
        (navigator.maxTouchPoints && navigator.maxTouchPoints > 0);

      // Floating "Open paper" button for mobile
      var btn = document.createElement('a');
      btn.textContent = 'Open paper';
      btn.href = '#';
      btn.target = '_blank';
      btn.rel = 'noopener noreferrer';
      btn.style.position = 'fixed';
      btn.style.right = '16px';
      btn.style.bottom = '16px';
      btn.style.zIndex = 9999;
      btn.style.padding = '10px 12px';
      btn.style.borderRadius = '999px';
      btn.style.border = '1px solid rgba(0,0,0,0.15)';
      btn.style.background = 'rgba(255,255,255,0.95)';
      btn.style.boxShadow = '0 6px 18px rgba(0,0,0,0.12)';
      btn.style.fontFamily = 'system-ui, -apple-system, Segoe UI, Roboto, Arial';
      btn.style.fontSize = '14px';
      btn.style.color = '#111';
      btn.style.textDecoration = 'none';
      btn.style.display = 'none';

      var hint = document.createElement('div');
      hint.textContent = 'Tap a point, then tap “Open paper”.';
      hint.style.position = 'fixed';
      hint.style.right = '16px';
      hint.style.bottom = '60px';
      hint.style.zIndex = 9999;
      hint.style.padding = '8px 10px';
      hint.style.borderRadius = '10px';
      hint.style.border = '1px solid rgba(0,0,0,0.12)';
      hint.style.background = 'rgba(255,255,255,0.95)';
      hint.style.boxShadow = '0 6px 18px rgba(0,0,0,0.10)';
      hint.style.fontFamily = btn.style.fontFamily;
      hint.style.fontSize = '12px';
      hint.style.color = 'rgba(0,0,0,0.65)';
      hint.style.display = 'none';

      document.body.appendChild(btn);
      document.body.appendChild(hint);

      var hideTimer = null;
      function showMobileUI(url) {
        if (!url) return;
        btn.href = url;
        btn.style.display = 'inline-block';
        hint.style.display = 'block';

        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () {
          btn.style.display = 'none';
          hint.style.display = 'none';
        }, 10000);
      }

      if (!isTouch) {
        // Desktop: single click opens directly
        plot.on('plotly_click', function (e) {
          try {
            var url = e.points[0].customdata[6]; // paper_link
            if (url && String(url).trim().length > 0) {
              window.open(url, '_blank', 'noopener');
            }
          } catch (err) {}
        });
      } else {
        // Mobile: tap point => show "Open paper" button (reliable)
        plot.on('plotly_click', function (e) {
          try {
            var url = e.points[0].customdata[6];
            if (url && String(url).trim().length > 0) {
              showMobileUI(url);
            }
          } catch (err) {}
        });

        // Optional: tap outside to hide
        document.addEventListener('touchstart', function (ev) {
          // if tap is not on button, don't immediately hide (avoid conflict)
          // but you can hide on a second tap elsewhere if you want.
        }, { passive: true });
      }
    })();
    """
    # make sure ./html exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fig.write_html(
        path,
        include_plotlyjs="cdn",
        full_html=True,
        post_script=post_script,
    )
    print(f"\tSaved clusters interactive html file: {path}")

    return df_out, best, all_results


def top_picks_by_cluster(
    df: pd.DataFrame,
    sort_by: str,
    top_n: int = 3,
    ascending: bool = False,
) -> dict[int, pd.DataFrame]:
    """
    Group by cluster and take top_n rows per cluster after sorting.

    Returns:
        dict: {cluster_id: df_top_cluster}
    """
    if "cluster" not in df.columns:
        raise ValueError(
            "df must contain a 'cluster' column. Run cluster_analysis first."
        )

    if sort_by not in df.columns:
        raise ValueError(f"sort_by column '{sort_by}' not found in df.")

    out = {}
    for c, g in df.groupby("cluster", sort=False):
        g2 = g.sort_values(sort_by, ascending=ascending, na_position="last").head(top_n)
        out[int(c)] = g2
    return out


def publisher_analysis(
    df: pd.DataFrame, path: str = "./html/publishers.html", top_n: int = 8
):
    """
    Performs publisher analysis on a dataframe and
    saves the results as a html file.
    """
    print(f"\nPublisher analysis on {len(df)} papers")
    df_plot = df.copy()
    df_plot["article_type"] = df_plot["type"].apply(map_article_type)

    top_publishers = df_plot["publisher"].value_counts().head(top_n).index

    df_plot = df_plot.copy()
    df_plot["publisher_grouped"] = df_plot["publisher"].where(
        df_plot["publisher"].isin(top_publishers), "Other publishers"
    )

    counts = (
        df_plot.groupby(["publisher_grouped", "article_type"])
        .size()
        .reset_index(name="count")
    )

    publisher_order = (
        counts.groupby("publisher_grouped")["count"]
        .sum()
        .sort_values(ascending=False)
        .index
    )

    # Put Other at the end
    publisher_order = [p for p in publisher_order if p != "Other publishers"] + [
        "Other publishers"
    ]

    fig = plot_publisher_interactive(counts, publisher_order, top_n)

    # make sure ./html exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    fig.write_html(path, include_plotlyjs="cdn")
    print(f"\tSaved publisher interactive html file: {path}")


def followed_authors_analysis(
    df: pd.DataFrame,
    *,
    followed_authors_path: str = "./config/followed_authors.yaml",
    from_date: str | None = None,
    types: list[str] | None = None,
    rows_per_author: int = 50,
    mailto: str | None = None,
):
    """
    Filter 'Papers by followed authors' from the existing dataframe.

    Additional: Query authors' new journal/conference papers from the past week based on ORCIDs in followed_authors.yaml.

    Notes
    -----
    ORCID queries only cover works where the publisher has submitted the ORCID to Crossref metadata

    Returns
    -------
    df_followed_in_results, df_followed_recent_by_orcid
    """
    print(f"\nFollowed authors analysis on {len(df)} papers")
    if from_date is None:
        from_date = (date.today() - timedelta(days=7)).isoformat()

    if types is None:
        types = ["journal-article", "proceedings-article"]

    # Load followed authors
    followed_authors = load_followed_authors(followed_authors_path)

    # Find followed authors in the current search results dataframe
    df_plot = add_followed_author_flags(df.copy(), followed_authors)
    df_followed_in_results = (
        df_plot[df_plot["is_followed_author_paper"]]
        .sort_values("score", ascending=False)
        .copy()
    )

    # Use ORCID to additionally retrieve "New works added by the author in the past week"
    rows = []
    for a in followed_authors:
        orcid = (a.get("orcid") or "").strip()
        name = (a.get("name") or "").strip()
        if not orcid:
            continue

        try:
            df_a = get_data_by_orcid(
                orcid=orcid,
                types=types,
                from_date=from_date,
                rows=rows_per_author,
                mailto=mailto,
                sort="created",
                order="desc",
            )
        except Exception as e:
            print(f"\t[followed-authors] ORCID query failed for {name or orcid}: {e}")
            continue

        if df_a.empty:
            continue

        df_a["followed_author_orcid"] = orcid
        df_a["followed_author_name"] = name or orcid
        rows.append(df_a)

    if rows:
        df_followed_recent_by_orcid = pd.concat(rows, ignore_index=True)
        # Deduplication (where the same article may be hit by
        # multiple authors or overlap with the main search)
        if "doi" in df_followed_recent_by_orcid.columns:
            df_followed_recent_by_orcid["doi_norm"] = (
                df_followed_recent_by_orcid["doi"].astype(str).str.lower()
            )
            df_followed_recent_by_orcid = df_followed_recent_by_orcid.drop_duplicates(
                "doi_norm"
            )
    else:
        df_followed_recent_by_orcid = pd.DataFrame()

    print("\tPapers by followed authors (found in keyword results)")
    if df_followed_in_results.empty:
        print("\t\t- None")
    else:
        for _, row in df_followed_in_results.iterrows():
            print(
                f"\t\t- {row['title']} ({row['container_title']}) {row['doi']} [{row.get('followed_author_label','')}]"
            )

    print("\tPapers by followed authors (found with ORCID)")
    if df_followed_recent_by_orcid.empty:
        print("\t\t- None")
    else:
        for _, row in df_followed_recent_by_orcid.sort_values(
            "created_date", ascending=False
        ).iterrows():
            print(
                f"\t\t- {row['title']} ({row['container_title']}) {row['doi']}  — {row.get('followed_author_name','')}"
            )

    return df_followed_in_results, df_followed_recent_by_orcid
