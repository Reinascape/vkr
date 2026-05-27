# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass

from kitsune.models.release import Release


@dataclass
class PaginationMeta:
    current_page: int
    last_page: int
    total: int

    @classmethod
    def from_dict(cls, data: dict) -> PaginationMeta:
        pagination = data.get('pagination', data)
        return cls(
            current_page=pagination.get('current_page', 1),
            last_page=pagination.get('total_pages', pagination.get('last_page', 1)),
            total=pagination.get('total', 0),
        )


@dataclass
class CatalogResponse:
    releases: list[Release]
    meta: PaginationMeta

    @classmethod
    def from_dict(cls, data: dict) -> CatalogResponse:
        releases = [Release.from_dict(r) for r in data.get('data', [])]
        meta = PaginationMeta.from_dict(data.get('meta', {}))
        return cls(releases=releases, meta=meta)
