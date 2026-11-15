"""Logótipos das empresas: um só por marca, partilhado por todas as vagas dessa marca.

1) Logótipos que as fontes dão (LinkedIn, Indeed) ficam guardados no estado por marca, para as vagas
   da mesma marca sem logótipo (ou com outra variante, ex.: BNP Paribas e BNP Paribas CIB) usarem o mesmo.
2) Sem logótipo, a página mostra o ícone do site oficial (domínio conhecido abaixo ou dado pela fonte).
3) Sem nada disto, a página mostra as iniciais.
"""
import re
import unicodedata
from urllib.parse import urlparse

SUFFIXES = {"portugal", "sa", "lda", "unipessoal", "sociedade", "cib", "group", "grupo", "inc", "corp", "corporation", "ltd",
            "limited", "gmbh", "ag", "plc", "llc", "co", "company", "the", "usa", "se", "nv", "bv", "spa", "sl",
            "holding", "holdings", "technologies", "global", "international", "iberia", "europe", "emea"}

# Domínios de marcas frequentes nas vagas (chave = brand_key do nome). Acrescenta à vontade.
KNOWN_DOMAINS = {
    "ey": "ey.com", "ernst young": "ey.com", "deloitte": "deloitte.com", "pwc": "pwc.com", "kpmg": "kpmg.com",
    "accenture": "accenture.com", "mckinsey": "mckinsey.com", "boston consulting": "bcg.com", "bcg": "bcg.com",
    "bain": "bain.com", "capgemini": "capgemini.com", "nttdata": "nttdata.com", "ntt data": "nttdata.com",
    "ibm": "ibm.com", "kearney": "kearney.com", "roland berger": "rolandberger.com", "oliver wyman": "oliverwyman.com",
    "bdo": "bdo.global", "grant thornton": "grantthornton.global", "mazars": "forvismazars.com", "forvis mazars": "forvismazars.com",
    "everis": "nttdata.com", "minsait": "minsait.com", "indra": "indracompany.com", "sopra steria": "soprasteria.com",
    "alten": "alten.com", "altran": "capgemini.com", "migso pcubed": "migso-pcubed.com", "metyis": "metyis.com",
    "wtw": "wtwco.com", "mercer": "mercer.com", "aon": "aon.com", "marsh": "marsh.com", "gartner": "gartner.com",
    "coca cola": "coca-cola.com", "coca cola europacific partners": "cocacolaep.com", "pepsico": "pepsico.com",
    "nestle": "nestle.com", "unilever": "unilever.com", "procter gamble": "pg.com", "l oreal": "loreal.com",
    "bnp paribas": "bnpparibas.com", "santander": "santander.com", "millennium bcp": "millenniumbcp.pt", "bcp": "millenniumbcp.pt",
    "caixa geral de depositos": "cgd.pt", "novo banco": "novobanco.pt", "bpi": "bancobpi.pt", "credito agricola": "creditoagricola.pt",
    "revolut": "revolut.com", "natixis": "natixis.com", "euronext": "euronext.com", "fidelidade": "fidelidade.pt",
    "siemens": "siemens.com", "siemens energy": "siemens-energy.com", "siemens healthineers": "siemens-healthineers.com",
    "bosch": "bosch.com", "continental": "continental.com", "vestas": "vestas.com", "carrier": "carrier.com",
    "schneider electric": "se.com", "abb": "abb.com", "atlas copco": "atlascopco.com", "infineon": "infineon.com",
    "sap": "sap.com", "microsoft": "microsoft.com", "google": "google.com", "amazon": "amazon.com", "aws": "aws.amazon.com",
    "farfetch": "farfetch.com", "outsystems": "outsystems.com", "feedzai": "feedzai.com", "talkdesk": "talkdesk.com",
    "sonae": "sonae.pt", "sonae sierra": "sonaesierra.com", "jeronimo martins": "jeronimomartins.com", "nos": "nos.pt",
    "meo": "meo.pt", "altice": "altice.pt", "vodafone": "vodafone.pt", "ctt": "ctt.pt", "tap": "flytap.com",
    "ana aeroportos": "ana.pt", "vinci": "vinci.com", "brisa": "brisa.pt", "cp comboios": "cp.pt", "metro do porto": "metrodoporto.pt",
    "genpact": "genpact.com", "cuf": "cuf.pt", "workato": "workato.com", "insulet": "insulet.com", "enovis": "enovis.com",
    "n ix": "n-ix.com", "luz saude": "luzsaude.pt", "jose de mello": "josedemello.pt", "galp energia": "galp.com",
    "johnson johnson": "jnj.com", "viatris": "viatris.com", "hovione": "hovione.com", "bial": "bial.com",
    "kuehne nagel": "kuehne-nagel.com", "dhl": "dhl.com", "maersk": "maersk.com",
    "bp": "bp.com", "shell": "shell.com", "totalenergies": "totalenergies.com", "engie": "engie.com",
    "lightsource bp": "lightsourcebp.com", "voltalia": "voltalia.com", "orsted": "orsted.com", "hyperion": "hyperionrenewables.com",
    # empresas-alvo do Pedro
    "edp": "edp.com", "edp renovaveis": "edpr.com", "e redes": "e-redes.pt", "edp comercial": "edp.pt",
    "ren": "ren.pt", "redes energeticas nacionais": "ren.pt", "galp": "galp.com", "floene": "floene.pt",
    "greenvolt": "greenvolt.com", "endesa": "endesa.pt", "iberdrola": "iberdrola.pt", "naturgy": "naturgy.pt",
    "repsol": "repsol.pt", "moeve": "moeveglobal.com", "cepsa": "moeveglobal.com", "prio": "prio.pt", "rubis": "rubis.pt",
    "finerge": "finerge.pt", "enel": "enelgreenpower.com", "enel green power": "enelgreenpower.com", "axpo": "axpo.com",
    "gold energy": "goldenergy.pt", "goldenergy": "goldenergy.pt", "martifer": "martifer.com", "ventinveste": "ventinveste.pt",
    "elecgas": "elecgas.pt", "tejo energia": "tejoenergia.com", "turbogas": "turbogas.pt", "dourogas": "dourogas.pt",
    "efacec": "efacec.com", "aguas de portugal": "adp.pt", "adp": "adp.pt", "epal": "epal.pt",
    "aguas do norte": "adnorte.pt", "aguas do tejo atlantico": "aguasdotejoatlantico.adp.pt",
    "aguas do centro litoral": "adcl.pt", "aguas publicas do alentejo": "agda.pt", "aguas do algarve": "aguasdoalgarve.pt",
    "adp valor": "adpvalor.pt", "adp energias": "adpenergias.pt", "indaqua": "indaqua.pt", "be water": "bewater.pt",
    "lusagua": "lusagua.pt", "veolia": "veolia.pt", "aguas do porto": "aguasdoporto.pt", "ags": "ags.pt",
    "aguas de gaia": "aguasgaia.pt", "aquapor": "aquapor.pt", "egf": "egf.pt", "valorsul": "valorsul.pt",
    "suldouro": "suldouro.pt", "resinorte": "resinorte.pt", "ersuc": "ersuc.pt", "amarsul": "amarsul.pt",
    "algar": "algar.com.pt", "valnor": "valnor.pt", "resiestrela": "resiestrela.pt", "valorlis": "valorlis.pt",
    "ecoleziria": "ecoleziria.pt", "resitejo": "resitejo.pt", "lipor": "lipor.pt", "braval": "braval.pt",
    "tratolixo": "tratolixo.pt", "sociedade ponto verde": "pontoverde.pt", "ponto verde": "pontoverde.pt",
    "valorpneu": "valorpneu.pt", "amb3e": "amb3e.pt", "novo verde": "novoverde.pt", "ecopilhas": "ecopilhas.pt",
    "suma": "suma.pt", "urbaser": "urbaser.pt", "mota engil": "mota-engil.com", "ecodeal": "ecodeal.pt",
    "quadrante": "quadrante.pt", "teixeira duarte": "teixeiraduarte.pt", "navigator": "thenavigatorcompany.com",
    "the navigator": "thenavigatorcompany.com",
}


