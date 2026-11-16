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
    base = dict(source="t", company="X", title="Sustainability Transformation Consultant",
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
    (dict(salary_max=28000), "salário"),
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
    b = evaluate(job(title="Senior Sustainability Transformation Consultant"), cfg, TODAY).score
    assert b == a + cfg["senior_bonus"]


def test_unrelated_job_scores_low(cfg):
    v = evaluate(job(title="Office Manager", description="3+ years of experience. Admin tasks."), cfg, TODAY)
    assert not v.include and "pontuação" in v.excluded_by


def test_salary_floor_keeps_unknown_and_high(cfg):
    assert evaluate(job(salary_max=45000), cfg, TODAY).include
    assert evaluate(job(), cfg, TODAY).include            # sem salário: passa, com aviso


def test_real_world_cases_from_fixture(cfg):
    """Casos reais: a PNO passa; a Siemens Energy (transformação digital com IA) já não é o perfil do Pedro
    (negócio, operações, ambiente); as quatro com problema concreto saem pelo motivo certo."""
    import json
    jobs = {d["company"]: Job(**d) for d in json.load(open("tests/fixtures/sample_jobs.json", encoding="utf-8"))}
    today = date(2026, 11, 15)
    assert "título excluído" in evaluate(jobs["Siemens Energy"], cfg, today).excluded_by
    assert evaluate(jobs["PNO"], cfg, today).include
    assert "língua" in evaluate(jobs["Nodis Consulting"], cfg, today).excluded_by
    assert "júnior" in evaluate(jobs["Navigator"], cfg, today).excluded_by
    assert "anos" in evaluate(jobs["Brisa"], cfg, today).excluded_by
    assert not evaluate(jobs["Greenvolt"], cfg, today).include      # AI Program Manager: tecnologia e antiga


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
    assert evaluate(job("EU Affairs Consultant", "Public affairs agency, monitoring the European Parliament and the European Commission."), maria, t).include
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


def test_maria_no_teachers_nor_generic_public_jobs():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc, company="X"):
        return Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 2 anos de experiência.")
    for title, desc in [
        ("Educadora de Infância (Creche) – Braga", "Educação de infância numa associação, crianças dos 0 aos 3 anos."),
        ("Maths Teacher - Paternity Cover", "Teach maths to children and young people at an international school."),
        ("Técnico Superior — Técnico Superior Turismo", "Câmara Municipal: promoção turística do município."),
        ("Modelador MEP Hidráulica", "Modelação BIM de redes hidráulicas e MEP para projetos na União Europeia."),
        ("Project Manager (Lisbon- Hybrid)", "Manage IT projects for public sector clients and ministries."),
        ("Knowledge Analyst – Procurement", "Research procurement practices across the European Union and Brussels offices."),
    ]:
        v = evaluate(job(title, desc), maria, t)
        assert not v.include, (title, v.themes)
    coke = job("Graduate Public Affairs, Communications and Sustainability",
               "Support public affairs and engagement with the European Commission and Portuguese government.",
               company="Coca-Cola Europacific Partners")
    assert evaluate(coke, maria, t).include


def test_maria_no_content_moderation_jobs():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    j = Job(source="linkedin", company="Concentrix", title="AI Dubbing Content Moderator (Spanish-Speaking)", url="u",
            location="Lisboa, Portugal", posted="2026-11-14",
            description="Review dubbed content for hate speech and discrimination policy violations. 1 year of experience.")
    assert not evaluate(j, maria, date(2026, 11, 15)).include


def test_maria_eu_projects_like_big4_teams():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc, company="X"):
        return Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                   description=desc + " 2 years of experience.")
    for title, desc in [
        ("Consultant – EU Institutions", "Support framework contracts with the European Commission (DG REGIO, DG EMPL) and EU tenders."),
        ("Grant Manager", "Manage EU-funded projects under Horizon Europe and Erasmus+; reporting to the European Commission."),
        ("Técnica de Projetos Europeus", "Gestão de candidaturas a programas europeus e acompanhamento de projetos financiados pela União Europeia."),
    ]:
        v = evaluate(job(title, desc, company="Deloitte"), maria, t)
        assert v.include and "Projetos europeus e cooperação" in v.themes, (title, v.themes, v.excluded_by)


