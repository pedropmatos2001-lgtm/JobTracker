"""Adaptadores de fontes. Cada `fetch_*` devolve list[Job] ou levanta SourceError.
As funções `parse_*` são puras (recebem JSON/HTML já obtido) e estão cobertas por testes.

AVISO: os formatos das APIs abaixo foram escritos a partir da documentação pública
e NÃO foram chamados ao vivo durante o desenvolvimento (a rede do ambiente de
desenvolvimento estava bloqueada). A primeira execução real mostra, por fonte,
se respondeu; uma fonte avariada nunca derruba as outras.
"""
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from .models import Job
from .scoring import strip_html

UA = {"User-Agent": "jobtracker-pedro/1.0 (uso pessoal)"}


class SourceError(Exception):
    pass


class SourceSkipped(Exception):
    """Fonte desligada de propósito (ex.: falta uma chave)."""


# ---------- utilitários ----------

def clean_url(u: str) -> str:
    p = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "gh_src", "source"))]
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/", urlencode(q), ""))


def iso(x) -> str:
    """Converte datas de várias formas para AAAA-MM-DD ('' se desconhecida)."""
    if x is None or x == "":
        return ""
    if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit() and len(x) >= 12):
        try:
            return datetime.fromtimestamp(int(x) / 1000, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return ""
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(x))
    return m.group(0) if m else ""


def http(session, method, url, **kw):
    kw.setdefault("timeout", 25)
    kw["headers"] = {**UA, **kw.get("headers", {})}
    last = None
    for i in range(3):
        try:
            r = session.request(method, url, **kw)
        except requests.RequestException as e:
            last = str(e)
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
            else:
                return r
        time.sleep(2 * (i + 1))
    raise SourceError(f"{urlsplit(url).netloc}: {last}")


def get_json(session, method, url, **kw):
    r = http(session, method, url, **kw)
    if r.status_code != 200:
        raise SourceError(f"{urlsplit(url).netloc}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise SourceError(f"{urlsplit(url).netloc}: resposta não é JSON")


# ---------- Greenhouse ----------

def parse_greenhouse(data, token, name):
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            source=f"greenhouse:{token}", company=name or token, title=j.get("title", ""),
            url=clean_url(j.get("absolute_url", "")), location=(j.get("location") or {}).get("name", ""),
            description=strip_html(j.get("content", "")), posted=iso(j.get("updated_at"))))
    return jobs


def fetch_greenhouse(src, session, env):
    token = src["token"]
    data = get_json(session, "GET", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                    params={"content": "true"})
    return parse_greenhouse(data, token, src.get("name"))


# ---------- Lever ----------

def parse_lever(data, company, name):
    jobs = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        jobs.append(Job(
            source=f"lever:{company}", company=name or company, title=j.get("text", ""),
            url=clean_url(j.get("hostedUrl", "")), location=cats.get("location", "") or "",
            description=j.get("descriptionPlain") or strip_html(j.get("description", "")),
            posted=iso(j.get("createdAt")), remote=(j.get("workplaceType") == "remote") or None))
    return jobs


def fetch_lever(src, session, env):
    c = src["company"]
    data = get_json(session, "GET", f"https://api.lever.co/v0/postings/{c}", params={"mode": "json"})
    return parse_lever(data, c, src.get("name"))


# ---------- Workable (lista + detalhe) ----------

def parse_workable_list(data, account, name):
    jobs = []
    for j in data.get("results", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        sc = j.get("shortcode", "")
        jobs.append(Job(
            source=f"workable:{account}", company=name or account, title=j.get("title", ""),
            url=f"https://apply.workable.com/{account}/j/{sc}", location=where,
            posted=iso(j.get("published")), remote=bool(j.get("remote")) or None,
            extra={"workable_account": account, "shortcode": sc}))
    return jobs


def fetch_workable(src, session, env):
    acc = src["account"]
    out, token = [], None
    for _ in range(5):
        body = {"token": token} if token else {"query": "", "location": [], "department": [],
                                                "worktype": [], "remote": []}
        data = get_json(session, "POST", f"https://apply.workable.com/api/v3/accounts/{acc}/jobs", json=body)
        out += parse_workable_list(data, acc, src.get("name"))
        token = data.get("nextPage")
        if not token:
            break
    return out


# ---------- SmartRecruiters (lista + detalhe) ----------

def parse_smartrecruiters_list(data, cid, name):
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), (loc.get("country") or "").upper()) if x)
        jobs.append(Job(
            source=f"smartrecruiters:{cid}", company=name or cid, title=j.get("name", ""),
            url=f"https://jobs.smartrecruiters.com/{cid}/{j.get('id', '')}", location=where,
            posted=iso(j.get("releasedDate")), remote=bool(loc.get("remote")) or None,
            extra={"sr_company": cid, "sr_id": j.get("id", "")}))
    return jobs


