import pytest
import requests

from jobtracker import sources
from jobtracker.models import Job


class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)


def test_iso_formats():
    assert sources.iso("2026-11-15T10:00:00Z") == "2026-11-15"
    assert sources.iso(1763251200000) == "2025-11-16"
    assert sources.iso("") == "" and sources.iso(None) == "" and sources.iso("ontem") == ""


def test_clean_url_strips_tracking_and_fragment():
    assert sources.clean_url("https://a.com/jobs/1/?utm_source=x&id=7#apply") == "https://a.com/jobs/1?id=7"


def test_parse_greenhouse():
    data = {"jobs": [{"title": "Strategy Manager", "absolute_url": "https://boards.greenhouse.io/x/jobs/1?gh_src=abc",
                      "location": {"name": "Lisbon"}, "updated_at": "2026-11-10T09:00:00Z",
                      "content": "&lt;p&gt;5+ years of experience&lt;/p&gt;"}]}
    (j,) = sources.parse_greenhouse(data, "x", "X Co")
    assert j.title == "Strategy Manager" and j.location == "Lisbon" and j.posted == "2026-11-10"
    assert j.description == "5+ years of experience" and "gh_src" not in j.url


def test_parse_lever():
    data = [{"text": "ESG Lead", "hostedUrl": "https://jobs.lever.co/x/1", "categories": {"location": "Porto"},
             "descriptionPlain": "desc", "createdAt": 1763251200000, "workplaceType": "remote"}]
    (j,) = sources.parse_lever(data, "x", None)
    assert j.company == "x" and j.remote is True and j.posted == "2025-11-16"


def test_parse_workable_list_and_detail_closed():
    data = {"results": [{"shortcode": "ABC123", "title": "AI Program Manager",
                         "location": {"city": "Lisbon", "country": "Portugal"}, "published": "2026-11-01"}]}
    (j,) = sources.parse_workable_list(data, "greenvolt", "Greenvolt")
    assert j.url == "https://apply.workable.com/greenvolt/j/ABC123" and j.location == "Lisbon, Portugal"
    assert sources.needs_detail(j)
    assert sources.fetch_detail(j, FakeSession(Resp(404))) is None          # fechada


def test_workable_detail_fills_description():
    j = Job("workable:g", "G", "T", "https://u", extra={"workable_account": "g", "shortcode": "A"})
    s = FakeSession(Resp(200, {"description": "<p>Lead AI</p>", "requirements": "<li>5+ years of experience</li>"}))
    out = sources.fetch_detail(j, s)
    assert "5+ years of experience" in out.description and not sources.needs_detail(out)


def test_parse_smartrecruiters_and_detail():
    data = {"content": [{"id": "99", "name": "Sustainability Manager", "releasedDate": "2026-11-12T00:00:00Z",
                         "location": {"city": "Lisbon", "country": "pt"}}], "totalFound": 1}
    (j,) = sources.parse_smartrecruiters_list(data, "acme", "Acme")
    assert j.url.endswith("/acme/99") and j.location == "Lisbon, PT"
    s = FakeSession(Resp(200, {"jobAd": {"sections": {"jobDescription": {"text": "<p>CSRD lead</p>"}}}}))
    assert "CSRD lead" in sources.fetch_detail(j, s).description


def test_parse_adzuna_marks_partial_and_salary():
    data = {"results": [{"title": "<b>ESG</b> Manager", "company": {"display_name": "Acme"},
                         "location": {"display_name": "Lisboa"}, "redirect_url": "https://adzuna/1",
                         "description": "snippet…", "created": "2026-11-14T00:00:00Z",
                         "salary_min": 40000, "salary_max": 50000}]}
    (j,) = sources.parse_adzuna(data)
    assert j.partial and j.title == "ESG Manager" and j.salary_max == 50000


def test_adzuna_skipped_without_keys():
    with pytest.raises(sources.SourceSkipped):
        sources.fetch_adzuna({"queries": ["x"]}, FakeSession(), {})