def test_maria_master_themes_and_remote_eu_jobs():
    import yaml
    from datetime import date
    from jobtracker.__main__ import build_profiles
    from jobtracker.models import Job
    from jobtracker.scoring import evaluate
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def job(title, desc, location="Lisboa, Portugal", remote=None):
        return Job(source="linkedin", company="X", title=title, url="u", location=location, posted="2026-11-14",
                   description=desc + " 2 years of experience.", remote=remote)
    v = evaluate(job("Monitoring & Evaluation Officer", "Impact evaluation of labour market policies and social protection programmes."), maria, t)
    assert v.include and {"Avaliação e monitorização de políticas", "Desenvolvimento e políticas de emprego"} <= set(v.themes)
    assert evaluate(job("Policy Officer – EU affairs", "Monitor the European Parliament and the European Commission.",
                        location="European Union", remote=True), maria, t).include
    assert evaluate(job("Policy Officer – EU affairs", "Monitor the European Parliament and the European Commission.",
                        location="Brussels, Belgium"), maria, t).include          # Bruxelas: só vagas de topo, e esta é
    assert not evaluate(job("Policy Officer – EU affairs", "Monitor the European Parliament and the European Commission.",
                            location="Madrid, Spain"), maria, t).include          # presencial noutros países: não



def test_pedro_business_and_environment_not_tech():
    import yaml
    from jobtracker.__main__ import build_profiles
    pedro = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["pedro"]["cfg"]
    t = date(2026, 11, 15)
    corp = " We work in a fast-paced environment with sustainable growth. Ambiente de trabalho dinâmico. 4 anos de experiência."
    def j(title, desc, company="X"):
        return Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                   posted="2026-11-14", description=desc + corp)
    for title, desc in [
        ("IT Business Analyst", "Gather requirements and improve processes for banking applications."),
        ("Engineering Manager, ChatBots and AI", "Lead engineers building conversational AI."),
        ("Atendedor de loja (M/F)", "Atendimento ao público numa loja de mobilidade."),
        ("Accounting Analyst", "Month-end close and process improvement."),
        ("Data Analyst", "Dashboards for operations."),
    ]:
        v = evaluate(j(title, desc), pedro, t)
        assert not v.include, (title, v.themes)
    # "environment" e "sustainable growth" genéricos já não contam como tema
    v = evaluate(j("Project Manager", "Manage internal projects."), pedro, t)
    assert "Clima e ambiente" not in v.themes and "Sustentabilidade e ESG" not in v.themes
    for title, desc in [
        ("Consultor Ambiental", "Avaliação de impacte ambiental, licenciamento ambiental e gestão ambiental para clientes industriais."),
        ("Sustainability Manager", "CSRD reporting, decarbonisation roadmap and circular economy initiatives; operating model for ESG."),
        ("Operations Excellence Manager", "Lean, process improvement and operating model for a water utility; waste management."),
    ]:
        v = evaluate(j(title, desc), pedro, t)
        assert v.include, (title, v.themes, v.excluded_by)


def test_pedro_gets_linkedin_sustainability_search_examples():
    """Exemplos de uma pesquisa 'sustentabilidade' no LinkedIn que têm de chegar ao Pedro."""
    import yaml
    from jobtracker.__main__ import build_profiles
    pedro = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["pedro"]["cfg"]
    t = date(2026, 11, 15)
    cases = {
        "Técnico de Ambiente (m/f) - Obra": "Gestão ambiental em obra, licenciamento ambiental e resíduos. 3 anos de experiência.",
        "Engenheiro/a do Ambiente | Consultoria Ambiental": "Consultoria ambiental: avaliação de impacte ambiental e licenciamento ambiental. 3 anos de experiência.",
        "Environmental Sustainability Engineer – Project Coordination": "Environmental management, decarbonisation and ISO 14001 projects. 4 years of experience.",
        "Engenheiro projetos de Energia e Descarbonização": "Projetos de eficiência energética e descarbonização da indústria. 3 anos de experiência.",
        "Environmental & Biodiversity Specialist": "Biodiversity assessments and environmental impact studies. 4 years of experience.",
        "Procurement Sustainability Specialist": "Sustainable procurement, supplier ESG assessments, decarbonisation. 4 years of experience.",
        "LCA specialist": "Life cycle assessment and carbon footprint of products. 3 years of experience.",
        "Global Nature Strategy Coordinator": "Nature-positive strategy, biodiversity and natural capital for a global manufacturer. 4 years of experience.",
    }
    for title, desc in cases.items():
        v = evaluate(Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal",
                         posted="2026-10-25", description=desc), pedro, t)
        assert v.include, (title, v.score, v.excluded_by)
    for title in ("Técnico de Manutenção", "Técnico de Informática", "Software Engineer", "Engenheiro de Software"):
        assert not evaluate(Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal",
                                posted="2026-11-10", description="3 anos."), pedro, t).include


