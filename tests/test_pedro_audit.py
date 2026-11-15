"""Regras do Pedro: casos da auditoria (o que passou mal e o que ficou de fora sem razão)."""
from datetime import date

import pytest
import yaml

from jobtracker.__main__ import build_profiles
from jobtracker.models import Job
from jobtracker.scoring import evaluate

T = date(2026, 11, 15)


@pytest.fixture(scope="module")
def ps():
    return {p["id"]: p["cfg"] for p in build_profiles(yaml.safe_load(open("config.yaml", encoding="utf-8")))}


def ev(cfg, title, desc, company="X"):
    return evaluate(Job(source="linkedin", company=company, title=title, url="u", location="Lisboa, Portugal",
                        posted="2026-11-10", description=desc), cfg, T)


@pytest.mark.parametrize("title,desc,company", [
    ("Environmental Technician", "Environmental monitoring, waste management compliance and environmental reporting. 3 years of experience.", "Veolia"),
    ("Consultor de Modernização Administrativa", "Reorganização de serviços em autarquias, simplificação de processos e modelo operativo. 3 anos de experiência.", "CEDRU"),
    ("Sustainability Manager (he/she)", "CSRD reporting, decarbonisation and circular packaging. 4 years of experience.", "X"),
    ("Environmental Scientist", "Environmental impact assessment and environmental due diligence for energy projects. 3 years of experience.", "ERM"),
    ("Water Infrastructure Program Manager", "PMO for the water and wastewater investment programme, EU funds (PRR) reporting. 5 years of experience.", "Águas do Norte"),
    ("Carbon Accounting Consultant", "GHG inventories, carbon footprint and science-based targets. 3 years of experience.", "Get2C"),
    ("Consultor/a", "Oferecemos serviços de consultoria em sustentabilidade, ESG e economia circular a grandes empresas. "
                    "Procuramos alguém com 3 anos de experiência.", "Systemiq"),
    ("Engenheiro do Ambiente", "Avaliação de impacte ambiental e consultoria ambiental. 3 anos de experiência.", "Ramboll"),
])
def test_pedro_good_jobs_are_not_dropped(ps, title, desc, company):
    v = ev(ps["pedro"], title, desc, company)
    assert v.include, (title, v.excluded_by, v.score)


@pytest.mark.parametrize("title,desc,company", [
    ("Project Manager", "Gestão de projetos de implementação de software e migração para cloud para a banca. 4 anos de experiência.", "Capgemini"),
    ("PMO", "PMO de projetos de sistemas de informação e implementação de ERP para clientes públicos. 4 anos de experiência.", "Minsait"),
    ("Consultor de IA", "Soluções de inteligência artificial para utilities. 3 anos de experiência.", "Minsait"),
    ("Business Intelligence Consultant", "Power BI dashboards for utilities clients. 3 years of experience.", "Accenture"),
    ("Digital Transformation Consultant", "Automação RPA e low-code para a administração pública. 3 anos de experiência.", "NTT Data"),
    ("Strategy Consultant – Energy", "Strategy and operating model projects for energy utilities. 3 years of experience.", "Strategy&"),
    ("QHSE Manager", "Sistemas de qualidade, ambiente e segurança (ISO 9001, 14001, 45001). 5 anos de experiência.", "Veolia"),
    ("Técnico QAS", "Qualidade, ambiente e segurança em obra. 3 anos de experiência.", "Mota-Engil"),
    ("Head of Procurement", "Gestão de compras e negociação com fornecedores. 6 anos de experiência.", "Galp"),
    ("Procurement Manager", "Supplier negotiation; some sustainability criteria in tenders. 5 years of experience.", "Galp"),
    ("Customer Experience Manager", "Melhorar a jornada do cliente. 4 anos de experiência.", "EDP Comercial"),
    ("Operations Analyst", "Back-office operations, payments processing and KYC checks. 3 years of experience.", "Banco Santander"),
    ("Asset Management Analyst", "Portfolio analysis for investment funds. 3 years of experience.", "BPI Gestão de Ativos"),
    ("Operations Manager", "Gestão de estafetas e logística de última milha. 4 anos de experiência.", "Glovo"),
    ("Commercial Manager – Renewables", "Carteira de clientes empresariais de energia renovável. 5 anos de experiência.", "Iberdrola"),
    ("Técnico de Ambiente – Regime de Turnos", "Controlo de efluentes e emissões em turnos rotativos. 3 anos de experiência.", "Navigator"),
    ("Técnico de Recrutamento e Seleção", "Recrutamento para o setor da energia. 2 anos de experiência.", "Randstad"),
    ("Project Manager", "Gestão de obras de AVAC e climatização. 4 anos de experiência.", "ClimaTec Climatização, Lda"),
    ("Telematics Implementation Specialist", "GPS tracking and telematics software for fleets; carbon emissions. 3 years of experience.", "Frotcom"),
    ("Sustainability Coordinator", "Sustentabilidade e gestão ambiental nos hotéis do grupo. 3 anos de experiência.", "Vila Galé Hotéis"),
    ("Senior Consultant – Big4 Sustainability Team", "Our client, a Big4 firm, is hiring for ESG. 4 years of experience.", "Hays"),
    ("Gestor de Processos Clínicos", "Melhoria de processos clínicos. 4 anos de experiência.", "Hospital da Luz"),
])
def test_pedro_bad_jobs_never_pass(ps, title, desc, company):
    v = ev(ps["pedro"], title, desc, company)
    assert not v.include, (title, v.themes, v.score)


def test_pedro_rules_do_not_leak_into_maria(ps):
    """Tudo o que está no topo do config (regras do Pedro) chega à Maria se ela não o substituir em `rules:`."""
    pedro, maria = ps["pedro"], ps["maria"]
    for key in ("keywords", "title_exclude", "target_companies", "senior_title", "years", "company_exclude"):
        assert maria[key] != pedro[key], key
    assert not maria.get("company_exclude") and not (maria.get("sector_bonus") or {}).get("pattern")
    assert "org_bonus" not in pedro and "target_role_titles" not in pedro