def brand_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    while words and words[-1] in SUFFIXES:
        words.pop()
    while len(words) > 1 and words[0] in ("the", "grupo", "group"):
        words.pop(0)
    return " ".join(words)


def _prefixes(key):
    """Da marca mais curta para a mais longa: «bnp», «bnp paribas», «bnp paribas cib»."""
    w = key.split()
    return [" ".join(w[:i]) for i in range(1, len(w) + 1) if i > 1 or len(w[0]) >= 3 or len(w) == 1]


def lookup(key, book, longest=False):
    """Logótipos: a marca mais curta (uniformiza variantes). Domínios: a mais longa (Siemens Energy, não Siemens)."""
    for k in (reversed(_prefixes(key)) if longest else _prefixes(key)):
        if k in book:
            return book[k]
    return ""


def domain_of(url: str) -> str:
    host = urlparse(url or "").hostname or ""
    host = re.sub(r"^www\d?\.", "", host)
    if not host or any(x in host for x in ("indeed.", "linkedin.", "glassdoor.", "google.")):
        return ""
    return host


def apply(state, entries):
    """Aprende os logótipos das entradas e uniformiza: mesma marca, mesmo logótipo."""
    book = state.setdefault("logos", {})
    for e in entries:
        if e.get("logo"):
            book.setdefault(brand_key(e.get("company", "")), e["logo"])
    for e in entries:
        key = brand_key(e.get("company", ""))
        if not key or key == "confidential":
            continue
        e["logo"] = lookup(key, book) or e.get("logo", "")
        if not e["logo"]:
            d = lookup(key, KNOWN_DOMAINS, longest=True) or e.get("domain", "")
            if d:
                e["domain"] = d