def test_parse_remotive():
    data = {"jobs": [{"title": "Ops Lead", "company_name": "R", "url": "https://remotive.com/1",
                      "candidate_required_location": "Europe", "publication_date": "2026-11-14T00:00:00",
                      "description": "<p>x</p>"}]}
    (j,) = sources.parse_remotive(data)
    assert j.remote and j.location == "Remote - Europe"


def test_parse_page_links_filters_and_dedupes():
    html_text = ('<a href="/job/1">Policy Officer - Digital</a><a href="/job/1">Policy Officer - Digital</a>'
                 '<a href="/about">About us today</a><a href="/job/2">Home</a>')
    jobs = sources.parse_page_links(html_text, "https://eu.example/list", r"officer|policy", "EU", "Lisbon")
    assert [j.url for j in jobs] == ["https://eu.example/job/1"] and jobs[0].unverified


def test_page_watch_zero_links_is_an_error():
    s = FakeSession(Resp(200, text="<html><body>JS only</body></html>"))
    with pytest.raises(sources.SourceError):
        sources.fetch_page_watch({"url": "https://x", "name": "N", "link_regex": "officer"}, s, {})


def test_http_retries_then_raises():
    s = FakeSession(Resp(503), Resp(503), Resp(503), Resp(503))
    with pytest.raises(sources.SourceError):
        sources.http(s, "GET", "https://x.com/a")
    assert len(s.calls) == 4


def test_http_recovers_after_network_error():
    s = FakeSession(requests.ConnectionError("boom"), Resp(200, {"ok": 1}))
    assert sources.http(s, "GET", "https://x.com/a").status_code == 200


def test_get_json_bad_status_and_bad_body():
    with pytest.raises(sources.SourceError):
        sources.get_json(FakeSession(Resp(404)), "GET", "https://x.com/a")
    with pytest.raises(sources.SourceError):
        sources.get_json(FakeSession(Resp(200, None)), "GET", "https://x.com/a")


def test_unknown_source_type():
    with pytest.raises(sources.SourceError):
        sources.fetch_source({"type": "nope"}, FakeSession(), {})


def test_page_links_edp_style_and_pdf_listing():
    html = ('<a href="/job/Porto-Dispatch-&-Remote-Operations-Specialist/1455742133/">Dispatch &amp; Remote Operations Specialist</a>'
            '<a href="/search/?q=x">Next</a>'
            '<a href="/files/uploads/cms/Anuncio_Juridico_2026.pdf">Jurista (m/f)</a>')
    jobs = sources.parse_page_links(html, "https://jobs.edp.com/search/", ".", "EDP (Porto)", "Porto")
    assert [j.title for j in jobs] == ["Dispatch & Remote Operations Specialist", "Jurista (m/f)"]
    assert jobs[0].url.startswith("https://jobs.edp.com/job/Porto-Dispatch")
    assert all(j.unverified and j.location == "Porto" for j in jobs)


# ---------- novos adaptadores (formatos escritos pela documentação pública; não testados ao vivo) ----------
from datetime import date

WD_SRC = {"tenant": "acme", "wd": "wd3", "site": "Careers", "name": "Acme"}


def test_workday_posted_text():
    t = date(2026, 11, 15)
    assert sources.parse_workday_posted("Posted Today", t) == "2026-11-15"
    assert sources.parse_workday_posted("Posted Yesterday", t) == "2026-11-14"
    assert sources.parse_workday_posted("Posted 5 Days Ago", t) == "2026-11-10"
    assert sources.parse_workday_posted("Posted 30+ Days Ago", t) == ""