def test_maria_mep_assistant_is_not_excluded():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    v = evaluate(Job(source="linkedin", company="European Parliament", title="MEP Assistant (Accredited Parliamentary Assistant)",
                     url="u", location="Lisboa, Portugal", posted="2026-11-14",
                     description="Support the MEP on LIBE committee files and EU affairs. 2 years of experience."), maria, date(2026, 11, 15))
    assert v.include, (v.excluded_by, v.themes)


def test_maria_needs_theme_in_title_or_two_themes():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-14", description=desc + " 2 anos de experiência."), maria, t)
    for title, desc in [
        ("Team Lead Roadside Assistance 24/7 Cockpit", "Lead the team; policy assessment of customer cases; stakeholder management."),
        ("Mechanical Project Manager - Industry", "Projects for clients including the European Commission headquarters building."),
        ("Administrativa/o - Fiel de Armazém", "Faturação e gestão de stock. Projeto cofinanciado: projetos europeus Portugal 2030."),
        ("Public Policy Economist (AI Training)", "Train AI models on public policy questions."),
    ]:
        assert not ev(title, desc).include, title
    for title, desc in [
        ("Public Policy Analyst (Junior)", "Policy analysis for public sector clients."),
        ("Graduate Public Affairs, Communications and Sustainability", "Stakeholder engagement and communications."),
        ("Coordenador(a) de Projetos", "ONG de direitos humanos: gestão de projetos europeus e advocacy junto da sociedade civil."),
    ]:
        v = ev(title, desc)
        assert v.include, (title, v.excluded_by, v.themes)


def test_maria_business_consulting_yes_tax_no():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Porto, Portugal",
                            posted="2026-11-14", description=desc + " 2 anos de experiência."), maria, t)
    for title, desc in [
        ("Consultora de Gestão", "Projetos de reorganização e melhoria de processos para empresas industriais."),
        ("Strategy Consultant", "Strategy and operations projects for corporate clients."),
        ("Junior Consultant – Business Transformation", "Operating model and change management for clients."),
        ("Business Analyst – Management Consulting", "Consulting projects: process mapping and stakeholder interviews for clients."),
    ]:
        v = ev(title, desc, company="Deloitte")
        assert v.include, (title, v.excluded_by, v.themes)
    for title in ("Tax Consultant", "Consultor Fiscal", "Transfer Pricing Consultant", "Audit Assistant", "Consultor de IVA",
                  "SAP Consultant", "IT Consultant", "Accounting Consultant"):
        assert not ev(title, "Consultoria para empresas.").include, title


def test_maria_no_tech_consulting():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    for title in ("Strategic Digital Consultant – Data & AI", "AI Governance Consultant", "Digital Transformation Consultant",
                  "Data Analyst – Public Sector", "Technology Consultant", "Automation Consultant (RPA)", "Power BI Consultant"):
        v = evaluate(Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal",
                         posted="2026-11-14", description="Public policy consulting for ministries. 2 anos."), maria, date(2026, 11, 15))
        assert not v.include, title


def test_maria_generic_consultant_title_with_tech_description_is_out():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    v = evaluate(Job(source="linkedin", company="X", title="Consultant", url="u", location="Lisboa, Portugal", posted="2026-11-14",
                     description="Software development and cloud migration projects; Python, SQL and SAP integration; "
                                 "machine learning pipelines for clients. 2 years of experience."), maria, date(2026, 11, 15))
    assert not v.include, (v.score, v.themes)


def test_maria_business_analyst_at_consultancy_and_priority_order():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, company, desc="Client projects, stakeholder interviews and reporting. 2 anos de experiência."):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-14", description=desc), maria, t)
    assert ev("Business Analyst", "Deloitte").include
    assert ev("Business Analyst", "Sia Partners").include
    assert not ev("Business Analyst", "Banco X").include                  # fora de uma consultora, não chega
    for title in ("Data Analyst", "AI Business Analyst", "Data Engineer"):
        assert not ev(title, "Deloitte").include, title                     # nada de tecnologia, mesmo numa Big4
    policy = ev("Public Policy Analyst", "PwC", "Public policy analysis and policy evaluation for ministries. 2 anos.")
    consult = ev("Strategy Consultant", "Empresa", "Strategy projects for corporate clients. 2 anos.")
    assert policy.include and consult.include and policy.score > consult.score   # os temas do mestrado vêm primeiro


