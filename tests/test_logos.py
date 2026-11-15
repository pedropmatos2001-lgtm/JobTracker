"""Logótipos: um por marca e ícone do site quando a fonte não dá logótipo."""
from jobtracker import logos


def test_brand_key_drops_legal_suffixes():
    assert logos.brand_key("GENPACT PORTUGAL, SOCIEDADE UNIPESSOAL LDA") == "genpact"
    assert logos.brand_key("Infineon Technologies") == "infineon"
    assert logos.brand_key("Águas do Norte, S.A.") == "aguas do norte"


def test_same_brand_gets_one_logo():
    e = [{"company": "BNP Paribas", "logo": "https://a/bnp.png"},
         {"company": "BNP Paribas CIB", "logo": "https://a/bnp-cib.png"},
         {"company": "BNP Paribas CIB", "logo": ""}]
    logos.apply({}, e)
    assert {x["logo"] for x in e} == {"https://a/bnp.png"}


def test_logo_is_remembered_across_runs():
    st = {}
    logos.apply(st, [{"company": "Coca-Cola Europacific Partners", "logo": "https://a/cc.png"}])
    later = [{"company": "Coca-Cola Europacific Partners", "logo": ""}]
    logos.apply(st, later)
    assert later[0]["logo"] == "https://a/cc.png"


def test_known_domain_when_no_logo_and_most_specific_wins():
    e = [{"company": "EY", "logo": ""}, {"company": "Siemens Energy", "logo": ""},
         {"company": "Turbogás", "logo": ""}, {"company": "Empresa Desconhecida", "logo": ""},
         {"company": "Outra", "logo": "", "domain": "outra.pt"}]
    logos.apply({}, e)
    assert [x.get("domain") for x in e] == ["ey.com", "siemens-energy.com", "turbogas.pt", None, "outra.pt"]


def test_domain_of_ignores_job_boards():
    assert logos.domain_of("https://www.edp.com/pt/carreiras") == "edp.com"
    assert logos.domain_of("https://pt.indeed.com/cmp/x") == ""
