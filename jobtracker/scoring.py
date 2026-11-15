"""Filtros e pontuação. Tudo aqui é determinístico e testável sem rede."""
import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .models import Job

# ---------- utilitários de texto ----------

def strip_html(s: str) -> str:
    s = html.unescape(s or "")                      # Greenhouse manda HTML escapado
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


_YEARS = re.compile(
    r"(\d{1,2})\s*(?:\+|\s*(?:-|–|to|a|ou)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?|anos)\b", re.I)
_EXP = re.compile(r"experi[eê]nc|experience|background|track record", re.I)


def extract_years(text: str) -> Optional[int]:
    """Maior 'mínimo de anos' pedido junto a palavras de experiência.
    '3-5 years of experience' -> 3 ; '5+ years' -> 5 ; None se não encontrar."""
    best = None
    for m in _YEARS.finditer(text or ""):
        lo = int(m.group(1))
        if lo > 15:
            continue
        window = text[max(0, m.start() - 80): m.end() + 80]
        if not _EXP.search(window):
            continue
        best = lo if best is None else max(best, lo)
    return best


_LANG = r"(?:dutch|neerland[eê]s|german|alem[ãa]o|deutsch|french|franc[êe]s|fran[cç]ais)"
_HARD_LANG = [
    re.compile(rf"(?:fluent|fluency|native|flu[eê]ncia|nativo)(?:\s+(?:in|with|em))?\s+{_LANG}", re.I),
    re.compile(rf"{_LANG}\s*(?:\(|:|-)?\s*(?:fluent|fluency|native|mandatory|required|obrigat[óo]ri)", re.I),
]
_SOFT_LANG = re.compile(_LANG, re.I)


def language_check(text: str):
    """(bloqueia, aviso). Bloqueia só quando a língua é claramente exigida."""
    for rx in _HARD_LANG:
        m = rx.search(text or "")
        if m:
            return True, f"exige língua que não falas: «{m.group(0)}»"
    if _SOFT_LANG.search(text or ""):
        return False, "menciona holandês/alemão/francês: confirmar se é obrigatório"
    return False, ""


def parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


# ---------- veredicto ----------

@dataclass
class Verdict:
    include: bool
    score: int = 0
    reasons: list = field(default_factory=list)   # porque passou/pontuou
    flags: list = field(default_factory=list)     # riscos a confirmar
    excluded_by: str = ""


def _rx(patterns):
    return [re.compile(p, re.I) for p in patterns or []]


def cheap_filter(job: Job, cfg: dict, today: date) -> str:
    """Filtros baratos (antes de ir buscar o detalhe). Devolve motivo de exclusão ou ''."""
    title = job.title or ""
    if (job.extra or {}).get("excluded"):            # a própria fonte já disse porquê (ex.: BEP)
        return job.extra["excluded"]
    for rx in _rx(cfg.get("title_exclude")):
        if rx.search(title):
            return f"título excluído ({rx.pattern})"
    loc_cfg = cfg.get("locations", {})
    loc = (job.location or "").strip()
    if loc and loc_cfg.get("exclude") and re.search(loc_cfg["exclude"], loc, re.I):
        return f"local excluído ({loc})"
    if loc:
        allow = re.compile(loc_cfg.get("allow", "portugal|lisbo|porto"), re.I)
        if not allow.search(loc):
            is_remote = bool(job.remote) or re.search(r"remote|remoto|anywhere|worldwide", loc, re.I)
            remote_ok = re.compile(loc_cfg.get("remote_ok", "portugal|europe|emea|eu\\b|worldwide|anywhere"), re.I)
            bare_remote = re.fullmatch(r"\s*(remote|remoto)\s*", loc, re.I)
            if not (is_remote and (bare_remote or remote_ok.search(loc))):
                return f"local fora do alvo ({loc})"
    max_age = cfg.get("max_age_days", 14)
    d = parse_date(job.posted)
    if d and (today - d).days > max_age:
        return f"publicada há {(today - d).days} dias"
    return ""


def evaluate(job: Job, cfg: dict, today: date) -> Verdict:
    why = cheap_filter(job, cfg, today)
    if why:
        return Verdict(False, excluded_by=why)

    text = f"{job.title}\n{job.description}"
    v = Verdict(True)

    # 1) bloqueios duros
    block, lang_msg = language_check(text)
    if block:
        return Verdict(False, excluded_by=lang_msg)
    if lang_msg:
        v.flags.append(lang_msg)

    years = extract_years(job.description)
    ycfg = cfg.get("years", {})
    if years is not None:
        if years > ycfg.get("max", 6):
            return Verdict(False, excluded_by=f"pede {years}+ anos de experiência")
        if years < ycfg.get("min", 3):
            return Verdict(False, excluded_by=f"pede só {years} ano(s): provável nível júnior")
        if years >= ycfg.get("stretch_from", 5):
            v.flags.append(f"pede {years}+ anos: esticado para o teu perfil")
        v.reasons.append(f"experiência pedida: {years} anos")
    elif job.description:
        v.flags.append("não indica anos de experiência")

    floor = cfg.get("min_salary_eur")
    if floor and job.salary_max is not None and job.salary_max < floor:
        return Verdict(False, excluded_by=f"salário máximo {job.salary_max:.0f} abaixo de {floor}")

    # 2) pontuação
    kw = cfg.get("keywords", {})
    score = 0
    strong_pts = 0
    for pat in kw.get("strong", []):
        rx = re.compile(pat, re.I)
        if rx.search(text):
            strong_pts += 3
            if rx.search(job.title):
                strong_pts += 2
            v.reasons.append(f"+ {pat}")
    score += min(strong_pts, kw.get("strong_cap", 14))
    med = sum(1 for pat in kw.get("medium", []) if re.search(pat, text, re.I))
    score += min(med, kw.get("medium_cap", 4))
    neg = sum(1 for pat in kw.get("negative", []) if re.search(pat, text, re.I))
    score -= 3 * neg
    if neg:
        v.flags.append(f"{neg} sinal(is) negativo(s) (vendas/comissões/etc.)")

    if re.search(cfg.get("senior_title", r"senior|sénior|manager|lead|head|principal|director|diretor"),
                 job.title, re.I):
        score += cfg.get("senior_bonus", 2)
        v.reasons.append("cargo de nível acima (senior/manager/lead)")

    # entidades que interessam pelo tipo (associações, clusters, CoLABs…), sem as teres de enumerar
    org = cfg.get("org_bonus") or {}
    if org.get("pattern") and re.search(org["pattern"], f"{job.company}\n{job.title}", re.I):
        score += org.get("points", 3)
        v.reasons.append("entidade do tipo associação / cluster / CoLAB / centro tecnológico")

    # 3) avisos
    if job.salary_min or job.salary_max:
        v.reasons.append(f"salário indicado: {job.salary_min or '?'}–{job.salary_max or '?'}")
    elif (job.extra or {}).get("pay_text"):
        v.flags.append(f"remuneração: {job.extra['pay_text'][:80]}")
    else:
        v.flags.append("sem salário indicado")
    if job.partial:
        v.flags.append("descrição parcial: anos/línguas por confirmar")
    if job.unverified:
        v.flags.append("fonte não confirma se está aberta: abrir a página")
    if not job.location:
        v.flags.append("local não indicado")
    if (job.extra or {}).get("deadline"):
        v.flags.append(f"candidaturas até {job.extra['deadline']}")

    v.score = score
    floor_score = (job.extra or {}).get("min_score", cfg.get("min_score", 6))
    if score < floor_score:
        v.include = False
        v.excluded_by = f"pontuação {score} abaixo de {floor_score}"
    return v
