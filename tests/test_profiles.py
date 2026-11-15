"""Vários perfis: cada um avalia com as suas regras e recebe só as suas vagas."""
import json

import yaml

from jobtracker import notify
from jobtracker.__main__ import build_profiles, run
from jobtracker.models import Job
from tests.test_run import WED, paths


def cfg2():
    with open("config.yaml", encoding="utf-8") as f:
        c = yaml.safe_load(f)
    c["sources"] = [{"type": "x", "name": "A"}]
    return c


def mk(title, desc, company="Empresa", url=None):
    return Job(source="linkedin", company=company, title=title, url=url or f"https://x/{abs(hash(title))}",
               location="Lisboa, Portugal", posted="2026-11-17", description=desc)


OPS = mk("Operations Excellence Manager", "4 years of experience. Process improvement, lean, operating model "
         "for a water utility. Sustainability reporting.", company="Águas do Norte")
POLICY = mk("Policy Analyst – EU Affairs", "3 years of experience. Public policy analysis, European Parliament "
            "monitoring, Horizon Europe funding applications. Stakeholder management.", company="Think Tank")
BOTH = mk("Gestor de Fundos Europeus e Sustentabilidade", "3 anos de experiência. Candidaturas a fundos, Portugal 2030, "
          "sustentabilidade e economia circular, melhoria de processos.", company="CCDR Norte")


def go(cfg, jobs, tmp_path, env=None):
    sp, dd = paths(tmp_path)
    sent = []
    r = run(cfg, env or {}, WED, sp, dd, fetch=lambda src: jobs, detail=lambda j: j,
            send=lambda t, b, c, p=None: sent.append((p["id"], t, b, c)) or ["ok"])
    return r, sent, json.load(open(f"{dd}/jobs.json", encoding="utf-8"))


def test_each_profile_gets_only_its_jobs(tmp_path):
    cfg = cfg2()
    cfg["click_url"] = "https://exemplo.github.io/JobTracker/"
    r, sent, docs = go(cfg, [OPS, POLICY], tmp_path)
    by = {pid: (t, b, c) for pid, t, b, c in sent}
    assert set(by) == {"pedro", "marta"}
    assert "Operations Excellence" in by["pedro"][1] and "Policy Analyst" not in by["pedro"][1]
    assert "Policy Analyst" in by["marta"][1] and "Operations Excellence" not in by["marta"][1]
    assert by["marta"][0].startswith("Marta: 1 vaga") and by["marta"][2].endswith("#marta")
    m = {x["title"]: x for x in docs["matches"]}
    assert set(m["Operations Excellence Manager"]["profiles"]) == {"pedro"}
    assert set(m["Policy Analyst – EU Affairs"]["profiles"]) == {"marta"}


def test_job_for_both_is_stored_once_with_both_profiles(tmp_path):
    r, sent, docs = go(cfg2(), [BOTH], tmp_path)
    assert len(docs["matches"]) == 1 and set(docs["matches"][0]["profiles"]) == {"pedro", "marta"}
    assert sorted(p for p, *_ in sent) == ["marta", "pedro"]


def test_page_gets_only_public_profile_fields(tmp_path):
    _, _, docs = go(cfg2(), [OPS], tmp_path)
    ids = [p["id"] for p in docs["profiles"]]
    assert ids == ["pedro", "marta"]
    for p in docs["profiles"]:
        assert not ({"ntfy_env", "email_env", "profile_env", "rules", "admin"} & set(p))


def test_source_alerts_go_only_to_admin(tmp_path):
    cfg = cfg2()
    cfg["sources"] = [{"type": "x", "name": "A"}]
    sp, dd = paths(tmp_path)
    sent = []
    def boom(src):
        raise RuntimeError("caiu")
    for _ in range(3):
        run(cfg, {}, WED, sp, dd, fetch=boom, detail=lambda j: j,
            send=lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"])
    assert sent == ["pedro"]                           # 3.ª falha: só o Pedro é avisado


def test_profile_without_own_channel_never_uses_someone_elses(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(notify, "send_ntfy", lambda topic, *a, **k: calls.append(topic))
    sp, dd = paths(tmp_path)
    run(cfg2(), {"NTFY_TOPIC": "topico-pedro"}, WED, sp, dd, fetch=lambda s: [POLICY, OPS], detail=lambda j: j)
    assert calls == ["topico-pedro"]                   # a vaga da Marta não vai para o tópico do Pedro
    calls.clear()
    sp2, dd2 = str(tmp_path / "s2.json"), str(tmp_path / "d2")
    run(cfg2(), {"NTFY_TOPIC": "topico-pedro", "NTFY_TOPIC_MARTA": "topico-marta"}, WED, sp2, dd2,
        fetch=lambda s: [POLICY, OPS], detail=lambda j: j)
    assert sorted(calls) == ["topico-marta", "topico-pedro"]


def test_rules_are_inherited_and_overridden():
    ps = {p["id"]: p for p in build_profiles(cfg2())}
    assert ps["pedro"]["cfg"]["keywords"] is not ps["marta"]["cfg"]["keywords"]
    assert ps["marta"]["cfg"]["max_age_days"] == ps["pedro"]["cfg"]["max_age_days"]   # herdado
    assert ps["marta"]["cfg"]["years"]["max"] == 5 and ps["pedro"]["admin"] and not ps["marta"]["admin"]


def test_no_repeats_on_next_run(tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    send = lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"]
    for _ in range(2):                                  # amanhã: as mesmas vagas continuam no LinkedIn
        run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert sorted(sent) == ["marta", "pedro"]           # só na primeira vez


def test_new_profile_gets_jobs_already_seen_by_others(tmp_path):
    sp, dd = paths(tmp_path)
    only_pedro = cfg2()
    only_pedro["profiles"] = only_pedro["profiles"][:1]
    sent = []
    send = lambda t, b, c, p=None: sent.append((p["id"], b)) or ["ok"]
    run(only_pedro, {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert [p for p, _ in sent] == ["pedro"]
    sent.clear()
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)   # entra a Marta
    assert [p for p, _ in sent] == ["marta"] and "Policy Analyst" in sent[0][1]
    sent.clear()
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert sent == []