def test_parse_workday_and_pagination():
    data = {"total": 1, "jobPostings": [{"title": "ESG Manager", "externalPath": "/job/Porto/ESG-Manager_R1",
                                         "locationsText": "Porto", "postedOn": "Posted Today"}]}
    (j,) = sources.parse_workday(data, WD_SRC, date(2026, 11, 15))
    assert j.url == "https://acme.wd3.myworkdayjobs.com/Careers/job/Porto/ESG-Manager_R1"
    assert j.extra["workday_detail"].endswith("/wday/cxs/acme/Careers/job/Porto/ESG-Manager_R1")
    s = FakeSession(Resp(200, data))
    assert len(sources.fetch_workday(WD_SRC, s, {})) == 1 and s.calls[0][0] == "POST"


def test_workday_detail_closed_and_open():
    j = Job(source="workday:acme", company="Acme", title="t", url="https://x/1",
            extra={"workday_detail": "https://acme/api"})
    assert sources.needs_detail(j)
    assert sources.fetch_detail(j, FakeSession(Resp(404))) is None
    ok = sources.fetch_detail(j, FakeSession(Resp(200, {"jobPostingInfo": {"jobDescription": "<p>3 years</p>",
                                                                              "startDate": "2026-11-10"}})))
    assert ok.description == "3 years" and ok.posted == "2026-11-10"


def test_parse_personio():
    xml = ('<workzag-jobs><position><id>7</id><office>Porto</office><name>Sustainability Lead</name>'
           '<createdAt>2026-11-12T10:00:00+00:00</createdAt><jobDescriptions><jobDescription><name>A</name>'
           '<value>&lt;p&gt;5 years&lt;/p&gt;</value></jobDescription></jobDescriptions></position></workzag-jobs>')
    (j,) = sources.parse_personio(xml, "acme", "Acme")
    assert j.url == "https://acme.jobs.personio.de/job/7" and j.location == "Porto" and j.posted == "2026-11-12"
    assert "5 years" in j.description
    with pytest.raises(sources.SourceError):
        sources.parse_personio("<<nope", "acme", None)


def test_parse_teamtailor():
    xml = ('<rss xmlns:tt="https://teamtailor.com/locations"><channel><item><title>PMO Manager</title>'
           '<link>https://jobs.acme.com/jobs/1-pmo?utm_source=x</link><pubDate>Fri, 13 Nov 2026 09:00:00 +0000</pubDate>'
           '<description>&lt;p&gt;desc&lt;/p&gt;</description><tt:locations><tt:location><tt:city>Lisboa</tt:city>'
           '</tt:location></tt:locations></item></channel></rss>')
    (j,) = sources.parse_teamtailor(xml, "jobs.acme.com", "Acme")
    assert j.posted == "2026-11-13" and j.location == "Lisboa" and "utm" not in j.url


def test_parse_recruitee_skips_unpublished():
    data = {"offers": [{"title": "A", "careers_url": "https://a.recruitee.com/o/a", "status": "published",
                        "city": "Porto", "created_at": "2026-11-01 10:00:00 UTC"},
                       {"title": "B", "careers_url": "https://a.recruitee.com/o/b", "status": "draft"}]}
    (j,) = sources.parse_recruitee(data, "a", None)
    assert j.title == "A" and j.posted == "2026-11-01"


JSONLD = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",'
          '"title":"Energy Analyst","datePosted":"2026-11-09","validThrough":"%s","description":"&lt;p&gt;3+ years&lt;/p&gt;",'
          '"jobLocation":{"address":{"addressLocality":"Porto","addressCountry":"PT"}},'
          '"baseSalary":{"currency":"EUR","value":{"minValue":3000,"maxValue":3500,"unitText":"MONTH"}}}</script></html>')


def test_jsonld_fills_job_and_salary_monthly_x14():
    j = Job(source="page:EDP", company="EDP", title="Energy Analyst", url="https://jobs.edp.com/job/1",
            unverified=True, extra={"jsonld": True})
    out = sources.fetch_detail(j, FakeSession(Resp(200, text=JSONLD % "2099-01-01")))
    assert out.salary_min == 42000 and out.salary_max == 49000 and out.location == "Porto, PT"
    assert out.posted == "2026-11-09" and out.unverified is False and "3+ years" in out.description


