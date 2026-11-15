"""Adaptadores de fontes. Cada `fetch_*` devolve list[Job] ou levanta SourceError.
As funções `parse_*` são puras (recebem JSON/HTML já obtido) e estão cobertas por testes.

AVISO: os formatos das APIs abaixo foram escritos a partir da documentação pública
e NÃO foram chamados ao vivo durante o desenvolvimento (a rede do ambiente de
desenvolvimento estava bloqueada). A primeira execução real mostra, por fonte,
se respondeu; uma fonte avariada nunca derruba as outras.
"""
import html
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests

from . import logos

from .models import Job
from .scoring import strip_html

UA = {"User-Agent": "jobtracker-pedro/1.0 (uso pessoal)"}


class SourceError(Exception):
    pass


class SourceSkipped(Exception):
    """Fonte desligada de propósito (ex.: falta uma chave)."""


class JobList(list):
    """Lista de vagas com uma nota opcional (ex.: 'parcial: 2 pesquisas falharam') e, para fontes
    que leem por números sequenciais (BEP), o cursor a guardar no estado."""
    note = ""
    cursor = None


def with_errors(jobs, errors):
    out = JobList(jobs)
    if errors:
        out.note = f"parcial, {len(errors)} pesquisa(s) falharam ({errors[0]})"
    return out


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


def http(session, method, url, tries=4, **kw):
    kw.setdefault("timeout", 25)
    kw["headers"] = {**UA, **kw.get("headers", {})}
    last = None
    for i in range(tries):
        wait = 2 * (i + 1)
        try:
            r = session.request(method, url, **kw)
        except requests.RequestException as e:
            last = str(e)
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:           # limite de pedidos: espera mais (respeita Retry-After)
                    try:
                        wait = min(30, max(wait, int(r.headers.get("Retry-After", 0))))
                    except (ValueError, TypeError, AttributeError):
                        pass
                    wait = max(wait, 8 * (i + 1))
            else:
                return r
        if i < tries - 1:
            time.sleep(wait)
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


def parse_page_links(html_text, base_url, pattern, name, location="", url_pattern="", same_host=True):
    """Ligações da página que parecem vagas. pattern filtra o TEXTO da ligação; url_pattern filtra o
    endereço (ex.: '/job/'); same_host descarta redes sociais, rodapé e outros domínios."""
    p = _Links()
    p.feed(html_text)
    rx = re.compile(pattern, re.I)
    urx = re.compile(url_pattern, re.I) if url_pattern else None
    host = urlsplit(base_url).netloc.lower().removeprefix("www.")
    seen, jobs = set(), []
    for href, text in p.links:
        if not (8 <= len(text) <= 160) or not rx.search(text):
            continue
        url = clean_url(urljoin(base_url, href))
        if same_host and urlsplit(url).netloc.lower().removeprefix("www.") != host:
            continue
        if urx and not urx.search(url):
            continue
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
    jobs = parse_page_links(r.text, src["url"], src.get("link_regex", "."), src["name"], src.get("location", ""),
                            src.get("url_regex", ""), src.get("same_host", True))
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
    host = src.get("host") or f"{c}.recruitee.com"          # domínio próprio (ex.: careers.unu.edu)
    data = get_json(session, "GET", f"https://{host}/api/offers/")
    if src.get("country"):                                  # só ofertas desse país (código ISO, ex.: PT)
        data = {"offers": [o for o in (data or {}).get("offers", []) or []
                           if (o.get("country_code") or "").upper() == src["country"].upper()]}
    return parse_recruitee(data, c, src.get("name"))


# ---------- RSS genérico (ex.: ReliefWeb, lista de vagas sem local fixo) ----------

