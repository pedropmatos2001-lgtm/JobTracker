"""Filtros e pontuação. Tudo aqui é determinístico e testável sem rede."""
import html
import re
import unicodedata
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
    themes: list = field(default_factory=list)    # temas fortes que a vaga cumpre (rótulos legíveis)
    excluded_by: str = ""


def pat_of(x):
    """Um tema pode ser só o padrão ou {label: "nome legível", pattern: "..."}."""
    return x["pattern"] if isinstance(x, dict) else x


def label_of(x):
    return x.get("label") if isinstance(x, dict) else None


def _rx(patterns):
    return [re.compile(p, re.I) for p in patterns or []]


def company_rx(c):
    """Regex de uma empresa-alvo: o `match` dado, ou o nome com acentos e hífenes tolerantes."""
    if isinstance(c, dict) and c.get("match"):
        return c["match"]
    name = c["name"] if isinstance(c, dict) else c
    out = []
    for ch in name:
        base = unicodedata.normalize("NFD", ch)[0]
        if ch in " -":
            out.append(r"[\s-]?")
        elif base != ch:
            out.append(f"[{re.escape(ch)}{re.escape(base)}]")
        else:
            out.append(re.escape(ch))
    return r"\b" + "".join(out) + r"\b"


def target_companies(cfg):
    """[(nome, setor, regex)] das empresas-alvo do perfil."""
    out = []
    for g in cfg.get("target_companies") or []:
        for c in g.get("companies") or []:
            out.append((c["name"] if isinstance(c, dict) else c, g.get("sector", ""), company_rx(c)))
    return out


def target_company(job, cfg):
    for name, _, rx in target_companies(cfg):
        if re.search(rx, job.company or "", re.I):
            return name
    return ""


def cheap_filter(job: Job, cfg: dict, today: date) -> str:
    """Filtros baratos (antes de ir buscar o detalhe). Devolve motivo de exclusão ou ''."""
    title = job.title or ""
    if (job.extra or {}).get("excluded"):            # a própria fonte já disse porquê (ex.: BEP)
        return job.extra["excluded"]
    if cfg.get("company_exclude") and re.search(cfg["company_exclude"], job.company or "", re.I):
        return "empresa excluída deste perfil"
    for rx in _rx(cfg.get("title_exclude")):
        if rx.search(title):
            return f"título excluído ({rx.pattern})"
    loc_cfg = cfg.get("locations", {})
    loc = (job.location or "").strip()
    if loc and loc_cfg.get("exclude") and re.search(loc_cfg["exclude"], loc, re.I):
        return f"local excluído ({loc})"
    if loc:
        allow = re.compile(loc_cfg.get("allow", "portugal|lisbo|porto"), re.I)
        top_only = loc_cfg.get("top_only")         # ex.: Bruxelas, só para vagas com temas de topo (ver evaluate)
        if not allow.search(loc) and not (top_only and re.search(top_only, loc, re.I)):
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


# Frases-padrão dos anúncios (igualdade de oportunidades, diversidade, privacidade) não dizem nada sobre o
# trabalho e fazem disparar temas como "igualdade de género" em quase todas as vagas. Saem antes de procurar temas.
BOILERPLATE = re.compile(
    r"equal (employment )?opportunit|without regard to|regardless of|irrespective of|(do|does|will) not discriminate|"
    r"n[ãa]o discrimina|value diversity|diverse workforce|diversity,? (equity )?(and|&) inclusion|diversidade e inclus|"
    r"inclusive (workplace|culture|environment)|we('| a)re committed to|we celebrate|we welcome applications|"
    r"reasonable accommodation|veteran|disabilit|igualdade de oportunidades|independentemente d|sem discrimina|"
    r"diversidade|inclus(ivo|iva|ão) (e|no) (ambiente|local)|privacy|privacidade|personal data|dados pessoais|"
    r"benefits (package|include)|employee benefits|(pacote|plano) de benef[íi]cios|benef[íi]cios (sociais|flex[íi]veis|de sa[úu]de|"
    r"competitivos|para colaboradores)|health insurance|seguro de sa[úu]de|what we offer|o que oferecemos|we offer( you)?\s*[:\-]|"
    r"oferecemos\s*[:\-]|we offer (you )?(a |an )?(competitive|attractive|flexible|hybrid|great)|oferecemos (um |uma )?(pacote|sal[áa]rio|"
    r"remunera|seguro|forma[çc][ãa]o|integra[çc][ãa]o|possibilidade|contrato|progress|regime|hor[áa]rio)|parental leave|licen[çc]a parental|"
    r"cofinanciad|co-financiad|co-funded by|cofinanced", re.I)


