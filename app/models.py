from pydantic import BaseModel, Field, model_validator


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, examples=["стоматология"])
    city: str = Field(..., min_length=1, examples=["Москва"])
    max_pages: int = Field(default=3, ge=1, le=10, description="Сколько страниц выдачи обойти")
    headless: bool = Field(
        default=False,
        description="False — показать окно Chrome (удобно при CAPTCHA)",
    )
    save_to_db: bool = Field(
        default=True,
        description="Сохранить результат в Supabase (PostgreSQL)",
    )


class SearchResultItem(BaseModel):
    position: int
    title: str
    url: str
    snippet: str | None = None
    page: int


class SearchResponse(BaseModel):
    search_query: str
    city: str
    total_results: int
    pages_scraped: int
    results: list[SearchResultItem]
    search_query_id: str | None = None
    search_run_id: str | None = None
    saved_count: int | None = None
    new_sites_count: int | None = None
    db_saved: bool = False
    scrape_source: str | None = Field(
        default=None,
        description="playwright или apify",
    )
    proxy_server: str | None = Field(
        default=None,
        description="direct или адрес прокси, через который прошёл запрос",
    )


class EnrichSitesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="Сколько pending-сайтов обработать")
    headless: bool = Field(default=True, description="False — показать браузер")
    retry_failed: bool = Field(default=True, description="Повторно обрабатывать failed")


class EnrichSiteResult(BaseModel):
    site_id: str
    url: str
    domain: str
    status: str
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    messengers: dict[str, str] = Field(default_factory=dict)
    has_contact_form: bool = False
    has_feedback_form: bool = False
    has_online_widget: bool = False
    has_order_button: bool = False
    page_title: str | None = None
    meta_description: str | None = None
    error_message: str | None = None


class EnrichSitesResponse(BaseModel):
    processed: int
    succeeded: int
    failed: int
    results: list[EnrichSiteResult]


class SiteItem(BaseModel):
    id: str
    url: str
    domain: str
    title: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    scrape_status: str
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    messengers: dict[str, str] = Field(default_factory=dict)
    has_contact_form: bool = False
    has_feedback_form: bool = False
    has_online_widget: bool = False
    has_order_button: bool = False
    scraped_at: str | None = None


class SitesListResponse(BaseModel):
    total_returned: int
    items: list[SiteItem]


class CityCatalogItem(BaseModel):
    name: str
    region: str


class CitiesListResponse(BaseModel):
    total: int
    items: list[CityCatalogItem]


class SearchBatchRequest(BaseModel):
    query: str = Field(..., min_length=1, examples=["стоматология"])
    cities: list[str] | None = Field(
        default=None,
        examples=[["Москва", "Казань", "Самара"]],
        description="Список городов из справочника",
    )
    use_all_cities: bool = Field(
        default=False,
        description="True — все города из data/cities.json",
    )
    max_pages: int = Field(default=2, ge=1, le=10)
    headless: bool = Field(default=True)
    save_to_db: bool = Field(default=True)
    stop_on_block: bool = Field(
        default=False,
        description="True — остановить batch при CAPTCHA/блокировке",
    )

    @model_validator(mode="after")
    def validate_city_source(self) -> "SearchBatchRequest":
        if not self.use_all_cities and not self.cities:
            raise ValueError("Укажите cities или установите use_all_cities=true")
        return self


class SearchBatchCityResult(BaseModel):
    city: str
    search_phrase: str
    status: str
    total_results: int
    pages_scraped: int
    error_message: str | None = None
    search_run_id: str | None = None
    saved_count: int | None = None
    new_sites_count: int | None = None
    scrape_source: str | None = None
    proxy_server: str | None = None
    results: list[SearchResultItem] = Field(default_factory=list)


class SearchBatchResponse(BaseModel):
    source: str | None = None
    query: str
    cities_requested: int
    cities_processed: int
    completed: int
    blocked: int
    failed: int
    total_results: int
    total_saved: int
    total_new_sites: int
    db_saved: bool
    cities: list[SearchBatchCityResult]


