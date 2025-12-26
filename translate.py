"""
DeepL title translation utilities for weekly-paper-report.
Languages supported: https://developers.deepl.com/docs/getting-started/supported-languages
"""

from dataclasses import dataclass, field
from typing import Iterable, Dict, List, Optional

import deepl


def _safe_str(x) -> str:
    if x is None:
        return ""
    try:
        return str(x)
    except Exception:
        return ""


@dataclass
class DeepLTitleTranslator:
    auth_key: str
    target_lang: str = "ZH-HANS"  # Chinese (simplified)
    max_batch_size: int = 50
    _client: object | None = field(default=None, init=False, repr=False)
    _cache: Dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _disabled: bool = field(default=False, init=False, repr=False)

    def _ensure_client(self) -> None:
        if self._disabled:
            return
        if self._client is not None:
            return

        # disable if no API key found
        key = (self.auth_key or "").strip()
        if not key:
            self._disabled = True
            return

        try:
            self._client = deepl.DeepLClient(key)
        except Exception:
            self._disabled = True

    @property
    def enabled(self) -> bool:
        self._ensure_client()
        return (not self._disabled) and (self._client is not None)

    def translate_titles(self, titles: Iterable[str]) -> Dict[str, str]:
        """
        Translate a collection of titles.

        Returns:
            dict mapping original_title -> translated_title
        """
        self._ensure_client()
        if not self.enabled:
            return {}

        cleaned: List[str] = []
        for t in titles:
            s = _safe_str(t).strip()
            if s:
                cleaned.append(s)

        if not cleaned:
            return {}

        # start with cached results
        result: Dict[str, str] = {
            s: self._cache[s] for s in cleaned if s in self._cache
        }

        # find titles not in cache
        uniq: List[str] = []
        for s in cleaned:
            if s in self._cache:
                continue
            if s not in uniq:
                uniq.append(s)

        # translate the missing ones
        out: Dict[str, str] = {}
        client = self._client  # type: ignore[assignment]

        try:
            for i in range(0, len(uniq), int(self.max_batch_size)):
                batch = uniq[i : i + int(self.max_batch_size)]
                res = client.translate_text(
                    batch,
                    target_lang=self.target_lang,
                )

                if isinstance(res, list):
                    translated = [getattr(x, "text", "") for x in res]
                else:
                    translated = [getattr(res, "text", "")]

                for src, zh in zip(batch, translated, strict=False):
                    zh = _safe_str(zh).strip()
                    if zh:
                        out[src] = zh
        except Exception:
            # Any API / quota / network error -> do nothing (keep English only)
            out = out or {}

        # update cache and result
        for k, v in out.items():
            if v:
                self._cache[k] = v
                result[k] = v

        return result