def test_jsonld_expired_means_closed_and_missing_keeps_job():
    j = Job(source="page:X", company="X", title="t", url="https://x/1", unverified=True, extra={"jsonld": True})
    assert sources.fetch_detail(j, FakeSession(Resp(200, text=JSONLD % "2020-01-01"))) is None
    j2 = Job(source="page:X", company="X", title="t", url="https://x/f.pdf", unverified=True, extra={"jsonld": True})
    assert sources.fetch_detail(j2, FakeSession(Resp(200, text="%PDF"))) is j2
    assert sources.fetch_detail(j2, FakeSession(Resp(404))) is None


def test_page_links_skip_social_and_other_hosts_and_use_url_regex():
    html = ('<a href="/job/Porto-Dispatch-Specialist/1455742133/">Dispatch Specialist</a>'
            '<a href="https://www.instagram.com/edp.global/">Instagram EDP</a>'
            '<a href="/content/Politica-Privacidade/">Política de Privacidade</a>'
            '<a href="https://www.edp.com/pt-pt">Website da EDP</a>')
    jobs = sources.parse_page_links(html, "https://jobs.edp.com/search/", ".", "EDP", "Porto", url_pattern="/job/")
    assert [j.title for j in jobs] == ["Dispatch Specialist"]


def test_http_backs_off_on_429(monkeypatch):
    waits, calls = [], []
    monkeypatch.setattr(sources.time, "sleep", lambda s: waits.append(s))

    class R:
        def __init__(self, c):
            self.status_code, self.headers = c, {}

    class S:
        def request(self, *a, **k):
            calls.append(1)
            return R(429 if len(calls) < 3 else 200)
    r = sources.http(S(), "GET", "https://x.test/")
    assert r.status_code == 200 and len(calls) == 3 and min(waits) >= 8


# ---------- LinkedIn ----------

def _fx(name):
    with open(f"tests/fixtures/{name}", encoding="utf-8") as f:
        return f.read()


def test_parse_linkedin_cards():
    a, b = sources.parse_linkedin_cards(_fx("linkedin_search.html"))
    assert a.title == "Sustainability Manager" and a.company == "ACME Energia"
    assert a.url == "https://www.linkedin.com/jobs/view/4051234567" and a.location == "Lisbon, Lisbon, Portugal"
    assert a.posted == "2026-11-14" and a.extra == {"linkedin_id": "4051234567"} and not a.description
    assert b.url.endswith("/4059999999") and b.remote is True     # id tirado do endereço quando falta o urn
    assert sources.needs_detail(a)


def test_parse_linkedin_detail_and_closed():
    desc, closed, level = sources.parse_linkedin_detail(_fx("linkedin_detail.html"))
    assert "4+ years of experience" in desc and "CSRD" in desc and not closed and level == "Mid-Senior level"
    _, closed, _ = sources.parse_linkedin_detail('<figure class="closed-job"><figcaption>No longer accepting applications</figcaption></figure>')
    assert closed


def test_fetch_linkedin_paginates_and_dedupes():
    page = _fx("linkedin_search.html")
    s = FakeSession(Resp(text=page), Resp(text=page))         # 2 pesquisas devolvem as mesmas 2 vagas
    jobs = sources.fetch_linkedin({"queries": ["esg", "pmo"]}, s, {}, sleep=lambda x: None)
    assert len(jobs) == 2 and "f_TPR=" not in s.calls[0][1]    # parâmetros vão em params, não no URL
    assert jobs.note == ""


def test_fetch_linkedin_keeps_partial_results_when_throttled():
    page = _fx("linkedin_search.html")
    s = FakeSession(Resp(text=page), Resp(status=429), Resp(status=429))
    jobs = sources.fetch_linkedin({"queries": ["esg", "pmo"]}, s, {}, sleep=lambda x: None)
    assert len(jobs) == 2 and "parcial" in jobs.note