def parse_rss_jobs(xml_text, src):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SourceError("RSS inválido")
    jobs = []
    for it in root.iter("item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        try:
            posted = parsedate_to_datetime(it.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            posted = ""
        desc = strip_html(it.findtext("description") or "")
        raw = it.findtext("description") or ""
        org = ""                                       # "Organization: X" num bloco do HTML da descrição
        parts = [x.strip() for x in re.split(r"<[^>]+>", html.unescape(raw)) if x.strip()]
        for k, part in enumerate(parts):
            m = re.match(r"(?:Organi[sz]ation|Organiza[çc][ãa]o|Source)\s*:?\s*(.*)$", part, re.I)
            if m:
                org = (m.group(1) or (parts[k + 1] if k + 1 < len(parts) else "")).strip()[:80]
                break
        jobs.append(Job(source=f"rss:{src['name']}", company=org or src.get("company") or src["name"], title=title,
                        url=clean_url(link), location=src.get("location", ""), description=desc, posted=posted,
                        remote=True if src.get("remote") else None))
    return jobs


def fetch_rss(src, session, env):
    r = http(session, "GET", src["url"])
    if r.status_code != 200:
        raise SourceError(f"{urlsplit(src['url']).netloc}: HTTP {r.status_code}")
    return parse_rss_jobs(r.text, src)


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


# ---------- LinkedIn (pesquisa pública, sem login: cobre todas as empresas) ----------
# Usa as páginas "guest" que o LinkedIn serve a quem não tem sessão (as mesmas que o Google indexa).
# Não usa a tua conta, por isso não há risco de a conta ser restringida. O LinkedIn pode limitar
# pedidos a partir dos servidores do GitHub (HTTP 429): nesse caso fica com o que já recolheu.

LI_BASE = "https://www.linkedin.com"
LI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}
LI_EMPTY = "<!DOCTYPE html>\n\n<!---->"


def _soup(html_text):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html_text or "", "html.parser")


def parse_linkedin_cards(html_text):
    """Cartões da página de resultados -> list[Job] (sem descrição; vai-se buscar no detalhe)."""
    jobs = []
    for card in _soup(html_text).find_all(class_="base-search-card"):
        jid = str(card.get("data-entity-urn", "")).split(":")[-1]
        if not jid.isdigit():
            a = card.find("a", class_="base-card__full-link", href=True)
            m = re.search(r"-(\d{6,})(?:\?|/|$)", a["href"]) if a else None
            jid = m.group(1) if m else ""
        t = card.find(class_="base-search-card__title")
        c = card.find(class_="base-search-card__subtitle")
        if not jid or t is None:
            continue
        loc = card.find(class_="job-search-card__location")
        tm = card.find("time")
        img = card.find("img")
        logo = ""
        if img is not None:
            logo = img.get("data-delayed-url") or img.get("src") or ""
            if not logo.startswith("https://") or "ghost" in logo:
                logo = ""
        title = t.get_text(" ", strip=True)
        where = loc.get_text(" ", strip=True) if loc else ""
        jobs.append(Job(
            source="linkedin", company=c.get_text(" ", strip=True) if c else "", title=title,
            url=f"{LI_BASE}/jobs/view/{jid}", location=where,
            posted=iso(tm.get("datetime")) if tm else "", logo=logo,
            remote=bool(re.search(r"remot|teletrabalho", f"{title} {where}", re.I)) or None,
            extra={"linkedin_id": jid}))
    return jobs


def parse_linkedin_detail(html_text):
    """(descrição, fechada?, nível) a partir da página da vaga."""
    soup = _soup(html_text)
    closed = bool(soup.find(class_=re.compile("closed-job")) or
                  re.search(r"No longer accepting applications|Já não aceita candidaturas", html_text or "", re.I))
    div = soup.find(class_="show-more-less-html__markup") or soup.find(class_="description__text")
    desc = div.get_text(" ", strip=True) if div else ""
    level = ""
    for li in soup.find_all(class_="description__job-criteria-item"):
        h = li.find(class_="description__job-criteria-subheader")
        v = li.find(class_="description__job-criteria-text")
        if h and v and re.search(r"seniority|antiguidade|nível", h.get_text(), re.I):
            level = v.get_text(" ", strip=True)
    return " ".join(desc.split()), closed, level