def job_text_for_themes(job):
    parts = re.split(r"(?<=[.!?])\s+|\n+", job.description or "")
    return f"{job.title}\n" + " ".join(x for x in parts if not BOILERPLATE.search(x))


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
    theme_text = job_text_for_themes(job)
    n_themes, title_theme, deep_theme = 0, False, False
    for item in kw.get("strong", []):
        pat = pat_of(item)
        rx = re.compile(pat, re.I)
        # `title_pattern` (opcional): palavras que no título bastam para o tema, mas no texto seriam genéricas
        # ("Engenheiro do Ambiente", "Innovation Consultant Environment"), sem as procurar na descrição.
        trx = re.compile(item["title_pattern"], re.I) if isinstance(item, dict) and item.get("title_pattern") else None
        in_title = bool(rx.search(job.title) or (trx and trx.search(job.title)))
        if rx.search(theme_text) or in_title:
            pts = item.get("points", 3) if isinstance(item, dict) else 3     # temas prioritários valem mais
            strong_pts += pts
            n_themes += 1
            if len({m.group(0).lower() for m in rx.finditer(theme_text)}) >= 2:
                deep_theme = True                     # o mesmo tema com várias provas diferentes
            if in_title:
                strong_pts += 2
                title_theme = True
            v.reasons.append(f"+ {label_of(item) or pat}")
            if label_of(item):
                v.themes.append(label_of(item))
    # Funções genéricas (ex.: business analyst) contam como tema quando a empresa é uma das empresas-alvo
    # (consultoras): o título diz o papel, a empresa diz o contexto.
    role_rx = cfg.get("target_role_titles")
    if role_rx and not title_theme and target_company(job, cfg) and re.search(role_rx, job.title or "", re.I):
        strong_pts += 3
        n_themes += 1
        title_theme = True
        v.reasons.append("+ função de consultoria numa consultora-alvo")
        v.themes.append(cfg.get("target_role_label", "Consultoria"))
    score += min(strong_pts, kw.get("strong_cap", 14))
    med = sum(1 for pat in kw.get("medium", []) if re.search(pat_of(pat), text, re.I))
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
        score += org.get("points", 5)
        v.reasons.append("entidade do tipo associação / cluster / CoLAB / centro tecnológico")
    sec = cfg.get("sector_bonus") or {}
    tc = target_company(job, cfg)
    if tc:
        score += sec.get("points", 4)
        v.reasons.append(f"empresa-alvo ({tc})")
    elif sec.get("pattern") and re.search(sec["pattern"], f"{job.company}\n{job.title}", re.I):
        score += sec.get("points", 4)
        v.reasons.append("empresa/setor preferido (utilities, energia, água, resíduos)")

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
    if cfg.get("require_theme") and not strong_pts:
        v.include = False
        v.excluded_by = f"sem nenhum dos temas do perfil (pontuação {score})"
        return v
    # Foco: um tema solto numa frase do anúncio (rodapé "cofinanciado por…", "foresight", "assistance") não chega.
    # A vaga tem de ter um tema no título ou pelo menos dois temas diferentes na descrição.
    # Local de exceção (ex.: Bruxelas): só passa se a vaga tiver um dos temas de topo.
    loc_cfg = cfg.get("locations", {})
    top_only, loc = loc_cfg.get("top_only"), (job.location or "")
    if top_only and re.search(top_only, loc, re.I) and not re.search(loc_cfg.get("allow", "portugal"), loc, re.I):
        if not set(v.themes) & set(loc_cfg.get("top_themes") or []):
            v.include = False
            v.excluded_by = f"local fora do alvo ({loc}): aqui só vagas de topo"
            return v
        v.flags.append(f"fora de Portugal: {loc}")
    if cfg.get("theme_focus") and not title_theme and n_themes < 2 and not deep_theme:
        v.include = False
        v.excluded_by = "só um tema, e não no título: a vaga não é sobre isso"
        return v
    floor_score = (job.extra or {}).get("min_score", cfg.get("min_score", 6))
    if score < floor_score:
        v.include = False
        v.excluded_by = f"pontuação {score} abaixo de {floor_score}"
    return v
