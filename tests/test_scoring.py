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
                            "and agentic AI. Operating model design for a sustainability programme. "
                            "Stakeholder management and requirements gathering.")
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


def test_no_bonus_for_associations_but_utilities_count_for_pedro():
    import yaml
    from datetime import date
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    desc = "Gestão de processos e melhoria contínua. 4 anos de experiência."
    base = dict(source="linkedin", title="Gestor de Operações", url="u", location="Porto, Portugal", posted="2026-11-14", description=desc)
    t = date(2026, 11, 15)
    plain = evaluate(Job(company="Empresa Qualquer", **base), cfg, t).score
    assert evaluate(Job(company="Associação Empresarial do Minho", **base), cfg, t).score == plain
    assert evaluate(Job(company="BCSD Portugal", **base), cfg, t).score == plain
    assert evaluate(Job(company="Águas do Norte", **base), cfg, t).score == plain + cfg["sector_bonus"]["points"]


def test_maria_only_gets_public_sector_and_human_rights():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc):
        return Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 3 anos de experiência.")
    assert evaluate(job("Técnica de Proteção de Crianças e Jovens", "Projeto de proteção de crianças em situação de risco."), maria, t).include
    assert evaluate(job("Project Officer – Gender Equality", "Programa de combate à violência doméstica e igualdade de género."), maria, t).include
    assert evaluate(job("Policy Officer", "Public policy analysis for the Ministry, public administration reform."), maria, t).include
    v = evaluate(job("Consultora de Incentivos", "SIFIDE, Portugal 2030, candidaturas a incentivos para empresas."), maria, t)
    assert not v.include                 # incentivos fiscais ficam de fora (pelo título ou por falta de temas)


def test_maria_eu_affairs_and_projects_yes_tax_incentives_no():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc):
        return Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 3 anos de experiência.")
    assert evaluate(job("EU Affairs Account Executive", "Public affairs agency, monitoring the European Parliament and the European Commission."), maria, t).include
    assert evaluate(job("Project Officer", "Management of EU-funded projects (Erasmus+, Interreg) and international cooperation."), maria, t).include
    assert not evaluate(job("Consultora de Incentivos Fiscais", "SIFIDE e benefícios fiscais para empresas."), maria, t).include
    assert not evaluate(job("Analista", "Candidaturas a incentivos financeiros ao investimento, SIFIDE e crédito fiscal."), maria, t).include


def test_boilerplate_does_not_make_corporate_jobs_reach_maria():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    eeo = (" We are an equal opportunity employer and value diversity. All qualified applicants will receive consideration "
           "regardless of gender, age or disability. We're committed to gender equality and women in leadership.")
    def job(title, desc):
        return Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 3 years of experience." + eeo)
    for title, desc in [
        ("HR Business Analyst & Automation", "Analyse HR processes and automate workflows with Power BI and Excel. Stakeholder management."),
        ("Strategic Digital Consultant – Data & AI", "Drive data strategy for the energy business. Mentor young talent and youth hires."),
        ("Associate Manager, Rim Data Maintenance", "Regulatory affairs data maintenance, legislation tracking for pharmaceutical products."),
        ("Information Security Automation Analyst", "Automate security controls; migration of systems to the cloud; protect vulnerable assets."),
        ("Operational Excellence Manager", "Lean, process improvement and operating model for manufacturing sites."),
    ]:
        v = evaluate(job(title, desc), maria, t)
        assert not v.include, (title, v.themes)
    ok = evaluate(job("Graduate Public Affairs, Communications and Sustainability",
                      "Monitor EU affairs and the European Commission, engage with public sector stakeholders."), maria, t)
    assert ok.include and "Assuntos europeus" in ok.themes


def test_maria_gets_libe_style_work_and_not_data_jobs():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc):
        return Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 2 anos de experiência.")
    for title, desc, theme in [
        ("Assessora Parlamentar", "Apoio ao grupo parlamentar na Comissão de Assuntos Constitucionais, Direitos, Liberdades e Garantias.", "Assessoria política e parlamentar"),
        ("Policy Officer – Rule of Law", "Monitor rule of law and civil liberties files in the LIBE committee of the European Parliament.", "Liberdades, justiça e Estado de direito"),
        ("Advocacy Officer", "NGO working on migrants' rights and asylum policy; civil society coalitions.", "Sociedade civil e associações"),
        ("Técnica de Projeto", "Associação de defesa dos direitos humanos: combate à discriminação e ao discurso de ódio.", "Liberdades, justiça e Estado de direito"),
    ]:
        v = evaluate(job(title, desc), maria, t)
        assert v.include and theme in v.themes, (title, v.themes, v.excluded_by)
