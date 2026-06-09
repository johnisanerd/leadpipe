import asyncio
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import DATABASE_URL, OPENAI_API_KEY
from app.db.connection import check_connection
from app.db.enrichment import list_sites, run_enrichment_batch, run_enrichment_for_site
from app.db.forms import list_site_forms, run_form_collection_batch, run_form_collection_for_site
from app.db.outreach import (
    list_outreach,
    run_form_fill_for_site,
    run_generate_batch,
    run_generate_for_site,
    run_qualify_batch,
    run_qualify_for_site,
    run_submit_for_site,
)
from app.services.site_pipeline import run_site_outreach_pipeline
from app.db.repository import (
    save_google_search_results,
    save_yandex_maps_search_results,
    save_yandex_search_results,
)
from app.models import (
    CitiesListResponse,
    CityCatalogItem,
    EnrichSiteResult,
    EnrichSitesRequest,
    EnrichSitesResponse,
    CollectFormsRequest,
    CollectFormsResponse,
    CollectFormsSiteResult,
    CollectSiteFormsResult,
    FormFillFieldItem,
    FormFillRequest,
    FormFillResponse,
    PipelineBatchRequest,
    PipelineStepResult,
    GenerateOutreachRequest,
    GenerateOutreachResponse,
    GenerateOutreachSiteResult,
    OutreachItem,
    OutreachListResponse,
    OutreachOfferContext,
    QualifyResult,
    QualifySiteResult,
    QualifySitesRequest,
    QualifySitesResponse,
    SitePipelineRequest,
    SitePipelineResponse,
    SubmitOutreachRequest,
    SubmitOutreachResponse,
    SiteFormItem,
    SiteFormsListResponse,
    SearchBatchRequest,
    SearchBatchResponse,
    SearchBatchCityResult,
    SearchRequest,
    SearchResponse,
    SiteItem,
    SitesListResponse,
)
from app.scraper.exceptions import ScrapeBlockedError, ScrapeProxyExhaustedError
from app.scraper.google import scrape_google_serp
from app.scraper.yandex import scrape_yandex_serp
from app.scraper.yandex_maps import scrape_yandex_maps
from app.services.batch_search import (
    run_google_batch_search,
    run_yandex_batch_search,
    run_yandex_maps_batch_search,
)
from app.utils.cities import load_all_cities

app = FastAPI(
    title="Playwright SERP Scraper",
    description="Google + Yandex SERP + Яндекс.Карты → Supabase → обогащение сайтов.",
    version="0.8.0",
)


def _require_db() -> None:
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан в .env")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "database_configured": bool(DATABASE_URL),
        "llm_configured": bool(OPENAI_API_KEY),
    }


@app.get("/health/db")
async def health_db() -> dict[str, str]:
    _require_db()
    try:
        await asyncio.to_thread(check_connection)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"БД недоступна: {exc}") from exc
    return {"status": "ok", "database": "connected"}