def test_unskilled_admin_health_fleet_jobs_never_pass():
    import yaml
    from jobtracker.__main__ import build_profiles
    ps = {p["id"]: p["cfg"] for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}
    t = date(2026, 11, 15)
    titles = ["Gestor de Frota (M/F)", "Assistente Administrativo - Centro de Saúde", "Administrativa/o - Fiel de Armazém",
              "Diretor/a de Golfe", "S4/HANA Consultant Logistic", "Consultor Funcional Sénior Cegid PHC",
              "Head of Talent Acquisition", "Contact Center Solutions Specialist", "Rececionista de Clínica"]
    for pid in ("pedro", "maria"):
        for title in titles:
            j = Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Portugal", posted="2026-11-14",
                    description="Políticas públicas, sustentabilidade, gestão ambiental e assuntos europeus. 3 anos.")
            assert not evaluate(j, ps[pid], t).include, (pid, title)


def test_maria_libe_and_public_affairs_rank_above_consulting():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-14", description=desc + " 2 anos de experiência."), maria, t)
    coke = ev("Graduate Public Affairs, Communications and Sustainability", "Public affairs and engagement with the European Commission.",
              company="Coca-Cola Europacific Partners")
    libe = ev("Policy Officer – Civil Liberties", "Rule of law, fundamental rights and asylum policy files.")
    consult = ev("Logistics Consultant", "Consulting projects for corporate clients on supply chain processes.", company="Accenture")
    assert coke.include and libe.include
    assert coke.score > consult.score and libe.score > consult.score


def test_maria_audit_generic_consultant_titles_and_incentives_out():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-10", description=desc + " 2 anos de experiência."), maria, t)
    for title, desc, company in [
        ("Incentives Consultant", "SIFIDE II and Portugal 2030 grant applications for companies.", "Ayming"),
        ("Consultor de Fundos Europeus", "Candidaturas aos sistemas de incentivos do Portugal 2030 e PRR para empresas.", "X"),
        ("Credit Risk Consultant", "Credit risk models and IFRS 9.", "Banco"),
        ("Consultor Imobiliário", "Venda de imóveis.", "Imobiliária"),
        ("Insurance Consultant", "Advise clients on insurance products.", "Seguradora"),
        ("HR Consultant", "Payroll and HR administration for client companies.", "X"),
        ("MEP Coordinator", "Coordinate MEP design (HVAC, electrical, plumbing) on construction projects.", "Construtora"),
    ]:
        v = ev(title, desc, company)
        assert not v.include, (title, v.score, v.themes)
    for title, desc, company in [
        ("EU Funds Consultant", "EU Institutions team: framework contracts and tenders for the European Commission.", "Deloitte"),
        ("Junior Consultant", "Management consulting projects; strategy and process redesign for clients.", "Boutique X"),
        ("Assessor de Comunicação Política", "Assessoria política ao grupo parlamentar; acompanhamento da agenda legislativa.", "X"),
        ("Técnica Superior – Igualdade e Não Discriminação", "Plano Municipal para a Igualdade e contratação pública responsiva ao género.", "Câmara Municipal de Lisboa"),
        ("Policy Officer – Data Protection and Civil Liberties", "Advocacy on data protection and civil liberties in EU law.", "NGO"),
        ("Programme Officer – Employment Policy", "Labour market policies, decent work and social protection.", "OIT"),
    ]:
        v = ev(title, desc, company)
        assert v.include, (title, v.excluded_by, v.themes)


def test_maria_top_priority_outranks_big4_generic_consulting():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-10", description=desc + " 2 years of experience."), maria, t)
    generic = [ev("Business Analyst", "Consulting projects; process mapping and stakeholder interviews.", "Deloitte"),
               ev("Strategy Consultant", "Strategy and operations consulting projects for corporate clients.", "Accenture"),
               ev("Consultora de Gestão", "Projetos de consultoria de gestão e melhoria de processos.", "Sia Partners")]
    top = [ev("Graduate Public Affairs", "Monitor legislative developments in Portugal and the EU; stakeholder engagement.", "Coca-Cola Europacific Partners"),
           ev("Accredited Parliamentary Assistant", "Follow LIBE and PETI committee files for an MEP. Remote from Lisbon.", "European Parliament"),
           ev("Assessora de Gabinete", "Acompanhamento legislativo e articulação com a Assembleia da República.", "Ministério"),
           ev("Monitoring & Evaluation Officer", "Impact evaluation of development cooperation programmes.", "Camões"),
           ev("Programme Officer – Employment Policy", "Labour market policies and social protection.", "OIT")]
    assert all(g.include for g in generic) and all(x.include for x in top)
    assert min(x.score for x in top) > max(g.score for g in generic), ([x.score for x in top], [g.score for g in generic])


