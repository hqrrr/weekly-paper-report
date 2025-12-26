"""
Generate paper report
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
import shutil
import os
import html

import pandas as pd

from translate import DeepLTitleTranslator
from util import load_followed_authors, i18n


TABLE_HEADERS = {
    "title": ("Title", "标题"),
    "container_title": ("Journal / Conference", "期刊 / 会议"),
    "type": ("Type", "类型"),
    "score": ("Relevance Score", "相关性评分"),
    "created_date": ("Created Date", "创建日期"),
    "followed_author_name": ("Followed Author", "已关注作者"),
    "followed_author_orcid": ("Author ORCID", "作者 ORCID"),
}


def df_with_i18n_headers(df: pd.DataFrame) -> pd.DataFrame:
    df_disp = df.copy()
    df_disp = df_disp.rename(
        columns={
            k: i18n(v[0], v[1])
            for k, v in TABLE_HEADERS.items()
            if k in df_disp.columns
        }
    )
    return df_disp


@dataclass
class PlotEmbed:
    """An HTML plot (e.g., Plotly output) to embed into the report via iframe."""

    title: str
    path: str  # e.g. "./html/clusters.html"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _safe_str(x) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


def _doi_to_url(doi: str) -> str:
    doi = (doi or "").strip()
    if not doi:
        return ""
    return f"https://doi.org/{doi}"


def resolve_theme_css(theme: str, themes_dir: str = "./themes") -> str:
    """
    Resolve a theme CSS file path.

    If <themes_dir>/<theme>.css does not exist, fall back to <themes_dir>/light.css.
    If neither exists, return "" (no stylesheet link).
    """
    themes_path = Path(themes_dir)

    if not themes_path.exists():
        return ""

    theme = (theme or "").strip() or "light"
    theme_file = themes_path / f"{theme}.css"
    if theme_file.exists():
        return str(theme_file)

    fallback = themes_path / "light.css"
    if fallback.exists():
        return str(fallback)

    return ""


def _to_abs_path(p: str) -> Path:
    """Interpret a path relative to current working directory and return an absolute Path."""
    path = Path(p)
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def _copy_file(src_abs: Path, dst_abs: Path) -> None:
    _ensure_dir(dst_abs.parent)
    shutil.copy2(src_abs, dst_abs)


def _df_to_html_table(
    df: Optional[pd.DataFrame],
    *,
    title: Optional[str] = None,
    max_rows: int = 30,
    columns: Optional[Sequence[str]] = None,
    link_doi: bool = True,
) -> str:
    """
    Convert a DataFrame into a compact HTML table.

    - Limits rows for readability
    - Optionally links DOI to doi.org
    - Keeps HTML safe by escaping all non-link cells
    """
    if df is None or df.empty:
        return (
            f'<h3>{title}</h3><p><em><span lang="en">None</span><span lang="zh">无</span></em></p>'
            if title
            else '<p><em><span lang="en">None</span><span lang="zh">无</span></em></p>'
        )

    df_show = df.copy()

    # Select columns if provided (only keep columns that exist)
    if columns is not None:
        cols = [c for c in columns if c in df_show.columns]
        if cols:
            df_show = df_show[cols]

    # Link DOI
    if link_doi and "doi" in df_show.columns:

        def _doi_link(d):
            d = _safe_str(d).strip()
            if not d:
                return ""
            url = _doi_to_url(d)
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{d}</a>'

        df_show["doi"] = df_show["doi"].apply(_doi_link)

    # Limit rows
    if max_rows is not None and len(df_show) > max_rows:
        df_show = df_show.head(int(max_rows))

    html_table = df_show.to_html(
        index=False,
        escape=False,  # allow DOI links
        border=0,
        classes="table",
    )
    html_table = f'<div class="table-wrap">{html_table}</div>'

    if title:
        return f"<h3>{title}</h3>\n{html_table}"
    return html_table


def _embed_iframe(
    title: str,
    src_path: str,
    height: str = "auto",
) -> str:
    """
    Embed an existing HTML file via iframe.
    Uses the given path as-is (relative paths recommended).
    """
    src = _safe_str(src_path).strip()
    if not src:
        return ""

    return f"""
    <section class="card">
        <div class="card-header">
            <h2>
                {title}
                <span lang="en" class="pill">Double-click to reset axes</span>
                <span lang="zh" class="pill">双击以重置坐标轴</span>
                <span lang="en" class="pill">For cluster map: click on a point to open the paper</span>
                <span lang="zh" class="pill">对于聚类图：点击数据点以打开对应论文</span>
            </h2>
            <div class="muted">
                <a href="{src}" target="_blank" rel="noopener noreferrer">
                    <span lang="en">Open Plot</span>
                    <span lang="zh">打开交互图表</span>
                </a>
            </div>
        </div>
        <div class="plot-wrap">
            <iframe class="plot-frame" src="{src}" loading="lazy" style="height:{height};"></iframe>
        </div>
    </section>
    """


def _is_cluster_plot(p_title: str, src_path: str) -> bool:
    t = (p_title or "").lower()
    s = (src_path or "").lower()
    return ("cluster" in t) or ("clusters" in s)


def _escape(s: str) -> str:
    return html.escape(str(s))


def _keywords_to_tag_cloud(keywords: list[str]) -> str:
    tags = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        tags.append(f'<span class="tag">{_escape(kw)}</span>')
    return "\n".join(tags)


def _list_items(items: list[str]) -> str:
    out = []
    for x in items:
        x = str(x).strip()
        if not x:
            continue
        out.append(f"<li>{_escape(x)}</li>")
    return (
        "\n".join(out)
        if out
        else '<li><em><span lang="en">None</span><span lang="zh">无</span></em></li>'
    )


def _followed_authors_to_list_items(followed_authors: list[dict]) -> str:
    items = []
    for a in followed_authors:
        name = (a.get("name") or "").strip()
        orcid = (a.get("orcid") or "").strip()

        if name and orcid:
            # ORCID -> link
            orcid_url = f"https://orcid.org/{orcid}"
            items.append(
                f'{_escape(name)} — <a href="{orcid_url}" target="_blank" rel="noopener noreferrer">{_escape(orcid)}</a>'
            )
        elif name:
            items.append(_escape(name))
        elif orcid:
            orcid_url = f"https://orcid.org/{orcid}"
            items.append(
                f'<a href="{orcid_url}" target="_blank" rel="noopener noreferrer">{_escape(orcid)}</a>'
            )

    # items already escaped/contain safe links -> output li manually (escape=False style)
    if not items:
        return '<li><em><span lang="en">None</span><span lang="zh">无</span></em></li>'
    return "\n".join([f"<li>{x}</li>" for x in items])


def _copy_js(js_src: str, assets_dir: Path, assets_subdir: str) -> str:
    """
    Copy a JS file into assets/js and return the relative href.
    """
    src_abs = _to_abs_path(js_src)
    js_out_dir = assets_dir / "js"
    _ensure_dir(js_out_dir)

    dst_abs = js_out_dir / src_abs.name
    _copy_file(src_abs, dst_abs)

    return f"{assets_subdir}/js/{dst_abs.name}"


def _format_title_for_top_picks(
    df: pd.DataFrame,
    *,
    flag_col: str = "is_top_score",
    title_col: str = "title",
) -> pd.DataFrame:
    """
    Escape title text and highlight rows where flag_col is True.
    Only touches the title column.
    """
    if df is None or df.empty or title_col not in df.columns:
        return df

    df = df.copy()
    has_flag = flag_col in df.columns

    def fmt(row):
        t = row.get(title_col, "")
        t = "" if t is None else str(t)
        t_esc = _escape(t)

        if has_flag and bool(row.get(flag_col, False)):
            # bold + a visible star marker
            return f'<span class="top-score-title">{t_esc}</span>'
        return t_esc

    df[title_col] = df.apply(fmt, axis=1)
    return df


def _append_zh_span(en_html: str, zh_text: str) -> str:
    """
    Append a zh translation in a span, without replacing the English.
    zh span has lang="zh" so the language toggle can hide/show it.
    """
    zh_text = "" if zh_text is None else str(zh_text).strip()
    if not zh_text:
        return en_html
    zh_esc = _escape(zh_text)
    return f'{en_html}<br/><span class="paper-title-zh" lang="zh">{zh_esc}</span>'


def _escape_title_and_optional_zh(en_text: str, zh_text: str | None) -> str:
    """
    For tables that previously output raw title text, escape it to keep HTML safe,
    then append zh span if available.
    """
    en_text = "" if en_text is None else str(en_text)
    en_esc = _escape(en_text)
    if zh_text is None:
        return en_esc
    return _append_zh_span(en_esc, zh_text)



def report_html(
    df_results: pd.DataFrame,
    df_followed_in_results: pd.DataFrame,
    df_followed_recent_by_orcid: pd.DataFrame,
    *,
    plots: Optional[Sequence[PlotEmbed]] = None,
    out_dir: str = "./report",
    out_name: str = "index.html",
    report_title: str = "Weekly Paper Report",
    subtitle: Optional[str] = None,
    top_picks_n: int = 10,
    cluster_top_picks_n: int = 3,
    cluster_sort_by: str = "score",
    theme: str = "light",
    themes_dir: str = "./themes",
    copy_assets: bool = True,
    assets_subdir: str = "assets",
    keywords: Optional[list[str]] = None,
    followed_authors_path: str = "./config/followed_authors.yaml",
    translation_auth_key: str = "",
    translation_source_lang: str = "EN",        # English (all)
    translation_target_lang: str = "ZH-HANS",   # Chinese (simplified)
) -> Path:
    """
    Build a single-page HTML report and save it to <out_dir>/<out_name>.

    Parameters
    ----------
    df_results:
        Keyword search results (used for top picks + summary counts).
    df_followed_in_results:
        Followed-author papers found within keyword results.
    df_followed_recent_by_orcid:
        Followed-author papers found via ORCID lookup in the last week.
    plots:
        List of PlotEmbed(title, path) pointing to existing Plotly HTML files.
        Example:
            plots=[
              PlotEmbed("Cluster map", "./html/clusters.html"),
              PlotEmbed("Publishers × article types", "./html/publishers.html"),
            ]
    out_dir:
        Path to output directory.
    out_name:
        Name of HTML report file.
    report_title:
        Title of HTML report.
    subtitle:
        Subtitle of HTML report.
    top_picks_n:
        Number of top picks to show.
    cluster_top_picks_n:
        Number of top picks to show in clusters.
    cluster_sort_by:
        Criteria to sort top picks in clusters by.
        Defaults to "score" (relevance to keywords).
    theme:
        Theme name (maps to <themes_dir>/<theme>.css). Defaults to "light".
        If not found, falls back to <themes_dir>/light.css.
    themes_dir:
        Directory that contains CSS theme files.
    copy_assets:
        Whether to copy assets to <out_dir>.
    assets_subdir:
        Subdirectory of <out_dir>.
    keywords:
        Keywords used in Crossref query.
    followed_authors_path:
        Path to followed_authors.yaml file.
    translation_auth_key:
        API key for translation using DeepL.
    translation_source_lang:
        Source language for translation. Defaults to "EN": English (all).
    translation_target_lang:
        Target language for translation. Defaults to "ZH-HANS": Chinese (simplified).

    Returns
    -------
    Path to the generated report HTML.
    """
    # translation
    translator = None
    translation_key = (translation_auth_key or "").strip()
    if translation_key:
        translator = DeepLTitleTranslator(translation_key, target_lang=translation_target_lang)

    out_dir_p = Path(out_dir)
    _ensure_dir(out_dir_p)
    out_path = out_dir_p / out_name

    plots = list(plots) if plots else []

    # Theme CSS
    css_src = resolve_theme_css(theme, themes_dir)
    css_href = ""

    assets_dir = out_dir_p / assets_subdir
    plots_dir = assets_dir / "plots"
    themes_out_dir = assets_dir / "themes"

    if copy_assets:
        _ensure_dir(plots_dir)
        _ensure_dir(themes_out_dir)

        if css_src:
            css_src_abs = _to_abs_path(css_src)
            # Keep the same theme filename (e.g., light.css)
            css_dst_abs = themes_out_dir / css_src_abs.name
            _copy_file(css_src_abs, css_dst_abs)
            css_href = f"{assets_subdir}/themes/{css_dst_abs.name}"
        else:
            # No CSS found -> render unstyled HTML
            css_href = ""
    else:
        # Not self-contained -> use the resolved path as-is (likely relative to project root)
        css_href = css_src

    css_link = f'<link rel="stylesheet" href="{css_href}">' if css_href else ""

    subtitle_html = f"<div class='subtitle'>{subtitle}</div>" if subtitle else ""

    # High-level statistics
    n_results = 0 if df_results is None else len(df_results)
    n_followed_in = 0 if df_followed_in_results is None else len(df_followed_in_results)
    n_followed_orcid = (
        0 if df_followed_recent_by_orcid is None else len(df_followed_recent_by_orcid)
    )

    # Query details section data
    keywords = keywords or []
    followed_authors = load_followed_authors(followed_authors_path) or []
    ## generate keyword tags
    keywords_tag_cloud_html = _keywords_to_tag_cloud([str(k) for k in keywords])
    ## followed authors list (use YAML content)
    followed_authors_list_html = _followed_authors_to_list_items(followed_authors)

    # Top picks from keyword results
    df_top = pd.DataFrame()
    if df_results is not None and not df_results.empty:
        df_top = df_results.copy()
        if "score" in df_top.columns:
            df_top = df_top.sort_values("score", ascending=False)
        cols_pref = ["title", "container_title", "type", "score", "doi", "is_top_score"]
        cols = [c for c in cols_pref if c in df_top.columns]
        if cols:
            df_top = df_top[cols]
        df_top = df_top.head(max(1, int(top_picks_n)))

        # try to translate paper titles
        title_raw_col = "__title_raw__"
        if "title" in df_top.columns:
            df_top[title_raw_col] = df_top["title"].astype(str)
            zh_map = {}
            if translator is not None:
                zh_map = translator.translate_titles(df_top[title_raw_col].tolist())

        # highlight titles for top 10%
        df_top = _format_title_for_top_picks(
            df_top, flag_col="is_top_score", title_col="title"
        )

        # append translated title after the English title
        if "title" in df_top.columns and title_raw_col in df_top.columns:
            def _append(row):
                raw = row.get(title_raw_col, "")
                zh = (zh_map.get(raw) if isinstance(zh_map, dict) else None) if raw else None
                return _append_zh_span(row.get("title", ""), zh or "")

            df_top["title"] = df_top.apply(_append, axis=1)
            df_top = df_top.drop(columns=[title_raw_col])

        # remove "is_top_score" columns in the html list
        if "is_top_score" in df_top.columns:
            df_top = df_top.drop(columns=["is_top_score"])

        # translate df header
        df_top = df_with_i18n_headers(df_top)

    # Top picks by cluster
    cluster_picks_html = ""
    if (
        df_results is not None
        and (not df_results.empty)
        and ("cluster" in df_results.columns)
    ):
        dfc = df_results.copy()

        sort_col = cluster_sort_by if cluster_sort_by in dfc.columns else None
        if sort_col is None:
            sort_col = "score" if "score" in dfc.columns else None  # fallback

        # Prefer these columns if they exist
        cols_pref = ["title", "container_title", "type", "score", "doi", "is_top_score"]
        cols_show = [c for c in cols_pref if c in dfc.columns]

        blocks = []
        for c, g in dfc.groupby("cluster", sort=True):
            g2 = g.copy()
            if sort_col is not None:
                g2 = g2.sort_values(sort_col, ascending=False, na_position="last")

            g2 = g2.head(max(1, int(cluster_top_picks_n)))

            # Cluster header label: use cluster_legend if available, else "Cluster X"
            label = f"Cluster {int(c)}"
            if "cluster_legend" in g2.columns:
                legends = g2["cluster_legend"].dropna().astype(str)
                if len(legends) > 0 and legends.iloc[0].strip():
                    label = legends.iloc[0].strip()

            table_df = (g2.loc[:, cols_show].copy() if cols_show else g2.copy())

            # translate paper title
            title_raw_col = "__title_raw__"
            zh_map = {}
            if "title" in table_df.columns:
                table_df[title_raw_col] = table_df["title"].astype(str).str.strip()
                if translator is not None:
                    zh_map = translator.translate_titles(table_df[title_raw_col].tolist())

            # highlight titles for top 10%
            table_df = _format_title_for_top_picks(
                table_df, flag_col="is_top_score", title_col="title"
            )

            # append translated title
            if "title" in table_df.columns and title_raw_col in table_df.columns:
                def _append(row):
                    raw = row.get(title_raw_col, "")
                    zh = zh_map.get(raw, "") if isinstance(zh_map, dict) else None
                    return _append_zh_span(row.get("title", ""), zh or "")

                table_df["title"] = table_df.apply(_append, axis=1)
                table_df = table_df.drop(columns=[title_raw_col])

            # remove "is_top_score" columns in the html list
            if "is_top_score" in table_df.columns:
                table_df = table_df.drop(columns=["is_top_score"])

            # translate df header
            table_df = df_with_i18n_headers(table_df)

            blocks.append(
                f"""
                <div class="cluster-block">
                  <h3 class="cluster-title">{label}</h3>
                  {_df_to_html_table(table_df, title=None, max_rows=cluster_top_picks_n, columns=list(table_df.columns))}
                </div>
                """
            )

        if blocks:
            cluster_picks_html = "\n".join(blocks)

    # Followed authors tables
    cols_followed = ["title", "container_title", "type", "score", "doi"]
    if (
        df_followed_in_results is not None
        and "followed_author_label" in df_followed_in_results.columns
    ):
        cols_followed = cols_followed + ["followed_author_label"]

    cols_orcid_pref = [
        "title",
        "container_title",
        "type",
        "created_date",
        "doi",
        "followed_author_name",
        "followed_author_orcid",
    ]
    cols_orcid = [
        c
        for c in cols_orcid_pref
        if df_followed_recent_by_orcid is not None
        and c in df_followed_recent_by_orcid.columns
    ]

    # Display-only versions with bilingual headers
    df_followed_in_results_disp = df_followed_in_results
    if (
        df_followed_in_results_disp is not None
        and not df_followed_in_results_disp.empty
    ):
        df_followed_in_results_disp = df_followed_in_results_disp.loc[:, cols_followed].copy()

        # translate paper title and append to EN title
        if "title" in df_followed_in_results_disp.columns:
            titles = df_followed_in_results_disp["title"].astype(str).tolist()
            zh_map = {}
            if translator is not None:
                zh_map = translator.translate_titles(titles)

            df_followed_in_results_disp["title"] = df_followed_in_results_disp["title"].apply(
                lambda t: _escape_title_and_optional_zh(t, zh_map.get(str(t), None))
            )

        # translate df header
        df_followed_in_results_disp = df_with_i18n_headers(df_followed_in_results_disp)

    df_followed_recent_by_orcid_disp = df_followed_recent_by_orcid
    if (
        df_followed_recent_by_orcid_disp is not None
        and not df_followed_recent_by_orcid_disp.empty
    ):
        df_followed_recent_by_orcid_disp = df_followed_recent_by_orcid_disp.loc[:, cols_orcid].copy()

        # translate paper title and append to EN title
        if "title" in df_followed_recent_by_orcid_disp.columns:
            titles = df_followed_recent_by_orcid_disp["title"].astype(str).tolist()
            zh_map = {}
            if translator is not None:
                zh_map = translator.translate_titles(titles)

            df_followed_recent_by_orcid_disp["title"] = df_followed_recent_by_orcid_disp["title"].apply(
                lambda t: _escape_title_and_optional_zh(t, zh_map.get(str(t), None))
            )

        # translate df header
        df_followed_recent_by_orcid_disp = df_with_i18n_headers(
            df_followed_recent_by_orcid_disp
        )

    # Plot embeds (copy into assets if requested)
    cluster_plot_html = ""
    other_plots_html = []

    for p in plots:
        src = (p.path or "").strip()
        if not src:
            continue

        if copy_assets:
            src_abs = _to_abs_path(src)
            dst_abs = plots_dir / src_abs.name
            _copy_file(src_abs, dst_abs)
            src_rel = f"{assets_subdir}/plots/{dst_abs.name}"
        else:
            src_abs = _to_abs_path(src)
            src_rel = os.path.relpath(str(src_abs), start=str(out_dir_p.resolve()))

        iframe_html = _embed_iframe(p.title, src_rel, height="auto")

        if not cluster_plot_html and _is_cluster_plot(p.title, src):
            cluster_plot_html = iframe_html
        else:
            other_plots_html.append(iframe_html)

    cluster_picks_section_html = f"""
    <section class="card">
        <div class="card-header">
            <h2>
                <span lang="en">Top picks by cluster</span>
                <span lang="zh">各主题聚类中的精选论文</span>
            </h2>
            <div class="muted">
                <span lang="en">Top {int(cluster_top_picks_n)} per cluster (sorted by {cluster_sort_by})</span>
                <span lang="zh">每个聚类中的前 {int(cluster_top_picks_n)} 名（按 {cluster_sort_by} 排序）</span>
            </div>
        </div>
        <div class="muted legend">
            <span lang="en">
                <span class="top-score-title">Top 10% papers</span>
                by Crossref relevance score
            </span>
            <span lang="zh">
                <span class="top-score-title">评分前 10% 的论文</span>
                （基于 Crossref 相关性评分）
            </span>
        </div>
      {cluster_picks_html if cluster_picks_html else '<p><em><span lang="en">No cluster information available.</span><span lang="zh">无可用聚类信息。</span></em></p>'}
    </section>
    """

    plots_html = "".join(
        [
            cluster_plot_html,
            (
                cluster_picks_section_html if cluster_plot_html else ""
            ),  # only insert if a cluster plot available
            "".join(other_plots_html),
        ]
    )

    # Language toggle JS
    lang_js_href = ""
    if copy_assets:
        lang_js_href = _copy_js("./js/lang_toggle.js", assets_dir, assets_subdir)
    else:
        lang_js_href = "./js/lang_toggle.js"

    lang_js_tag = (
        f'<script src="{lang_js_href}" defer></script>' if lang_js_href else ""
    )

    # Build HTML
    html = f"""<!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>{report_title}</title>
        {css_link}
        {lang_js_tag}
    </head>
    <body>
        <div class="container">
            <header>
                <h1>{report_title}</h1>
                {subtitle_html}
                
                <div class="lang-switch">
                    <label class="sr-only" for="lang-select">
                        <span lang="en">Language</span>
                        <span lang="zh">语言</span>
                    </label>
                
                    <select id="lang-select" class="lang-select" aria-label="Language">
                        <option value="en">EN</option>
                        <option value="zh">中文</option>
                    </select>
                </div>
                
                <div class="muted">
                    <span lang="en">Generated by 
                        <a href="https://github.com/hqrrr/weekly-paper-report" target="_blank">weekly-paper-report</a>
                    </span>
                    <span lang="zh">由 
                        <a href="https://github.com/hqrrr/weekly-paper-report" target="_blank">weekly-paper-report</a>
                     生成</span>
                </div>
            </header>
        
        <!-- Key statistics -->
        <section class="stats">
            <div class="stat">
                <div class="label">
                    <span lang="en">Keyword search results</span>
                    <span lang="zh">关键词搜索结果</span>
                </div>
                <div class="value">{n_results}</div>
            </div>
            <div class="stat">
                <div class="label">
                    <span lang="en">Papers by followed authors (in keyword search results)</span>
                    <span lang="zh">已关注的作者发表的论文（在关键词搜索结果中）</span>
                </div>
                <div class="value">{n_followed_in}</div>
            </div>
            <div class="stat">
                <div class="label">
                    <span lang="en">Papers by followed authors (additional search)</span>
                    <span lang="zh">已关注的作者发表的论文（额外检索）</span>
                </div>
                <div class="value">{n_followed_orcid}</div>
            </div>
        </section>
        
        <!-- Query details -->
        <details class="details-card">
            <summary class="details-summary">
                <span class="details-title">
                    <span lang="en">Query details</span>
                    <span lang="zh">查询详情</span>
                </span>
                <span class="details-hint">
                    <span lang="en">Keywords & followed authors</span>
                    <span lang="zh">关键词 & 已关注作者</span>
                </span>
            </summary>
        
            <div class="details-body">
                <div class="details-grid">
                    <div class="details-block">
                        <h3>
                            <span lang="en">Keywords</span>
                            <span lang="zh">关键词</span>
                        </h3>
                        <!-- tag cloud -->
                        <div class="tag-cloud">
                            {keywords_tag_cloud_html}
                        </div>
                    </div>
                    <div class="details-block">
                        <h3>
                            <span lang="en">Followed authors</span>
                            <span lang="zh">已关注作者</span>
                        </h3>
                        <ul class="clean-list">
                            {followed_authors_list_html}
                        </ul>
                    </div>
                </div>
            </div>
        </details>
        
        <!-- Top picks from keyword results -->
        <div class="grid">
            <section class="card">
            <div class="card-header">
                <h2>
                    <span lang="en">Top picks from keyword search results <span class="pill">Top {int(top_picks_n)}</span></span>
                    <span lang="zh">关键词搜索结果中的精选论文 <span class="pill">前 {int(top_picks_n)} 名</span></span>
                </h2>
                <div class="muted">
                    <span lang="en">Sorted by Crossref relevance score (if available)</span>
                    <span lang="zh">根据 Crossref 相关性评分排序（如可用）</span>
                </div>
            </div>
            <div class="muted legend">
                <span lang="en">
                    <span class="top-score-title">Top 10% papers</span>
                    by Crossref relevance score
                </span>
                <span lang="zh">
                    <span class="top-score-title">评分前 10% 的论文</span>
                    （基于 Crossref 相关性评分）
                </span>
            </div>
            {_df_to_html_table(df_top, title=None, max_rows=int(top_picks_n), columns=list(df_top.columns) if not df_top.empty else None)}
            </section>
          
            <!-- Papers by followed authors (keyword results) -->
            <section class="card">
                <div class="card-header">
                    <h2>
                        <span lang="en">Papers by followed authors <span class="pill">Found in keyword search results</span></span>
                        <span lang="zh">已关注作者的论文 <span class="pill">出现在关键词搜索结果中</span></span>
                    </h2>
                    <div class="muted">
                        <span lang="en">Matched by ORCID first, then fallback name matching</span>
                        <span lang="zh">优先通过ORCID进行匹配，若失败则转为姓名匹配</span>
                    </div>
                </div>
                {_df_to_html_table(
                    df_followed_in_results_disp, 
                    title=None, 
                    max_rows=80, 
                    columns=list(df_followed_in_results_disp.columns) if df_followed_in_results_disp is not None and not df_followed_in_results_disp.empty else None,
                )}
            </section>
            
            <!-- Papers by followed authors (ORCID results) -->
            <section class="card">
                <div class="card-header">
                    <h2>
                        <span lang="en">Papers by followed authors <span class="pill">Found with ORCID or name</span></span>
                        <span lang="zh">已关注作者的论文 <span class="pill">通过 ORCID 或姓名找到</span></span>
                    </h2>
                    <div class="muted">
                        <span lang="en">Works deposited with ORCID in Crossref (filtered by created date)</span>
                        <span lang="zh">通过 ORCID 提交至 Crossref 的论文（按创建日期筛选）</span>
                    </div>
                </div>
                {_df_to_html_table(
                    df_followed_recent_by_orcid_disp, 
                    title=None, 
                    max_rows=120, 
                    columns=list(df_followed_recent_by_orcid_disp.columns) if df_followed_recent_by_orcid_disp is not None and not df_followed_recent_by_orcid_disp.empty else None,
                )}
            </section>
          
            <!-- Clustering & Publisher Analysis -->
            {plots_html}
          
        </div>
        </div>
    </body>
    </html>
    """

    out_path.write_text(html, encoding="utf-8")
    return out_path