@app.post("/search/google", response_model=SearchResponse)
async def search_google(payload: SearchRequest) -> SearchResponse:
    try:
        search_phrase, results, meta = await asyncio.to_thread(
            scrape_google_serp,
            payload.query,
            payload.city,
            payload.max_pages,
            headless=payload.headless,
        )
    except (RuntimeError, ScrapeBlockedError, ScrapeProxyExhaustedError) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка Playwright: {exc}") from exc

    pages_scraped = max((item.page for item in results), default=0)
    response = SearchResponse(
        search_query=search_phrase,
        city=payload.city,
        total_results=len(results),
        pages_scraped=pages_scraped,
        results=results,
        scrape_source=meta.scrape_source,
        proxy_server=meta.proxy_server,
    )

    if payload.save_to_db:
        _require_db()
        try:
            saved = await asyncio.to_thread(
                save_google_search_results,
                query=payload.query,
                city=payload.city,
                search_phrase=search_phrase,
                max_pages=payload.max_pages,
                pages_scraped=pages_scraped,
                results=results,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка сохранения в БД: {exc}") from exc

        response.search_query_id = saved["search_query_id"]
        response.search_run_id = saved["search_run_id"]
        response.saved_count = saved["saved_count"]
        response.new_sites_count = saved["new_sites_count"]
        response.db_saved = True

    return response


@app.post("/search/yandex", response_model=SearchResponse)
async def search_yandex(payload: SearchRequest) -> SearchResponse:
    try:
        search_phrase, results, meta = await asyncio.to_thread(
            scrape_yandex_serp,
            payload.query,
            payload.city,
            payload.max_pages,
            headless=payload.headless,
        )
    except (RuntimeError, ScrapeBlockedError, ScrapeProxyExhaustedError) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка Playwright: {exc}") from exc

    pages_scraped = max((item.page for item in results), default=0)
    response = SearchResponse(
        search_query=search_phrase,
        city=payload.city,
        total_results=len(results),
        pages_scraped=pages_scraped,
        results=results,
        scrape_source=meta.scrape_source,
        proxy_server=meta.proxy_server,
    )

    if payload.save_to_db:
        _require_db()
        try:
            saved = await asyncio.to_thread(
                save_yandex_search_results,
                query=payload.query,
                city=payload.city,
                search_phrase=search_phrase,
                max_pages=payload.max_pages,
                pages_scraped=pages_scraped,
                results=results,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка сохранения в БД: {exc}") from exc

        response.search_query_id = saved["search_query_id"]
        response.search_run_id = saved["search_run_id"]
        response.saved_count = saved["saved_count"]
        response.new_sites_count = saved["new_sites_count"]
        response.db_saved = True

    return response


@app.post("/search/yandex-maps", response_model=SearchResponse)
async def search_yandex_maps(payload: SearchRequest) -> SearchResponse:
    try:
        search_phrase, results, meta = await asyncio.to_thread(
            scrape_yandex_maps,
            payload.query,
            payload.city,
            payload.max_pages,
            headless=payload.headless,
        )
    except (RuntimeError, ScrapeBlockedError, ScrapeProxyExhaustedError) as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка Playwright: {exc}") from exc

    pages_scraped = payload.max_pages if results else 0
    response = SearchResponse(
        search_query=search_phrase,
        city=payload.city,
        total_results=len(results),
        pages_scraped=pages_scraped,
        results=results,
        scrape_source=meta.scrape_source,
        proxy_server=meta.proxy_server,
    )

    if payload.save_to_db:
        _require_db()
        try:
            saved = await asyncio.to_thread(
                save_yandex_maps_search_results,
                query=payload.query,
                city=payload.city,
                search_phrase=search_phrase,
                max_pages=payload.max_pages,
                pages_scraped=pages_scraped,
                results=results,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка сохранения в БД: {exc}") from exc

        response.search_query_id = saved["search_query_id"]
        response.search_run_id = saved["search_run_id"]
        response.saved_count = saved["saved_count"]
        response.new_sites_count = saved["new_sites_count"]
        response.db_saved = True

    return response


@app.get("/cities", response_model=CitiesListResponse)
async def get_cities() -> CitiesListResponse:
    try:
        items = await asyncio.to_thread(load_all_cities)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения справочника городов: {exc}") from exc
    return CitiesListResponse(
        total=len(items),
        items=[CityCatalogItem(**item) for item in items],
    )


@app.post("/search/google/batch", response_model=SearchBatchResponse)
async def search_google_batch(payload: SearchBatchRequest) -> SearchBatchResponse:
    if payload.save_to_db:
        _require_db()
    try:
        result = await asyncio.to_thread(
            run_google_batch_search,
            query=payload.query,
            cities=payload.cities,
            use_all_cities=payload.use_all_cities,
            max_pages=payload.max_pages,
            headless=payload.headless,
            save_to_db=payload.save_to_db,
            stop_on_block=payload.stop_on_block,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка batch-поиска: {exc}") from exc

    return SearchBatchResponse(
        **{
            **result,
            "cities": [SearchBatchCityResult(**city) for city in result["cities"]],
        }
    )


@app.post("/search/yandex/batch", response_model=SearchBatchResponse)
async def search_yandex_batch(payload: SearchBatchRequest) -> SearchBatchResponse:
    if payload.save_to_db:
        _require_db()
    try:
        result = await asyncio.to_thread(
            run_yandex_batch_search,
            query=payload.query,
            cities=payload.cities,
            use_all_cities=payload.use_all_cities,
            max_pages=payload.max_pages,
            headless=payload.headless,
            save_to_db=payload.save_to_db,
            stop_on_block=payload.stop_on_block,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка batch-поиска Yandex: {exc}") from exc

    return SearchBatchResponse(
        **{
            **result,
            "cities": [SearchBatchCityResult(**city) for city in result["cities"]],
        }
    )


@app.post("/search/yandex-maps/batch", response_model=SearchBatchResponse)
async def search_yandex_maps_batch(payload: SearchBatchRequest) -> SearchBatchResponse:
    if payload.save_to_db:
        _require_db()
    try:
        result = await asyncio.to_thread(
            run_yandex_maps_batch_search,
            query=payload.query,
            cities=payload.cities,
            use_all_cities=payload.use_all_cities,
            max_pages=payload.max_pages,
            headless=payload.headless,
            save_to_db=payload.save_to_db,
            stop_on_block=payload.stop_on_block,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка batch Яндекс.Карт: {exc}") from exc

    return SearchBatchResponse(
        **{
            **result,
            "cities": [SearchBatchCityResult(**city) for city in result["cities"]],
        }
    )


@app.post("/enrich/sites", response_model=EnrichSitesResponse)
async def enrich_sites_endpoint(payload: EnrichSitesRequest) -> EnrichSitesResponse:
    _require_db()
    try:
        result = await asyncio.to_thread(
            run_enrichment_batch,
            limit=payload.limit,
            headless=payload.headless,
            retry_failed=payload.retry_failed,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка enrichment: {exc}") from exc

    return EnrichSitesResponse(
        processed=result["processed"],
        succeeded=result["succeeded"],
        failed=result["failed"],
        results=[EnrichSiteResult(**item) for item in result["results"]],
    )


@app.post("/enrich/site/{site_id}", response_model=EnrichSiteResult)
async def enrich_one_site(
    site_id: UUID,
    headless: bool = Query(default=True, description="False — показать браузер"),
) -> EnrichSiteResult:
    _require_db()
    try:
        item = await asyncio.to_thread(run_enrichment_for_site, site_id, headless=headless)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка enrichment: {exc}") from exc
    return EnrichSiteResult(**item)


@app.get("/sites", response_model=SitesListResponse)
async def get_sites(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    scrape_status: str | None = Query(default=None, description="pending|running|done|failed"),
) -> SitesListResponse:
    _require_db()
    try:
        items = await asyncio.to_thread(
            list_sites,
            limit=limit,
            offset=offset,
            scrape_status=scrape_status,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения БД: {exc}") from exc

    return SitesListResponse(
        total_returned=len(items),
        items=[SiteItem(**item) for item in items],
    )

@app.post("/forms/collect", response_model=CollectFormsResponse)
async def collect_forms_batch(payload: CollectFormsRequest) -> CollectFormsResponse:
    _require_db()
    try:
        result = await asyncio.to_thread(
            run_form_collection_batch,
            limit=payload.limit,
            headless=payload.headless,
            only_without_forms=payload.only_without_forms,
            only_with_contact_flag=payload.only_with_contact_flag,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка сбора форм: {exc}") from exc

    return CollectFormsResponse(
        processed=result["processed"],
        forms_found=result["forms_found"],
        results=[CollectFormsSiteResult(**item) for item in result["results"]],
    )


@app.post("/forms/collect/site/{site_id}", response_model=CollectSiteFormsResult)
async def collect_forms_for_one_site(
    site_id: UUID,
    headless: bool = Query(default=True, description="False — показать браузер"),
) -> CollectSiteFormsResult:
    _require_db()
    try:
        item = await asyncio.to_thread(run_form_collection_for_site, site_id, headless=headless)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка сбора форм: {exc}") from exc

    return CollectSiteFormsResult(
        site_id=item["site_id"],
        url=item["url"],
        domain=item["domain"],
        status=item["status"],
        forms_found=len(item.get("forms", [])),
        forms_saved=item.get("forms_saved", 0),
        forms=item.get("forms", []),
        error_message=item.get("error_message"),
    )


@app.get("/forms", response_model=SiteFormsListResponse)
async def get_forms(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    site_id: UUID | None = Query(default=None),
    submittable_only: bool = Query(default=False),
) -> SiteFormsListResponse:
    _require_db()
    try:
        items = await asyncio.to_thread(
            list_site_forms,
            limit=limit,
            offset=offset,
            site_id=site_id,
            submittable_only=submittable_only,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения форм: {exc}") from exc

    return SiteFormsListResponse(
        total_returned=len(items),
        items=[SiteFormItem(**item) for item in items],
    )


@app.post("/outreach/qualify", response_model=QualifySitesResponse)
async def qualify_sites_batch(payload: QualifySitesRequest) -> QualifySitesResponse:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        result = await asyncio.to_thread(
            run_qualify_batch,
            limit=payload.limit,
            target_query=payload.target_query,
            target_description=payload.target_description,
            only_without_qualify=payload.only_without_qualify,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка квалификации: {exc}") from exc

    return QualifySitesResponse(
        processed=result["processed"],
        fit_count=result["fit_count"],
        results=[
            QualifySiteResult(
                site_id=item["site_id"],
                domain=item["domain"],
                status=item["status"],
                qualify=QualifyResult(**item["qualify"]) if item.get("qualify") else None,
                error_message=item.get("error_message"),
            )
            for item in result["results"]
        ],
    )


@app.post("/outreach/qualify/site/{site_id}", response_model=QualifySiteResult)
async def qualify_one_site(
    site_id: UUID,
    target_query: str = Query(default="стоматология"),
    target_description: str = Query(default="стоматологическая клиника"),
) -> QualifySiteResult:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        item = await asyncio.to_thread(
            run_qualify_for_site,
            site_id,
            target_query=target_query,
            target_description=target_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка квалификации: {exc}") from exc

    return QualifySiteResult(
        site_id=item["site_id"],
        domain=item["domain"],
        status=item["status"],
        qualify=QualifyResult(**item["qualify"]) if item.get("qualify") else None,
        error_message=item.get("error_message"),
    )


@app.post("/outreach/generate", response_model=GenerateOutreachResponse)
async def generate_outreach_batch(payload: GenerateOutreachRequest) -> GenerateOutreachResponse:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        result = await asyncio.to_thread(
            run_generate_batch,
            limit=payload.limit,
            offer_description=payload.offer_description,
            sender_name=payload.sender_name,
            sender_company=payload.sender_company,
            sender_phone=payload.sender_phone,
            sender_email=payload.sender_email,
            call_to_action=payload.call_to_action,
            only_without_outreach=payload.only_without_outreach,
            skip_unqualified=payload.skip_unqualified,
            min_qualify_score=payload.min_qualify_score,
            auto_qualify=payload.auto_qualify,
            target_query=payload.target_query,
            target_description=payload.target_description,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка генерации outreach: {exc}") from exc

    return GenerateOutreachResponse(
        processed=result["processed"],
        generated=result["generated"],
        results=[GenerateOutreachSiteResult(**item) for item in result["results"]],
    )


@app.post("/outreach/generate/site/{site_id}", response_model=GenerateOutreachSiteResult)
async def generate_outreach_for_one_site(
    site_id: UUID,
    payload: OutreachOfferContext,
    auto_qualify: bool = Query(default=True),
    skip_unqualified: bool = Query(default=False),
    target_query: str = Query(default="стоматология"),
    target_description: str = Query(default="стоматологическая клиника"),
) -> GenerateOutreachSiteResult:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        item = await asyncio.to_thread(
            run_generate_for_site,
            site_id,
            offer_description=payload.offer_description,
            sender_name=payload.sender_name,
            sender_company=payload.sender_company,
            call_to_action=payload.call_to_action,
            auto_qualify=auto_qualify,
            skip_unqualified=skip_unqualified,
            target_query=target_query,
            target_description=target_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка генерации outreach: {exc}") from exc

    return GenerateOutreachSiteResult(**item)


@app.get("/outreach", response_model=OutreachListResponse)
async def get_outreach(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    site_id: UUID | None = Query(default=None),
    sent_status: str | None = Query(default=None),
) -> OutreachListResponse:
    _require_db()
    try:
        items = await asyncio.to_thread(
            list_outreach,
            limit=limit,
            offset=offset,
            site_id=site_id,
            sent_status=sent_status,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения outreach: {exc}") from exc

    return OutreachListResponse(
        total_returned=len(items),
        items=[OutreachItem(**item) for item in items],
    )


@app.post("/outreach/form-fill/site/{site_id}", response_model=FormFillResponse)
async def build_outreach_form_fill(
    site_id: UUID,
    payload: FormFillRequest,
) -> FormFillResponse:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        outreach_id = UUID(payload.outreach_id) if payload.outreach_id else None
        item = await asyncio.to_thread(
            run_form_fill_for_site,
            site_id,
            sender_name=payload.sender_name,
            sender_phone=payload.sender_phone,
            sender_email=payload.sender_email,
            outreach_id=outreach_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка подготовки формы: {exc}") from exc

    return FormFillResponse(
        **item,
        fields=[FormFillFieldItem(**field) for field in item["fields"]],
    )


@app.post("/outreach/submit/site/{site_id}", response_model=SubmitOutreachResponse)
async def submit_outreach_form(
    site_id: UUID,
    payload: SubmitOutreachRequest,
) -> SubmitOutreachResponse:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        outreach_id = UUID(payload.outreach_id) if payload.outreach_id else None
        item = await asyncio.to_thread(
            run_submit_for_site,
            site_id,
            sender_name=payload.sender_name,
            sender_phone=payload.sender_phone,
            sender_email=payload.sender_email,
            outreach_id=outreach_id,
            headless=payload.headless,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка отправки формы: {exc}") from exc

    return SubmitOutreachResponse(
        site_id=item["site_id"],
        domain=item["domain"],
        outreach_id=item["outreach_id"],
        form_id=item["form_id"],
        page_url=item["page_url"],
        form_selector=item["form_selector"],
        submit_selector=item.get("submit_selector"),
        dry_run=item.get("dry_run", True),
        submitted=item.get("submitted", False),
        fields_filled=item.get("fields_filled", 0),
        scrape_source=item.get("scrape_source"),
        proxy_server=item.get("proxy_server"),
        fields=[FormFillFieldItem(**field) for field in item.get("fields", [])],
    )


@app.post("/pipeline/site/{site_id}", response_model=SitePipelineResponse)
async def run_pipeline_for_site(
    site_id: UUID,
    payload: SitePipelineRequest,
) -> SitePipelineResponse:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")
    try:
        item = await asyncio.to_thread(
            run_site_outreach_pipeline,
            site_id,
            headless=payload.headless,
            skip_enrich_if_done=payload.skip_enrich_if_done,
            offer_description=payload.offer_description,
            sender_name=payload.sender_name,
            sender_company=payload.sender_company,
            sender_phone=payload.sender_phone,
            sender_email=payload.sender_email,
            call_to_action=payload.call_to_action,
            auto_qualify=payload.auto_qualify,
            skip_unqualified=payload.skip_unqualified,
            target_query=payload.target_query,
            target_description=payload.target_description,
            dry_run_submit=payload.dry_run_submit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка pipeline: {exc}") from exc

    return SitePipelineResponse(
        site_id=item["site_id"],
        domain=item["domain"],
        status=item["status"],
        steps=[PipelineStepResult(**step) for step in item.get("steps", [])],
        outreach_id=item.get("outreach_id"),
        error_message=item.get("error_message"),
        submit=item.get("submit"),
    )


@app.post("/pipeline/batch", response_model=list[SitePipelineResponse])
async def run_pipeline_batch(payload: PipelineBatchRequest) -> list[SitePipelineResponse]:
    _require_db()
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY не задан в .env")

    if payload.search_query and payload.search_cities:
        from app.services.batch_search import run_google_batch_search

        try:
            await asyncio.to_thread(
                run_google_batch_search,
                query=payload.search_query,
                cities=payload.search_cities,
                max_pages=payload.search_max_pages,
                headless=payload.headless,
                save_to_db=True,
                stop_on_block=False,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Ошибка поиска: {exc}") from exc

    try:
        sites = await asyncio.to_thread(
            list_sites,
            limit=payload.sites_limit,
            scrape_status=payload.scrape_status,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения сайтов: {exc}") from exc

    results: list[SitePipelineResponse] = []
    for site in sites:
        try:
            item = await asyncio.to_thread(
                run_site_outreach_pipeline,
                UUID(site["id"]),
                headless=payload.headless,
                skip_enrich_if_done=payload.skip_enrich_if_done,
                offer_description=payload.offer_description,
                sender_name=payload.sender_name,
                sender_company=payload.sender_company,
                sender_phone=payload.sender_phone,
                sender_email=payload.sender_email,
                call_to_action=payload.call_to_action,
                auto_qualify=payload.auto_qualify,
                skip_unqualified=payload.skip_unqualified,
                target_query=payload.target_query,
                target_description=payload.target_description,
                dry_run_submit=payload.dry_run_submit,
            )
            results.append(
                SitePipelineResponse(
                    site_id=item["site_id"],
                    domain=item["domain"],
                    status=item["status"],
                    steps=[PipelineStepResult(**step) for step in item.get("steps", [])],
                    outreach_id=item.get("outreach_id"),
                    error_message=item.get("error_message"),
                    submit=item.get("submit"),
                )
            )
        except Exception as exc:
            results.append(
                SitePipelineResponse(
                    site_id=site["id"],
                    domain=site["domain"],
                    status="failed",
                    steps=[],
                    error_message=str(exc),
                )
            )

    return results