def test_fetch_linkedin_all_blocked_is_an_error():
    s = FakeSession(Resp(status=429), Resp(status=429), Resp(status=429), Resp(status=429))
    with pytest.raises(sources.SourceError):
        sources.fetch_linkedin({"queries": ["esg", "pmo", "x"]}, s, {}, sleep=lambda x: None)


def test_fetch_linkedin_detail():
    j = sources.parse_linkedin_cards(_fx("linkedin_search.html"))[0]
    out = sources.fetch_linkedin_detail(j, FakeSession(Resp(text=_fx("linkedin_detail.html"))), sleep=lambda x: None)
    assert "CSRD" in out.description and out.extra["level"] == "Mid-Senior level"
    j2 = sources.parse_linkedin_cards(_fx("linkedin_search.html"))[0]
    assert sources.fetch_linkedin_detail(j2, FakeSession(Resp(status=404), Resp(status=404)), sleep=lambda x: None) is None
    j3 = sources.parse_linkedin_cards(_fx("linkedin_search.html"))[0]
    with pytest.raises(sources.SourceError):      # página sem descrição = bloqueio, não "fechada"
        sources.fetch_linkedin_detail(j3, FakeSession(Resp(text="<html>login</html>")), sleep=lambda x: None)


# ---------- Indeed (JobSpy) ----------

def test_parse_jobspy_rows():
    nan = float("nan")
    rows = [{"job_url": "https://pt.indeed.com/viewjob?jk=abc", "title": "Gestor de Projeto ESG", "company": "Gama",
             "location": "Porto, Portugal", "date_posted": "2026-11-13", "description": "**3 anos de experiência**",
             "interval": "monthly", "min_amount": 2500.0, "max_amount": 3000.0, "currency": "EUR", "is_remote": False},
            {"job_url": "https://pt.indeed.com/viewjob?jk=def", "title": "Analista", "company": nan, "location": nan,
             "date_posted": nan, "description": nan, "interval": nan, "min_amount": nan, "max_amount": nan,
             "currency": nan, "is_remote": nan},
            {"job_url": nan, "title": "sem link"}]
    a, b = sources.parse_jobspy_rows(rows)
    assert a.salary_min == 35000 and a.salary_max == 42000 and a.posted == "2026-11-13" and a.remote is None
    assert b.company == "" and b.salary_min is None and b.description == ""