def fetch_smartrecruiters(src, session, env):
    cid = src["company"]
    out, offset = [], 0
    for _ in range(5):
        data = get_json(session, "GET", f"https://api.smartrecruiters.com/v1/companies/{cid}/postings",
                        params={"limit": 100, "offset": offset, "country": src.get("country", "pt")})
        out += parse_smartrecruiters_list(data, cid, src.get("name"))
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


# ---------- Adzuna (agregador; precisa de chave gratuita) ----------

def parse_adzuna(data):
    jobs = []
    for j in data.get("results", []):
        jobs.append(Job(
            source="adzuna", company=(j.get("company") or {}).get("display_name", ""),
            title=strip_html(j.get("title", "")), url=clean_url(j.get("redirect_url", "")),
            location=(j.get("location") or {}).get("display_name", ""),
            description=strip_html(j.get("description", "")), posted=iso(j.get("created")),
            salary_min=j.get("salary_min"), salary_max=j.get("salary_max"), partial=True))
    return jobs


def fetch_adzuna(src, session, env):
    app_id, app_key = env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise SourceSkipped("faltam ADZUNA_APP_ID / ADZUNA_APP_KEY")
    out = []
    for q in src.get("queries", []):
        for where in src.get("where", [""]):
            data = get_json(session, "GET", f"https://api.adzuna.com/v1/api/jobs/{src.get('country', 'pt')}/search/1",
                            params={"app_id": app_id, "app_key": app_key, "what": q, "where": where,
                                    "results_per_page": 50, "max_days_old": src.get("max_days_old", 10),
                                    "content-type": "application/json"})
            out += parse_adzuna(data)
    return out


# ---------- Remotive (remoto) ----------

def parse_remotive(data):
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(Job(
            source="remotive", company=j.get("company_name", ""), title=j.get("title", ""),
            url=clean_url(j.get("url", "")), location=f"Remote - {j.get('candidate_required_location', '')}".strip(" -"),
            description=strip_html(j.get("description", "")), posted=iso(j.get("publication_date")),
            remote=True))
    return jobs


def fetch_remotive(src, session, env):
    out = []
    for term in src.get("search", []):
        out += parse_remotive(get_json(session, "GET", "https://remotive.com/api/remote-jobs",
                                       params={"search": term}))
    return out


# ---------- page_watch (páginas de carreiras sem API) ----------

class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._buf = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._buf = dict(attrs).get("href"), []

    def handle_data(self, d):
        if self._href is not None:
            self._buf.append(d)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._buf).split())
            if text:
                self.links.append((self._href, text))
            self._href = None


def parse_page_links(html_text, base_url, pattern, name, location=""):
    p = _Links()
    p.feed(html_text)
    rx = re.compile(pattern, re.I)
    seen, jobs = set(), []
    for href, text in p.links:
        if not (8 <= len(text) <= 160) or not rx.search(text):
            continue
        url = clean_url(urljoin(base_url, href))
        if url in seen:
            continue
        seen.add(url)
        jobs.append(Job(source=f"page:{name}", company=name, title=text, url=url,
                        location=location, unverified=True, extra={"jsonld": True}))
    return jobs


def fetch_page_watch(src, session, env):
    r = http(session, "GET", src["url"])
    if r.status_code != 200:
        raise SourceError(f"{urlsplit(src['url']).netloc}: HTTP {r.status_code}")
    jobs = parse_page_links(r.text, src["url"], src.get("link_regex", "."), src["name"], src.get("location", ""))
    if not jobs:
        raise SourceError("0 ligações com o padrão: a página pode ser dinâmica (JavaScript) ou ter mudado")
    return jobs


# ---------- Workday (endpoint não documentado: pode mudar sem aviso) ----------

def parse_workday_posted(text, today=None):
    """'Posted 5 Days Ago' / 'Posted Today' / 'Posted Yesterday' -> AAAA-MM-DD ('' se não der)."""
    from datetime import date, timedelta
    today = today or date.today()
    t = (text or "").lower()
    if "today" in t or "hoje" in t:
        return today.isoformat()
    if "yesterday" in t or "ontem" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*(day|dia)", t)
    if m and "+" not in t:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""