def fetch_linkedin(src, session, env, sleep=time.sleep):
    queries = src.get("queries") or [src.get("search", "")]
    hours = int(src.get("hours", 72))
    max_per_query = int(src.get("max_per_query", 50))
    out, seen, errors = [], set(), []
    for qi, q in enumerate(queries):
        start = 0
        while start < max_per_query:
            if qi or start:
                sleep(float(src.get("delay", 2.0)))
            params = {"keywords": q, "location": src.get("location", "Portugal"),
                      "f_TPR": f"r{hours * 3600}", "start": start}
            if src.get("geo_id"):
                params["geoId"] = src["geo_id"]
            if src.get("remote"):
                params["f_WT"] = "2"             # só vagas 100% remotas
            try:
                r = http(session, "GET", f"{LI_BASE}/jobs-guest/jobs/api/seeMoreJobPostings/search",
                         params=params, headers=LI_HEADERS, tries=2)
            except SourceError as e:
                errors.append(f"«{q}»: {e}")
                break
            if r.status_code != 200:
                errors.append(f"«{q}»: HTTP {r.status_code}")
                break
            page = parse_linkedin_cards(r.text)
            if not page:
                if r.text.strip() and r.text.strip() != LI_EMPTY.strip() and not start:
                    errors.append(f"«{q}»: página sem vagas (bloqueio?)")
                break
            fresh = [j for j in page if j.url not in seen]
            if src.get("remote"):
                for j in fresh:
                    j.remote = True
            if src.get("_company_rx"):      # pesquisa por empresa: fica só com vagas dessas empresas
                for j in fresh:
                    seen.add(j.url)
                n_page, fresh = len(fresh), [j for j in fresh if re.search(src["_company_rx"], j.company or "", re.I)]
                out += fresh
                if not n_page or len(page) < 10:
                    break
                start += len(page)
                continue
            for j in fresh:
                seen.add(j.url)
            out += fresh
            if not fresh or len(page) < 10:
                break
            start += len(page)
        # bloqueado logo à cabeça em 2 pesquisas seguidas: pára para não piorar o limite
        if len(errors) >= 2 and not out:
            break
    if errors and not out:
        raise SourceError("; ".join(errors[:3]))
    return with_errors(out, errors)


def fetch_linkedin_detail(job, session, sleep=time.sleep):
    jid = job.extra["linkedin_id"]
    sleep(1.5)                                   # ritmo de pessoa, para não levar com limites
    r = http(session, "GET", f"{LI_BASE}/jobs-guest/jobs/api/jobPosting/{jid}", headers=LI_HEADERS, tries=2)
    if r.status_code == 404:                     # confirma na página normal antes de dar como fechada
        r = http(session, "GET", f"{LI_BASE}/jobs/view/{jid}", headers=LI_HEADERS, tries=2)
        if r.status_code in (404, 410):
            return None
    if r.status_code != 200:
        raise SourceError(f"linkedin detalhe: HTTP {r.status_code}")
    desc, closed, level = parse_linkedin_detail(r.text)
    if closed:
        return None
    if not desc:
        raise SourceError("linkedin detalhe: sem descrição (bloqueio ou página mudou)")
    job.description = desc
    if level:
        job.extra["level"] = level
    return job


# ---------- Indeed Portugal (via biblioteca JobSpy, que acompanha as mudanças do Indeed) ----------

def _annual_eur(amount, interval, currency):
    if amount is None or (currency and str(currency).upper() != "EUR"):
        return None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    if amount != amount:                          # NaN
        return None
    mult = {"yearly": 1, "monthly": 14, "weekly": 52, "daily": 220, "hourly": 1760}.get(str(interval or "yearly"), None)
    return amount * mult if mult else None


def parse_jobspy_rows(rows, source="indeed"):
    """rows: list[dict] (DataFrame.to_dict('records') do JobSpy) -> list[Job]."""
    def val(r, k):
        v = r.get(k)
        if v is None:
            return None
        try:
            if v != v:                            # NaN do pandas
                return None
        except Exception:
            pass
        return v
    jobs = []
    for r in rows:
        url, title = val(r, "job_url"), val(r, "title")
        if not url or not title:
            continue
        iv, cur = val(r, "interval"), val(r, "currency")
        jobs.append(Job(
            source=source, company=str(val(r, "company") or ""), title=str(title),
            url=clean_url(str(url)), location=str(val(r, "location") or ""),
            description=strip_html(str(val(r, "description") or "")),
            posted=iso(str(val(r, "date_posted") or "")),
            salary_min=_annual_eur(val(r, "min_amount"), iv, cur),
            salary_max=_annual_eur(val(r, "max_amount"), iv, cur),
            remote=bool(val(r, "is_remote")) or None,
            logo=str(val(r, "company_logo") or "") if str(val(r, "company_logo") or "").startswith("https://") else "",
            domain=logos.domain_of(str(val(r, "company_url_direct") or ""))))
    return jobs