def test_maria_digital_rights_is_libe_not_tech():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    v = evaluate(Job(source="linkedin", company="NGO", title="Policy Officer – Digital Rights", url="u", location="Lisboa, Portugal",
                     posted="2026-11-10", description="Civil liberties and fundamental rights in EU digital legislation. 2 anos."),
                 maria, date(2026, 11, 15))
    assert v.include, (v.excluded_by, v.themes)
    assert not evaluate(Job(source="linkedin", company="X", title="Digital Marketing Consultant", url="u", location="Lisboa, Portugal",
                            posted="2026-11-10", description="Campaigns. 2 anos."), maria, date(2026, 11, 15)).include


def test_maria_brussels_only_for_top_jobs_pedro_never():
    import yaml
    from jobtracker.__main__ import build_profiles
    ps = {p["id"]: p["cfg"] for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}
    t = date(2026, 11, 15)
    def ev(pid, title, desc, loc="Brussels, Brussels Region, Belgium"):
        return evaluate(Job(source="linkedin", company="Flint Global", title=title, url="u", location=loc, posted="2026-11-01",
                            description=desc + " 2 years of experience."), ps[pid], t)
    flint = ev("maria", "Consultant - Public Policy and Public Affairs with a focus on EU energy, environment and sustainability policy",
               "Public affairs and EU policy advice for clients; monitor the European Parliament and the European Commission.")
    assert flint.include and any("fora de Portugal" in f for f in flint.flags)
    assert not ev("maria", "Strategy Consultant", "Strategy projects for corporate clients.", ).include   # consultoria genérica em Bruxelas: não
    assert not ev("maria", "Project Officer", "Management of EU-funded projects (Erasmus+, Interreg).").include   # não é tema de topo
    assert not ev("pedro", "Sustainability Consultant", "CSRD and decarbonisation for clients.").include      # o Pedro não tem Bruxelas
    assert not ev("maria", "Public Affairs Manager", "EU affairs.", loc="Porto Alegre, Brazil").include      # Porto Alegre não é Porto


def test_maria_public_affairs_title_beats_multi_theme_consulting_and_noise_out():
    import yaml
    from jobtracker.__main__ import build_profiles
    maria = {p["id"]: p for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}["maria"]["cfg"]
    t = date(2026, 11, 15)
    def ev(title, desc, company="X"):
        return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                            posted="2026-11-10", description=desc + " 2 years of experience."), maria, t)
    coke = ev("Graduate Public Affairs, Communications and Sustainability",
              "Support public affairs and communications; stakeholder engagement.", "Coca-Cola Europacific Partners")
    eura = ev("Junior Consultant", "Consulting on EU funding: European Commission programmes, framework contracts, "
              "EU policy and public affairs for clients.", "EurA AG")
    assert coke.include and coke.score > eura.score, (coke.score, eura.score)
    for title in ("Research Scientist – Patient-Centered Outcomes", "Lecturer in Systems Thinking and Foresight",
                  "Oliver Wyman - Social Media Manager", "Portugal com ACNUR | Projeto Face To Face",
                  "Associate Director, Patient Advocacy", "Implementation Associate Consultant"):
        assert not ev(title, "Public policy, EU affairs, human rights and consulting projects.").include, title


def test_new_jobs_16_nov_false_positives_out():
    import yaml
    from jobtracker.__main__ import build_profiles
    ps = {p["id"]: p["cfg"] for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}
    t = date(2026, 11, 16)
    def ev(pid, title, desc="Sustainability, water utilities, public policy and EU affairs. 3 years of experience."):
        return evaluate(Job(source="linkedin", company="X", title=title, url="u", location="Lisboa, Lisboa, Portugal",
                            posted="2026-11-15", description=desc), ps[pid], t)
    for pid in ("pedro", "maria"):
        assert not ev(pid, "Associate Water Sector Specialist - based in Luxembourg").include
        assert not ev(pid, "Content Reviewer – Detection Intel (Czech Speakers)").include
    for title in ("R2R FA Team Leader", "ALM & Structured Funding Analyst (Securitisation & Derivatives)",
                  "Artwork & Change Management Coordinator"):
        assert not ev("pedro", title).include, title
    assert ev("pedro", "Water Projects Manager", "Water utility projects, wastewater and sustainability. 3 anos.").include