def workday_base(src):
    return f"https://{src['tenant']}.{src.get('wd', 'wd3')}.myworkdayjobs.com"


def parse_workday(data, src, today=None):
    base, site = workday_base(src), src["site"]
    jobs = []
    for j in (data or {}).get("jobPostings", []) or []:
        path = j.get("externalPath", "")
        if not path:
            continue
        jobs.append(Job(
            source=f"workday:{src['tenant']}", company=src.get("name") or src["tenant"],
            title=j.get("title", ""), url=clean_url(f"{base}/{site}{path}"),
            location=j.get("locationsText", "") or src.get("location", ""),
            posted=parse_workday_posted(j.get("postedOn"), today),
            extra={"workday_detail": f"{base}/wday/cxs/{src['tenant']}/{site}{path}"}))
    return jobs


def fetch_workday(src, session, env):
    base = workday_base(src)
    url = f"{base}/wday/cxs/{src['tenant']}/{src['site']}/jobs"
    out, offset = [], 0
    for _ in range(int(src.get("max_pages", 5))):
        data = get_json(session, "POST", url, json={
            "appliedFacets": {}, "limit": 20, "offset": offset, "searchText": src.get("search", "")},
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        page = parse_workday(data, src)
        out.extend(page)
        offset += 20
        if not page or offset >= int(data.get("total", 0) or 0):
            break
    return out


# ---------- Personio (feed XML público) ----------

def parse_personio(xml_text, slug, name):
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SourceError("personio: XML inválido (o feed pode estar desligado nesta empresa)")
    jobs = []
    for pos in root.iter("position"):
        pid = (pos.findtext("id") or "").strip()
        title = (pos.findtext("name") or "").strip()
        if not pid or not title:
            continue
        desc = " ".join(strip_html(v.text or "") for v in pos.iter("value"))
        jobs.append(Job(
            source=f"personio:{slug}", company=name or slug, title=title,
            url=f"https://{slug}.jobs.personio.de/job/{pid}", location=(pos.findtext("office") or "").strip(),
            description=desc, posted=iso(pos.findtext("createdAt"))))
    return jobs


def fetch_personio(src, session, env):
    slug = src["account"]
    r = http(session, "GET", f"https://{slug}.jobs.personio.de/xml", params={"language": src.get("language", "en")})
    if r.status_code != 200:
        raise SourceError(f"personio: HTTP {r.status_code}")
    return parse_personio(r.text, slug, src.get("name"))


# ---------- Teamtailor (feed RSS público) ----------

def parse_teamtailor(xml_text, host, name):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SourceError("teamtailor: RSS inválido")
    jobs = []
    for it in root.iter("item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        try:
            posted = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            posted = ""
        city = next((e.text.strip() for e in it.iter() if e.tag.split("}")[-1] == "city" and e.text), "")
        jobs.append(Job(source=f"teamtailor:{host}", company=name or host, title=title, url=clean_url(link),
                        location=city, description=strip_html(it.findtext("description") or ""), posted=posted))
    return jobs


def fetch_teamtailor(src, session, env):
    host = src["host"]
    r = http(session, "GET", f"https://{host}/jobs.rss")
    if r.status_code != 200:
        raise SourceError(f"teamtailor: HTTP {r.status_code}")
    return parse_teamtailor(r.text, host, src.get("name"))


# ---------- Recruitee (API pública de ofertas) ----------

def parse_recruitee(data, company, name):
    jobs = []
    for o in (data or {}).get("offers", []) or []:
        if o.get("status") not in (None, "published"):
            continue
        jobs.append(Job(
            source=f"recruitee:{company}", company=name or company, title=o.get("title", ""),
            url=clean_url(o.get("careers_url", "")), location=o.get("location") or o.get("city") or "",
            description=strip_html(" ".join(o.get(k, "") or "" for k in ("description", "requirements"))),
            posted=iso(o.get("published_at") or o.get("created_at")), remote=o.get("remote") or None))
    return jobs


def fetch_recruitee(src, session, env):
    c = src["company"]
    return parse_recruitee(get_json(session, "GET", f"https://{c}.recruitee.com/api/offers/"), c, src.get("name"))


# ---------- JSON-LD JobPosting (qualquer página de vaga que o Google indexe) ----------

def parse_jsonld_jobposting(html_text):
    """Devolve o primeiro objeto JobPosting encontrado em <script type="application/ld+json">, ou None."""
    import json
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html_text or "", re.I | re.S):
        try:
            data = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop(0)
            if not isinstance(d, dict):
                continue
            t = d.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return d
            if isinstance(d.get("@graph"), list):
                stack.extend(d["@graph"])
    return None


def jsonld_salary_eur_year(d):
    """Salário anual em EUR a partir de baseSalary; mensal conta x14 (prática em Portugal). None se não der."""
    bs = (d or {}).get("baseSalary")
    if not isinstance(bs, dict) or str(bs.get("currency", "EUR")).upper() != "EUR":
        return None, None
    v = bs.get("value") if isinstance(bs.get("value"), dict) else bs
    unit = str(v.get("unitText", "")).upper()
    mult = {"YEAR": 1, "MONTH": 14, "WEEK": 52, "DAY": 220, "HOUR": 1760}.get(unit)
    if not mult:
        return None, None
    def num(x):
        try:
            return float(x) * mult
        except (TypeError, ValueError):
            return None
    lo, hi = num(v.get("minValue", v.get("value"))), num(v.get("maxValue", v.get("value")))
    return lo, hi


def apply_jsonld(job, d, today=None):
    """Preenche o Job com o JobPosting. Devolve None se a vaga já expirou (validThrough no passado)."""
    from datetime import date
    today = today or date.today()
    vt = iso(d.get("validThrough"))
    if vt and vt < today.isoformat():
        return None
    job.description = strip_html(d.get("description", "")) or job.description
    job.posted = job.posted or iso(d.get("datePosted"))
    loc = d.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = (loc or {}).get("address") if isinstance(loc, dict) else None
    if isinstance(addr, dict):
        job.location = job.location or ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressCountry")) if isinstance(x, str) and x)
    if d.get("jobLocationType") == "TELECOMMUTE":
        job.remote = True
    job.salary_min, job.salary_max = jsonld_salary_eur_year(d)
    job.unverified = False
    return job


FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters, "adzuna": fetch_adzuna, "remotive": fetch_remotive,
    "page_watch": fetch_page_watch,
    "workday": fetch_workday, "personio": fetch_personio, "teamtailor": fetch_teamtailor,
    "recruitee": fetch_recruitee,
}


