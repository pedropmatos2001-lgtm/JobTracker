from datetime import date

import pytest
import yaml

from jobtracker.models import Job
from jobtracker.scoring import evaluate, extract_years, language_check, strip_html

TODAY = date(2026, 11, 18)


@pytest.fixture
def cfg():
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def job(**kw):
    base = dict(source="t", company="X", title="AI Transformation Consultant",
                url="https://x/1", location="Lisbon, Portugal", posted="2026-11-15",
                description="3+ years of experience in digital transformation. Process re-engineering "
                            "and agentic AI. Stakeholder management and requirements gathering.")
    base.update(kw)
    return Job(**base)


# ---- anos de experiência ----
@pytest.mark.parametrize("text,expected", [
    ("3+ years of experience in consulting", 3),
    ("5+ years in program management, experience with AI", 5),
    ("3-5 years of professional experience", 3),
    ("Experiência mínima de 8 anos em ESG", 8),
    ("We have 25 years of company history", None),           # sem palavra de experiência
    ("2 years of English; 6+ years of experience in strategy", 6),
    ("Great culture and team", None),
])
def test_extract_years(text, expected):
    assert extract_years(text) == expected


# ---- línguas ----
def test_language_blocks_fluent_dutch():
    block, msg = language_check("Fluent Dutch, with good French and English")
    assert block and "Dutch" in msg


def test_language_french_plus_is_only_a_flag():
    block, msg = language_check("Fluent English; French is a plus")
    assert not block and msg


def test_language_clean_text():
    assert language_check("Fluent English and Portuguese") == (False, "")


def test_language_portuguese_blockers():
    assert language_check("Exige fluência em alemão")[0]
    assert language_check("Francês obrigatório")[0]


# ---- veredicto ----
def test_good_job_passes(cfg):
    v = evaluate(job(), cfg, TODAY)
    assert v.include and v.score >= cfg["min_score"]
    assert any("sem salário" in f for f in v.flags)


@pytest.mark.parametrize("kw,fragment", [
    (dict(title="Software Engineer - AI"), "título"),
    (dict(title="Junior ESG Analyst"), "título"),
    (dict(location="Berlin, Germany"), "local"),
    (dict(location="Remote - USA"), "local"),
    (dict(posted="2026-09-01"), "dias"),
    (dict(description="8+ years of experience in ESG reporting."), "anos"),
    (dict(description="1-2 years of experience in sustainability."), "júnior"),
    (dict(description="Fluent Dutch required. 4 years of experience in AI transformation."), "língua"),
    (dict(salary_max=30000), "salário"),
])
def test_exclusions(cfg, kw, fragment):
    v = evaluate(job(**kw), cfg, TODAY)
    assert not v.include and fragment in v.excluded_by


def test_remote_europe_is_ok(cfg):
    assert evaluate(job(location="Remote - Europe", remote=True), cfg, TODAY).include


def test_bare_remote_is_ok(cfg):
    assert evaluate(job(location="Remote", remote=True), cfg, TODAY).include


def test_stretch_flag_for_5_years(cfg):
    v = evaluate(job(description="5+ years of experience. AI transformation, process re-engineering, ESG."),
                 cfg, TODAY)
    assert v.include and any("esticado" in f for f in v.flags)


def test_senior_title_gets_bonus(cfg):
    a = evaluate(job(), cfg, TODAY).score
    b = evaluate(job(title="Senior AI Transformation Consultant"), cfg, TODAY).score
    assert b == a + cfg["senior_bonus"]


def test_unrelated_job_scores_low(cfg):
    v = evaluate(job(title="Office Manager", description="3+ years of experience. Admin tasks."), cfg, TODAY)
    assert not v.include and "pontuação" in v.excluded_by


def test_salary_floor_keeps_unknown_and_high(cfg):
    assert evaluate(job(salary_max=45000), cfg, TODAY).include
    assert evaluate(job(), cfg, TODAY).include            # sem salário: passa, com aviso


def test_real_world_cases_from_fixture(cfg):
    """Casos reais: a Siemens Energy tem de passar (era a vaga mais próxima do perfil),
    e as quatro com problema concreto têm de ser excluídas pelo motivo certo."""
    import json
    jobs = {d["company"]: Job(**d) for d in json.load(open("tests/fixtures/sample_jobs.json", encoding="utf-8"))}
    today = date(2026, 11, 15)
    assert evaluate(jobs["Siemens Energy"], cfg, today).include
    assert evaluate(jobs["PNO"], cfg, today).include
    assert "língua" in evaluate(jobs["Nodis Consulting"], cfg, today).excluded_by
    assert "júnior" in evaluate(jobs["Navigator"], cfg, today).excluded_by
    assert "anos" in evaluate(jobs["Brisa"], cfg, today).excluded_by
    assert "dias" in evaluate(jobs["Greenvolt"], cfg, today).excluded_by


def test_partial_and_unverified_flags(cfg):
    v = evaluate(job(partial=True, unverified=True), cfg, TODAY)
    assert any("parcial" in f for f in v.flags) and any("não confirma" in f for f in v.flags)


def test_strip_html_handles_escaped_markup():
    assert strip_html("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"


def test_islands_excluded_even_if_portugal():
    import yaml
    from datetime import date
    from jobtracker.models import Job
    from jobtracker.scoring import cheap_filter
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    j = Job(source="linkedin", company="X", title="ESG Manager", url="u", location="Funchal, Madeira, Portugal")
    assert "local excluído" in cheap_filter(j, cfg, date(2026, 11, 15))
    j.location = "Ponta Delgada, Azores, Portugal"
    assert "local excluído" in cheap_filter(j, cfg, date(2026, 11, 15))
    j.location = "Évora, Portugal"
    assert cheap_filter(j, cfg, date(2026, 11, 15)) == ""


def test_bep_ccdr_offer_passes_with_one_strong_theme():
    import yaml
    from datetime import date
    from jobtracker import sources
    from jobtracker.scoring import evaluate
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    src = next(s for s in cfg["sources"] if s["type"] == "bep")
    with open("tests/fixtures/bep_detail.html", encoding="utf-8") as f:
        d = sources.parse_bep_detail(f.read(), 1)
    v = evaluate(sources.bep_to_job(d, src, today=date(2026, 11, 15)), cfg, date(2026, 11, 15))
    assert v.include and any("remuneração" in x for x in v.flags) and any("candidaturas até" in x for x in v.flags)


def test_associations_and_clusters_get_bonus():
    import yaml
    from datetime import date
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    desc = "Gestão de projetos europeus Horizon Europe. 4 anos de experiência."
    base = dict(source="linkedin", title="Gestor de Projetos", url="u", location="Porto, Portugal",
                posted="2026-11-14", description=desc)
    t = date(2026, 11, 15)
    plain = evaluate(Job(company="Empresa Qualquer", **base), cfg, t)
    for org in ["Associação Smart Energy", "PRODUTECH - Cluster das Tecnologias de Produção",
                "BCSD Portugal", "VORTEX CoLAB", "AEP - Associação Empresarial de Portugal"]:
        v = evaluate(Job(company=org, **base), cfg, t)
        assert v.score == plain.score + 3, org
    assert not plain.include and evaluate(Job(company="BCSD Portugal", **base), cfg, t).include