def fetch_indeed(src, session, env):
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        raise SourceSkipped(f"biblioteca python-jobspy não instalada ({e})")
    out, seen, errors = [], set(), []
    for q in src.get("queries", []):
        try:
            df = scrape_jobs(site_name=["indeed"], search_term=q, country_indeed=src.get("country", "portugal"),
                             location=src.get("location") or None, hours_old=int(src.get("hours", 72)),
                             results_wanted=int(src.get("max_per_query", 50)), description_format="markdown",
                             verbose=0)
        except Exception as e:
            errors.append(f"«{q}»: {e}")
            continue
        for j in parse_jobspy_rows(df.to_dict("records") if df is not None else [], "indeed"):
            if j.url not in seen:
                seen.add(j.url)
                out.append(j)
    if errors and not out:
        raise SourceError("; ".join(errors[:3]))
    return with_errors(out, errors)


# ---------- BEP — Bolsa de Emprego Público (administração central, CCDR, institutos, autarquias) ----------
# Não há API nem RSS. Cada oferta tem um número sequencial (Oferta_Detalhes.aspx?CodOferta=N) e um
# número inexistente redireciona para a página de erro. O tracker lê os números novos desde o último
# visto (guardado em state.json) e pára ao fim de várias falhas seguidas.

BEP_DETAIL = "https://www.bep.gov.pt/pages/oferta/Oferta_Detalhes.aspx?CodOferta={}"
_BEP_ID = "ctl00_ctl00_FormMasterContentPlaceHolder_ContentPlaceHolder1_"
ISLANDS = re.compile(r"\bRA[AM]\b|madeira|porto santo|a[çc]ores|azores|funchal|ponta delgada|angra", re.I)


def parse_bep_detail(html_text, cod, today=None):
    """Devolve dict com os campos da oferta, ou None se o número não existir."""
    from datetime import date
    soup = _soup(html_text)
    def f(name):
        el = soup.find(id=_BEP_ID + name)
        return " ".join(el.get_text(" ", strip=True).split()) if el else ""
    code = f("lblNOCodigo")
    if not code:
        return None
    first_row = []
    tbl = soup.find(id=_BEP_ID + "tblLocaisTrabalho")
    if tbl:
        rows = tbl.find_all("tr")
        if len(rows) > 1:
            first_row = [" ".join(td.get_text(" ", strip=True).split()) for td in rows[1].find_all("td")]
    distrito = first_row[5] if len(first_row) > 5 else ""
    concelho = first_row[6] if len(first_row) > 6 else (first_row[3] if len(first_row) > 3 else "")
    posto = f("lblNOCarPosto")
    return {"code": code, "tipo": f("lblNOTipoOferta"), "estado": f("lblNOEstado"), "nivel": f("lblNONivelOrganico"),
            "organismo": f("lblNOOrganismo"), "carreira": f("lblNOCarreira"), "categoria": f("lblNOCategoria"),
            "remuneracao": f("lblNORemuneracao"), "posto": posto, "habilitacao": f("lblNOHabLit"),
            "requisitos": f("lblNOOutrosReqs"), "publicada": iso(f("lblNODataPub")), "limite": iso(f("lblNODataLim")),
            "distrito": distrito, "concelho": concelho, "url": BEP_DETAIL.format(cod)}