def fetch_source(src, session, env):
    kind = src.get("type")
    if kind not in FETCHERS:
        raise SourceError(f"tipo de fonte desconhecido: {kind}")
    return FETCHERS[kind](src, session, env)


# ---------- detalhe (descrição completa + confirma que está aberta) ----------

def fetch_detail(job: Job, session):
    """Devolve o Job com descrição completa, ou None se a vaga já fechou (404/410)."""
    ex = job.extra or {}
    if "workable_account" in ex:
        r = http(session, "GET", f"https://apply.workable.com/api/v2/accounts/{ex['workable_account']}/jobs/{ex['shortcode']}")
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise SourceError(f"workable detalhe: HTTP {r.status_code}")
        d = r.json()
        job.description = strip_html(" ".join(d.get(k, "") or "" for k in ("description", "requirements", "benefits")))
        job.posted = job.posted or iso(d.get("published"))
    elif "sr_id" in ex:
        r = http(session, "GET", f"https://api.smartrecruiters.com/v1/companies/{ex['sr_company']}/postings/{ex['sr_id']}")
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise SourceError(f"smartrecruiters detalhe: HTTP {r.status_code}")
        secs = ((r.json().get("jobAd") or {}).get("sections") or {})
        job.description = strip_html(" ".join((s or {}).get("text", "") for s in secs.values()))
    elif "workday_detail" in ex:
        r = http(session, "GET", ex["workday_detail"], headers={"Accept": "application/json"})
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise SourceError(f"workday detalhe: HTTP {r.status_code}")
        info = (r.json().get("jobPostingInfo") or {})
        job.description = strip_html(info.get("jobDescription", ""))
        job.posted = job.posted or iso(info.get("startDate"))
        if info.get("canApply") is False:
            return None
    elif ex.get("jsonld"):
        r = http(session, "GET", job.url)
        if r.status_code in (404, 410):
            return None
        if r.status_code == 200:
            d = parse_jsonld_jobposting(r.text)
            if d:
                return apply_jsonld(job, d)
    return job


def needs_detail(job: Job) -> bool:
    ex = job.extra or {}
    return ("workable_account" in ex or "sr_id" in ex or "workday_detail" in ex or ex.get("jsonld")) and not job.description