class FormFieldItem(BaseModel):
    name: str | None = None
    type: str
    selector: str
    label: str | None = None
    placeholder: str | None = None
    purpose: str = "unknown"
    required: bool = False


class SiteFormItem(BaseModel):
    id: str
    site_id: str
    site_url: str
    domain: str
    page_url: str
    form_index: int
    form_action: str | None = None
    form_method: str
    form_selector: str
    submit_selector: str | None = None
    fields: list[FormFieldItem] = Field(default_factory=list)
    form_type: str
    form_score: int
    has_captcha: bool
    in_iframe: bool
    is_submittable: bool
    scrape_status: str
    error_message: str | None = None
    discovered_at: str | None = None


class SiteFormsListResponse(BaseModel):
    total_returned: int
    items: list[SiteFormItem]


class CollectFormsRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50, description="Сколько сайтов обработать")
    headless: bool = Field(default=True, description="False — показать браузер")
    only_without_forms: bool = Field(
        default=True,
        description="True — только сайты без сохранённых форм",
    )
    only_with_contact_flag: bool = Field(
        default=False,
        description="True — только сайты с has_contact_form=true",
    )


class CollectFormsSiteResult(BaseModel):
    site_id: str
    url: str
    domain: str
    status: str
    forms_found: int
    forms_saved: int
    error_message: str | None = None


class CollectFormsResponse(BaseModel):
    processed: int
    forms_found: int
    results: list[CollectFormsSiteResult]


class CollectSiteFormsResult(BaseModel):
    site_id: str
    url: str
    domain: str
    status: str
    forms_found: int
    forms_saved: int
    forms: list[dict] = Field(default_factory=list)
    error_message: str | None = None


class QualifyResult(BaseModel):
    is_fit: bool
    score: int
    business_type: str
    business_name: str
    city_guess: str = ""
    services: list[str] = Field(default_factory=list)
    reason: str
    red_flags: list[str] = Field(default_factory=list)


class QualifySiteResult(BaseModel):
    site_id: str
    domain: str
    status: str
    qualify: QualifyResult | None = None
    error_message: str | None = None


class QualifySitesRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    target_query: str = Field(default="стоматология", description="Целевая ниша")
    target_description: str = Field(
        default="стоматологическая клиника",
        description="Кого считаем подходящим лидом",
    )
    only_without_qualify: bool = Field(
        default=True,
        description="True — только сайты без llm_qualified_at",
    )


class QualifySitesResponse(BaseModel):
    processed: int
    fit_count: int
    results: list[QualifySiteResult]


class OutreachOfferContext(BaseModel):
    offer_description: str = Field(
        ...,
        min_length=10,
        examples=["Помогаем стоматологиям получать больше заявок через SEO и контекст."],
    )
    sender_name: str = Field(..., min_length=1, examples=["Иван Петров"])
    sender_company: str = Field(..., min_length=1, examples=["Dental Marketing"])
    sender_phone: str = Field(default="", examples=["+7 (999) 123-45-67"])
    sender_email: str = Field(default="", examples=["ivan@example.com"])
    call_to_action: str = Field(
        default="Ответьте на сообщение — обсудим детали.",
        description="Финальная фраза с призывом",
    )


class GenerateOutreachRequest(OutreachOfferContext):
    limit: int = Field(default=10, ge=1, le=50)
    only_without_outreach: bool = Field(
        default=True,
        description="True — пропускать сайты с pending/sent outreach",
    )
    skip_unqualified: bool = Field(
        default=True,
        description="True — не генерировать для сайтов с is_fit=false",
    )
    min_qualify_score: int = Field(default=60, ge=0, le=100)
    auto_qualify: bool = Field(
        default=True,
        description="True — квалифицировать сайт перед генерацией, если ещё не был",
    )
    target_query: str = Field(default="стоматология")
    target_description: str = Field(default="стоматологическая клиника")