def bep_to_job(d, src, today=None):
    from datetime import date
    today = today or date.today()
    headline = re.split(r"(?<=[.;:])\s|\s{2,}| Caracteriza| Descri[çc][ãa]o| Desempenho", d["posto"])[0][:90].strip(" .;:-–")
    title = d["carreira"] or d["categoria"] or "Oferta BEP"
    if headline and headline.lower() != title.lower():
        title = f"{title} — {headline}"
    where = ", ".join(x for x in (d["concelho"], d["distrito"]) if x)
    excluded = ""
    if ISLANDS.search(where):
        excluded = f"fora do continente ({where})"
    elif d["estado"] and not re.search(r"ativ", d["estado"], re.I):
        excluded = f"oferta {d['estado'].lower()}"
    elif d["limite"] and d["limite"] < today.isoformat():
        excluded = f"prazo terminou ({d['limite']})"
    elif src.get("exclude_types") and re.search(src["exclude_types"], d["tipo"], re.I):
        excluded = f"tipo excluído ({d['tipo']})"
    elif src.get("careers") and not re.search(src["careers"], f"{d['carreira']} {d['categoria']} {d['tipo']}", re.I):
        excluded = f"carreira fora do alvo ({d['carreira']})"
    desc = " ".join(x for x in (d["posto"], d["requisitos"], f"Habilitação: {d['habilitacao']}",
                                f"Organismo: {d['organismo']} ({d['nivel']})", f"Tipo: {d['tipo']}") if x)
    extra = {"pay_text": d["remuneracao"], "deadline": d["limite"], "bep_code": d["code"]}
    if excluded:
        extra["excluded"] = excluded
    if src.get("min_score") is not None:
        extra["min_score"] = src["min_score"]
    return Job(source="bep", company=d["organismo"] or d["nivel"], title=title, url=d["url"],
               location=f"{where}, Portugal" if where else "Portugal", description=desc,
               posted=d["publicada"], extra=extra)


def fetch_bep(src, session, env, sleep=time.sleep):
    known = src.get("_known") or set()
    cursor = src.get("_cursor") or {}
    last = int(cursor.get("last") or src.get("start_id", 0))
    if not last:
        raise SourceError("falta start_id (um número CodOferta recente) no config.yaml")
    lookback, max_misses, max_pages = int(src.get("lookback", 40)), int(src.get("max_misses", 40)), int(src.get("max_pages", 250))
    out, misses, pages, top, errors = [], 0, 0, last, []
    cod = last - lookback
    while pages < max_pages:
        cod += 1
        if cod > top and misses >= max_misses:
            break
        url = BEP_DETAIL.format(cod)
        if url in known:
            continue
        pages += 1
        if pages > 1:
            sleep(float(src.get("delay", 0.4)))
        try:
            r = http(session, "GET", url, tries=2)
        except SourceError as e:
            errors.append(str(e))
            if len(errors) >= 5:
                break
            continue
        final = str(getattr(r, "url", "") or "")
        d = parse_bep_detail(r.text, cod) if r.status_code == 200 and "Erro" not in final else None
        if d is None:
            if cod > top:
                misses += 1
            continue
        misses = 0
        top = max(top, cod)
        out.append(bep_to_job(d, src))
    if errors and not out and len(errors) >= 5:
        raise SourceError("; ".join(errors[:3]))
    res = with_errors(out, errors)
    res.cursor = {"last": top}
    return res



# ---------- Plataforma Portuguesa das ONGD (lista de oportunidades) ----------
ONGD_URL = "https://www.plataformaongd.pt/oportunidades-de-emprego-e-voluntariado"


def parse_ongd(html_text, base_url=ONGD_URL):
    """Cada vaga é um bloco .articleFull: título (h3), entidade, local, prazo e ligação para os termos de referência."""
    out = []
    for b in _soup(html_text).select(".articleFull"):
        h = b.find("h3")
        a = b.find("a", class_="button") or b.find("a", href=True)
        if not h or not a:
            continue
        info = b.find("p").get_text("\n", strip=True) if b.find("p") else ""
        field = lambda k: (re.search(rf"{k}:\s*\n?\s*([^\n]+)", info, re.I) or [None, ""])[1].strip()
        org, loc, due = field("Entidade"), field("Local"), field("Prazo de candidatura")
        url = clean_url(urljoin(base_url, a["href"]))
        desc = (f"Oportunidade publicada na Plataforma Portuguesa das ONGD (organizações não governamentais para o "
                f"desenvolvimento). Entidade: {org}. Local: {loc or 'não indicado'}. Prazo de candidatura: {due or 'não indicado'}.")
        out.append(Job(source="ongd", company=org or "ONGD", title=h.get_text(" ", strip=True), url=url,
                       location=loc if loc and not re.fullmatch(r"v[áa]rios|diversos", loc, re.I) else "Portugal",
                       description=desc, unverified=True))
    return out