def test_indeed_skipped_without_library(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "jobspy":
            raise ImportError("no")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(sources.SourceSkipped):
        sources.fetch_indeed({"queries": ["x"]}, None, {})


# ---------- BEP ----------

BEP_SRC = {"name": "BEP", "start_id": 1000, "careers": "t[ée]cnico superior|dire[çc][ãa]o",
           "exclude_types": "mobilidade|concurso interno", "min_score": 3, "lookback": 0, "max_misses": 3}


def test_parse_bep_detail_to_job():
    from datetime import date
    d = sources.parse_bep_detail(_fx("bep_detail.html"), 1001)
    j = sources.bep_to_job(d, BEP_SRC, today=date(2026, 11, 15))
    assert j.title == "Técnico Superior — Gestão de fundos europeus do Portugal 2030"
    assert j.company.startswith("Comissão de Coordenação") and j.location == "Porto, Porto, Portugal"
    assert j.posted == "2026-11-13" and j.extra["deadline"] == "2026-11-27" and "excluded" not in j.extra
    assert "Licenciatura" in j.description and j.extra["min_score"] == 3
    assert sources.parse_bep_detail("<html>erro</html>", 1) is None


def test_bep_excludes_islands_internal_and_expired():
    from datetime import date
    base = sources.parse_bep_detail(_fx("bep_detail.html"), 1001)
    t = date(2026, 11, 15)
    assert "continente" in sources.bep_to_job({**base, "distrito": "RAM - Ilha da Madeira"}, BEP_SRC, t).extra["excluded"]
    assert "tipo" in sources.bep_to_job({**base, "tipo": "Mobilidade Interna"}, BEP_SRC, t).extra["excluded"]
    assert "prazo" in sources.bep_to_job({**base, "limite": "2026-11-01"}, BEP_SRC, t).extra["excluded"]
    assert "carreira" in sources.bep_to_job({**base, "carreira": "Assistente Operacional", "categoria": ""}, BEP_SRC, t).extra["excluded"]


class BepResp(Resp):
    def __init__(self, ok, cod):
        super().__init__(200, text=_fx("bep_detail.html") if ok else "<html>Erro</html>")
        self.url = sources.BEP_DETAIL.format(cod) if ok else "https://www.bep.gov.pt/pages/erros/ErroGenerico.aspx"


def test_fetch_bep_scans_until_misses_and_moves_cursor():
    # 1001 existe, 1002 ainda não, 1003 existe (buraco na numeração), 1004-1006 não -> pára
    s = FakeSession(BepResp(True, 1001), BepResp(False, 1002), BepResp(True, 1003),
                    BepResp(False, 1004), BepResp(False, 1005), BepResp(False, 1006))
    jobs = sources.fetch_bep(BEP_SRC, s, {}, sleep=lambda x: None)
    assert [j.url.rsplit("=", 1)[1] for j in jobs] == ["1001", "1003"] and jobs.cursor == {"last": 1003}
    assert len(s.calls) == 6


def test_fetch_bep_skips_known_and_uses_cursor():
    src = {**BEP_SRC, "_cursor": {"last": 1003}, "lookback": 2,
           "_known": {sources.BEP_DETAIL.format(1003)}}
    s = FakeSession(BepResp(True, 1002), BepResp(False, 1004), BepResp(False, 1005), BepResp(False, 1006))
    jobs = sources.fetch_bep(src, s, {}, sleep=lambda x: None)
    assert len(jobs) == 1 and jobs.cursor == {"last": 1003} and len(s.calls) == 4   # 1003 não foi pedida


def test_linkedin_card_keeps_company_logo():
    a, b = sources.parse_linkedin_cards(_fx("linkedin_search.html"))
    assert a.logo.startswith("https://media.licdn.com/") and b.logo == ""
    assert "logo" in a.to_dict()


def test_linkedin_company_search_keeps_only_target_companies():
    s = FakeSession(Resp(text=_fx("linkedin_search.html")))
    jobs = sources.fetch_linkedin({"queries": ["ACME"], "_company_rx": r"\bACME\b"}, s, {}, sleep=lambda x: None)
    assert [j.company for j in jobs] == ["ACME Energia"]


def test_parse_ongd():
    jobs = sources.parse_ongd(_fx("ongd.html"))
    assert [j.title for j in jobs] == ["Farmacêuticos/as", "Gestor/a de Projetos de Cooperação e Advocacy"]
    a, b = jobs
    assert a.company == "Médicos Sem Fronteiras" and a.location == "Portugal" and a.url.startswith("https://msf.org.pt/vaga/farmaceuticos")
    assert b.company == "Oikos" and b.location == "Lisboa" and "2026.11.30" in b.description and "ONGD" in b.description


def test_parse_eu_agencies_only_open_calls():
    src = {"name": "EUDA (agência europeia, Lisboa)", "company": "EUDA"}
    euda = sources.parse_euda(_fx("euda.html"), src, "https://www.euda.europa.eu/about/jobs_en")
    assert [j.title for j in euda] == ["Policy officer – EU drug policy and international cooperation"]
    assert euda[0].url == "https://www.euda.europa.eu/calls/2026/ad202602_en" and euda[0].location == "Lisboa, Portugal"
    emsa = sources.parse_emsa(_fx("emsa.html"), {"name": "EMSA", "company": "EMSA"}, "https://www.emsa.europa.eu/jobs/vacancies.html")
    assert [j.title for j in emsa] == ["Officer - Decarbonisation of Shipping (FGIV)", "Officer - Ship Safety and Accident Investigation (SNE)"]
    assert "26.11.2026" in emsa[0].description