class GenerateOutreachSiteResult(BaseModel):
    site_id: str
    domain: str
    status: str
    outreach_id: str | None = None
    message_subject: str | None = None
    message_text: str | None = None
    personalization_hook: str | None = None
    qualify: QualifyResult | dict | None = None
    error_message: str | None = None


class GenerateOutreachResponse(BaseModel):
    processed: int
    generated: int
    results: list[GenerateOutreachSiteResult]


class OutreachItem(BaseModel):
    id: str
    site_id: str
    site_url: str
    domain: str
    message_subject: str | None = None
    message_text: str | None = None
    sent_status: str
    sent_at: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    llm_fit_score: int | None = None
    llm_is_fit: bool | None = None
    llm_business_name: str | None = None


class OutreachListResponse(BaseModel):
    total_returned: int
    items: list[OutreachItem]


class FormFillFieldItem(BaseModel):
    selector: str
    value: str
    purpose: str = "unknown"


class FormFillRequest(BaseModel):
    sender_name: str = Field(..., min_length=1, examples=["Иван Петров"])
    sender_phone: str = Field(default="", examples=["+7 (999) 123-45-67"])
    sender_email: str = Field(default="", examples=["ivan@example.com"])
    outreach_id: str | None = Field(
        default=None,
        description="ID outreach-записи; если не указан — последний pending",
    )


class FormFillResponse(BaseModel):
    site_id: str
    domain: str
    outreach_id: str
    form_id: str
    page_url: str
    form_selector: str
    submit_selector: str | None = None
    fields: list[FormFillFieldItem]


class SubmitOutreachRequest(BaseModel):
    sender_name: str = Field(..., min_length=1)
    sender_phone: str = Field(default="")
    sender_email: str = Field(default="")
    outreach_id: str | None = None
    headless: bool = Field(default=True)
    dry_run: bool = Field(
        default=True,
        description="True — заполнить форму без клика Submit",
    )


class SubmitOutreachResponse(BaseModel):
    site_id: str
    domain: str
    outreach_id: str
    form_id: str
    page_url: str
    form_selector: str
    submit_selector: str | None = None
    dry_run: bool
    submitted: bool
    fields_filled: int
    scrape_source: str | None = None
    proxy_server: str | None = None
    fields: list[FormFillFieldItem] = Field(default_factory=list)


class PipelineStepResult(BaseModel):
    step: str
    status: str
    detail: str | None = None
    error: str | None = None
    emails: int | None = None
    phones: int | None = None
    forms_saved: int | None = None
    outreach_id: str | None = None
    dry_run: bool | None = None
    submitted: bool | None = None
    fields_filled: int | None = None


class SitePipelineRequest(OutreachOfferContext):
    headless: bool = Field(default=True)
    skip_enrich_if_done: bool = Field(default=True)
    auto_qualify: bool = Field(default=True)
    skip_unqualified: bool = Field(default=True)
    target_query: str = Field(default="стоматология")
    target_description: str = Field(default="стоматологическая клиника")
    dry_run_submit: bool = Field(
        default=True,
        description="True — заполнить форму без отправки",
    )


class SitePipelineResponse(BaseModel):
    site_id: str
    domain: str
    status: str
    steps: list[PipelineStepResult]
    outreach_id: str | None = None
    error_message: str | None = None
    submit: SubmitOutreachResponse | dict | None = None


class PipelineBatchRequest(SitePipelineRequest):
    search_query: str | None = Field(
        default=None,
        description="Если задан — сначала POST /search/google/batch",
    )
    search_cities: list[str] | None = Field(default=None, examples=[["Казань"]])
    search_max_pages: int = Field(default=1, ge=1, le=10)
    sites_limit: int = Field(default=5, ge=1, le=20, description="Сколько сайтов обработать")
    scrape_status: str = Field(
        default="pending",
        description="Какие сайты брать из GET /sites после поиска",
    )