def fetch_ongd(src, session, env):
    url = src.get("url", ONGD_URL)
    r = http(session, "GET", url)
    if r.status_code != 200:
        raise SourceError(f"plataformaongd.pt: HTTP {r.status_code}")
    return parse_ongd(r.text, url)


# ---------- Agências europeias em Lisboa (EUDA, EMSA): tabelas de vagas ----------
EU_CLOSED = re.compile(r"closed|under assessment|cancel|completed|suspended|reserve list", re.I)


def _eu_job(src, title, url, extra_text):
    name = src.get("company") or src["name"]
    desc = f"Vaga numa agência da União Europeia com sede em Lisboa ({name}). {extra_text}"
    return Job(source=f"eu:{name}", company=name, title=title, url=url, location="Lisboa, Portugal",
               description=desc, unverified=True)


def parse_euda(html_text, src, base_url):
    """Tabela de calls: Status | Vacancy | Reference | Contract type | Closing date | Grade. Só as que aceitam candidaturas."""
    out = []
    for tr in _soup(html_text).select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        status, a = tds[0].get_text(" ", strip=True), tds[1].find("a", href=True)
        if not a or EU_CLOSED.search(status):
            continue
        extra = f"Tipo de contrato: {tds[3].get_text(' ', strip=True)}. Prazo: {tds[4].get_text(' ', strip=True)}."
        out.append(_eu_job(src, a.get_text(" ", strip=True), clean_url(urljoin(base_url, a["href"])), extra))
    return out


def parse_emsa(html_text, src, base_url):
    """Tabela: Vacancy (código com ligação) | Description (título) | Published | Deadline."""
    out = []
    for tr in _soup(html_text).select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[0].find("a", href=True)
        title = re.sub(r"\s*Deadline extended\s*", " ", tds[1].get_text(" ", strip=True)).strip()
        deadline = tds[3].get_text(" ", strip=True)
        if not a or "/jobs/vacancies/item/" not in a["href"] or not deadline or re.search(r"\bnotice\b", title, re.I):
            continue
        extra = f"Referência: {a.get_text(' ', strip=True)}. Publicada: {tds[2].get_text(' ', strip=True)}. Prazo: {deadline}."
        out.append(_eu_job(src, title, clean_url(urljoin(base_url, a["href"])), extra))
    return out


def fetch_eu_agency(src, session, env):
    r = http(session, "GET", src["url"])
    if r.status_code != 200:
        raise SourceError(f"{urlsplit(src['url']).netloc}: HTTP {r.status_code}")
    parser = {"euda": parse_euda, "emsa": parse_emsa}[src["layout"]]
    if "<table" not in r.text:
        raise SourceError("página sem tabela de vagas: pode ter mudado")
    return parser(r.text, src, src["url"])           # 0 vagas abertas é normal nestas agências

FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters, "adzuna": fetch_adzuna, "remotive": fetch_remotive,
    "page_watch": fetch_page_watch,
    "workday": fetch_workday, "personio": fetch_personio, "teamtailor": fetch_teamtailor,
    "recruitee": fetch_recruitee, "linkedin": fetch_linkedin, "indeed": fetch_indeed,
    "bep": fetch_bep, "ongd": fetch_ongd, "eu_agency": fetch_eu_agency, "rss": fetch_rss,
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
    if "linkedin_id" in ex:
        return fetch_linkedin_detail(job, session)
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
    return ("linkedin_id" in ex or "workable_account" in ex or "sr_id" in ex or "workday_detail" in ex or ex.get("jsonld")) and not job.description
