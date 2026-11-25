"""Composition root: the only place that knows every concrete class.

Use cases receive their dependencies through the constructor (Dependency Injection),
so swapping pyairbnb, SQLite or an exporter never touches business code.
"""

from __future__ import annotations

from functools import cached_property
from typing import TextIO

from airbnb_tracker.application.ports import (
    CalendarGateway,
    Clock,
    DatasetExporter,
    ListingDetailsGateway,
    ListingSearchGateway,
    ReportExporter,
    ReviewGateway,
)
from airbnb_tracker.application.use_cases import (
    BuildMarketReportUseCase,
    EnrichGateways,
    EnrichListingsUseCase,
    EnrichRepositories,
    ExportDatasetUseCase,
    ExportSources,
    ScanMarketUseCase,
)
from airbnb_tracker.domain.specifications import (
    AnyListing,
    ExcludeIds,
    HasRoomType,
    ListingSpecification,
    MaxBedrooms,
    MinBedrooms,
    MinRating,
)
from airbnb_tracker.infrastructure.airbnb import (
    DefaultPyAirbnbClient,
    PyAirbnbCalendarGateway,
    PyAirbnbDetailsGateway,
    PyAirbnbReviewGateway,
    PyAirbnbSearchGateway,
)
from airbnb_tracker.infrastructure.clock import SystemClock
from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.infrastructure.exporters import ConsoleReportExporter, DatasetExporterFactory
from airbnb_tracker.infrastructure.persistence import InMemoryRepository, SqliteRepository
from airbnb_tracker.infrastructure.resilience import (
    RateLimiter,
    RequestGuard,
    ResilientCalendarGateway,
    ResilientDetailsGateway,
    ResilientReviewGateway,
    ResilientSearchGateway,
    RetryPolicy,
)


class Container:
    def __init__(
        self,
        settings: AppSettings,
        *,
        clock: Clock | None = None,
        repository: SqliteRepository | InMemoryRepository | None = None,
        search_gateway: ListingSearchGateway | None = None,
        details_gateway: ListingDetailsGateway | None = None,
        calendar_gateway: CalendarGateway | None = None,
        review_gateway: ReviewGateway | None = None,
        stream: TextIO | None = None,
    ) -> None:
        self.settings = settings
        self.clock: Clock = clock or SystemClock()
        self._repository = repository
        self._search_gateway = search_gateway
        self._details_gateway = details_gateway
        self._calendar_gateway = calendar_gateway
        self._review_gateway = review_gateway
        self._stream = stream

    # --- infrastructure -------------------------------------------------------------
    @cached_property
    def repository(self) -> SqliteRepository | InMemoryRepository:
        return self._repository or SqliteRepository(self.settings.storage.database_path)

    @cached_property
    def _client(self) -> DefaultPyAirbnbClient:
        return DefaultPyAirbnbClient()

    @cached_property
    def _guard(self) -> RequestGuard:
        scraping = self.settings.scraping
        return RequestGuard(
            RateLimiter(scraping.min_delay_seconds),
            RetryPolicy(scraping.max_attempts, scraping.backoff_seconds),
        )

    @cached_property
    def search_gateway(self) -> ListingSearchGateway:
        if self._search_gateway:
            return self._search_gateway
        proxy = self.settings.scraping.proxy_url
        inner = PyAirbnbSearchGateway(
            self.clock,
            proxy_url=proxy,
            complete_coverage=self.settings.search.complete_coverage,
            client=self._client,
        )
        return ResilientSearchGateway(inner, self._guard)

    @cached_property
    def details_gateway(self) -> ListingDetailsGateway:
        if self._details_gateway:
            return self._details_gateway
        proxy = self.settings.scraping.proxy_url
        inner = PyAirbnbDetailsGateway(proxy_url=proxy, client=self._client)
        return ResilientDetailsGateway(inner, self._guard)

    @cached_property
    def calendar_gateway(self) -> CalendarGateway:
        if self._calendar_gateway:
            return self._calendar_gateway
        proxy = self.settings.scraping.proxy_url
        inner = PyAirbnbCalendarGateway(proxy_url=proxy, client=self._client)
        return ResilientCalendarGateway(inner, self._guard)

    @cached_property
    def review_gateway(self) -> ReviewGateway:
        if self._review_gateway:
            return self._review_gateway
        proxy = self.settings.scraping.proxy_url
        inner = PyAirbnbReviewGateway(proxy_url=proxy, client=self._client)
        return ResilientReviewGateway(inner, self._guard)

    def report_exporter(self) -> ReportExporter:
        return ConsoleReportExporter(self._stream)

    def dataset_exporter(self, name: str) -> DatasetExporter:
        return DatasetExporterFactory(self.settings.storage.exports_dir).create(name)

    # --- domain policy built from configuration -------------------------------------
    def competitor_filter(self) -> ListingSpecification:
        """Rules applied after searching (Airbnb's own filters run server-side)."""
        search = self.settings.search
        spec: ListingSpecification = AnyListing()
        if search.filters.room_type:
            spec = spec & HasRoomType(search.filters.room_type)
        if search.filters.min_bedrooms is not None:
            spec = spec & MinBedrooms(search.filters.min_bedrooms)
        if search.max_bedrooms is not None:
            spec = spec & MaxBedrooms(search.max_bedrooms)
        if search.min_rating is not None:
            spec = spec & MinRating(search.min_rating)
        if search.exclude_ids:
            spec = spec & ExcludeIds(search.exclude_ids)
        return spec

    # --- use cases ------------------------------------------------------------------
    @cached_property
    def scan_market(self) -> ScanMarketUseCase:
        repo = self.repository
        return ScanMarketUseCase(self.search_gateway, repo, repo, self.clock)

    @cached_property
    def enrich_listings(self) -> EnrichListingsUseCase:
        repo = self.repository
        return EnrichListingsUseCase(
            EnrichGateways(self.details_gateway, self.calendar_gateway, self.review_gateway),
            EnrichRepositories(snapshots=repo, profiles=repo, calendars=repo, reviews=repo),
            self.clock,
        )

    @cached_property
    def build_report(self) -> BuildMarketReportUseCase:
        return BuildMarketReportUseCase(self.repository, self.repository, self.clock)

    @cached_property
    def export_dataset(self) -> ExportDatasetUseCase:
        repo = self.repository
        return ExportDatasetUseCase(
            ExportSources(snapshots=repo, profiles=repo, calendars=repo, reviews=repo, runs=repo)
        )
