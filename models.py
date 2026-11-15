from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Job:
    source: str                      # ex.: "greenhouse:systemiq"
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""            # texto simples (sem HTML)
    posted: str = ""                 # AAAA-MM-DD, se a fonte o disser
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    remote: Optional[bool] = None
    partial: bool = False            # descricao incompleta (ex.: Adzuna)
    unverified: bool = False         # fonte nao confirma se esta aberta (ex.: page_watch)
    extra: dict = field(default_factory=dict)   # dados para ir buscar o detalhe

    @property
    def uid(self) -> str:
        return self.url

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("description", None)   # nao guardar textos longos no estado
        d.pop("extra", None)
        return d
