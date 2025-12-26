"""
Utility functions
"""

from typing import List, Optional
from datetime import datetime, date, timedelta
from pathlib import Path
import yaml
import math

import pandas as pd
import re


def i18n(en: str, zh: str) -> str:
    """
    Create a bilingual html tag.
    """
    return f'<span lang="en">{en}</span><span lang="zh">{zh}</span>'


def date_parts_to_datetime(parts: Optional[List[List[int]]]) -> Optional[datetime]:
    """
    Convert Crossref date-parts to a datetime.

    Examples:
    [[2025, 12, 1]] -> 2025-12-01
    [[2025, 12]]    -> 2025-12-01 (assume day=1)
    [[2025]]        -> 2025-01-01 (assume month=1, day=1)
    """
    if not parts or not parts[0]:
        return None

    ymd = parts[0]
    year = ymd[0]
    month = ymd[1] if len(ymd) > 1 else 1
    day = ymd[2] if len(ymd) > 2 else 1

    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def last_n_days_iso(n: int) -> str:
    """
    Get date from last n days.
    """
    return (date.today() - timedelta(days=n)).isoformat()


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleanse data by removing anomalous metadata.
    """
    df = df.copy()

    # basic sanity checks
    df = df[df["title"].notna()]
    df = df[df["author_count"] >= 1]

    # language filter (keep English + unknown)
    if "language" in df.columns:
        df = df[
            df["language"].isna() |
            df["language"].str.startswith("en", na=False)
            ]

    return df.reset_index(drop=True)


TYPE_MAP = {
    "journal-article": "Journal article",
    "proceedings-article": "Conference paper",
    "review-article": "Review",
    "posted-content": "Preprint / Posted content",
    "book-chapter": "Book chapter",
    "book": "Book",
    "dissertation": "Dissertation",
    "dataset": "Dataset",
}


def map_article_type(t: str) -> str:
    if not isinstance(t, str):
        return "Other"
    return TYPE_MAP.get(t, "Other")


def load_keywords(path: str | Path) -> list[str]:
    """
    Load keywords from a YAML file.

    Expected format:
    keywords:
      - keyword1
      - keyword2
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"keywords file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    keywords = data.get("keywords", [])
    if not isinstance(keywords, list):
        raise ValueError("keywords must be a list")

    # clean + stringify
    out = []
    for k in keywords:
        if k is None:
            continue
        s = str(k).strip()
        if s:
            out.append(s)

    if not out:
        raise ValueError("keywords list is empty")

    return out


def load_followed_authors(path: str | Path) -> list[dict]:
    """
    Load followed authors configuration from YAML.
    """
    path = Path(path)
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data.get("followed_authors", [])


def normalize_orcid(orcid: str) -> str:
    if not isinstance(orcid, str):
        return ""
    o = orcid.strip()
    return o.replace("https://orcid.org/", "").replace("http://orcid.org/", "")


def add_followed_author_flags(
    df: pd.DataFrame,
    followed_authors: list[dict],
) -> pd.DataFrame:
    """
    Add flags for 'Papers by followed authors'.

    Priority:
    1) ORCID match (robust)
    2) Name match in authors string (fallback)
    """
    df = df.copy()

    # Prepare ORCID lookup
    orcid_to_name = {}
    followed_orcids = set()
    fallback_names = []

    for a in followed_authors:
        display_name = (a.get("name") or "").strip()

        orcid = normalize_orcid(a.get("orcid", ""))
        if orcid:
            followed_orcids.add(orcid)
            orcid_to_name[orcid] = display_name or orcid

        for n in a.get("names", []):
            n = (n or "").strip()
            if n:
                fallback_names.append(n)

    # ORCID matching
    def match_orcid(orcid_str: str) -> list[str]:
        if not isinstance(orcid_str, str) or not orcid_str.strip():
            return []
        paper_orcids = [normalize_orcid(x) for x in orcid_str.split(";")]
        hits = [orcid_to_name[o] for o in paper_orcids if o in followed_orcids]
        return list(dict.fromkeys(hits))  # de-duplicate, keep order

    df["followed_author_hits"] = df.get("orcids", "").apply(match_orcid)
    df["is_followed_author_paper"] = df["followed_author_hits"].apply(bool)

    # Name fallback
    if fallback_names:
        pattern = "|".join(re.escape(n) for n in fallback_names)
        mask = ~df["is_followed_author_paper"] & df.get("authors", "").fillna(
            ""
        ).str.contains(pattern, case=False, regex=True)

        df.loc[mask, "is_followed_author_paper"] = True
        df.loc[mask, "followed_author_hits"] = df.loc[
            mask, "followed_author_hits"
        ].apply(lambda x: x or ["Name match"])

    # Pretty label for reporting
    df["followed_author_label"] = df["followed_author_hits"].apply(
        lambda xs: ", ".join(xs) if xs else ""
    )

    return df


def add_top_score_flag(
    df: pd.DataFrame,
    *,
    score_col: str = "score",
    frac: float = 0.10,
    out_col: str = "is_top_score",
) -> pd.DataFrame:
    """
    Mark the top 'frac' papers (e.g. 0.10 = 10%) by Crossref score within this dataframe.
    """
    df = df.copy()

    if score_col not in df.columns or df.empty:
        df[out_col] = False
        return df

    s = pd.to_numeric(df[score_col], errors="coerce")
    df[out_col] = False

    valid = s.notna()
    n_valid = int(valid.sum())
    if n_valid <= 0:
        return df

    top_n = max(1, int(math.ceil(frac * n_valid)))

    # rank by score desc; stable tie-breaker uses original order
    idx_top = s[valid].sort_values(ascending=False).head(top_n).index
    df.loc[idx_top, out_col] = True
    return df
