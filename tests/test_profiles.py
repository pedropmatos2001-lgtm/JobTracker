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
BOTH = mk("Gestor de Fundos Europeus e Sustentabilidade", "3 anos de experiência. Administração pública: candidaturas a fundos, "
          "Portugal 2030, cooperação internacional, sustentabilidade e economia circular, melhoria de processos.", company="CCDR Norte")


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
    assert set(by) == {"pedro", "maria"}
    assert "Operations Excellence" in by["pedro"][1] and "Policy Analyst" not in by["pedro"][1]
    assert "Policy Analyst" in by["maria"][1] and "Operations Excellence" not in by["maria"][1]
    assert by["maria"][0].startswith("Maria: 1 vaga") and by["maria"][2].endswith("#maria")
    m = {x["title"]: x for x in docs["matches"]}
    assert set(m["Operations Excellence Manager"]["profiles"]) == {"pedro"}
    assert set(m["Policy Analyst – EU Affairs"]["profiles"]) == {"maria"}


def test_job_for_both_is_stored_once_with_both_profiles(tmp_path):
    r, sent, docs = go(cfg2(), [BOTH], tmp_path)
    assert len(docs["matches"]) == 1 and set(docs["matches"][0]["profiles"]) == {"pedro", "maria"}
    assert sorted(p for p, *_ in sent) == ["maria", "pedro"]


def test_page_gets_only_public_profile_fields(tmp_path):
    _, _, docs = go(cfg2(), [OPS], tmp_path)
    ids = [p["id"] for p in docs["profiles"]]
    assert ids == ["pedro", "maria"]
    for p in docs["profiles"]:
        assert not ({"ntfy_env", "email_env", "profile_env", "admin"} & set(p))
        assert "keywords" not in p["rules"] and not any("|" in k for k in p["rules"]["strong"])   # só rótulos legíveis


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
    assert calls == ["topico-pedro"]                   # a vaga da Maria não vai para o tópico do Pedro
    calls.clear()
    sp2, dd2 = str(tmp_path / "s2.json"), str(tmp_path / "d2")
    run(cfg2(), {"NTFY_TOPIC": "topico-pedro", "NTFY_TOPIC_MARIA": "topico-maria"}, WED, sp2, dd2,
        fetch=lambda s: [POLICY, OPS], detail=lambda j: j)
    assert sorted(calls) == ["topico-maria", "topico-pedro"]


def test_rules_are_inherited_and_overridden():
    ps = {p["id"]: p for p in build_profiles(cfg2())}
    assert ps["pedro"]["cfg"]["keywords"] is not ps["maria"]["cfg"]["keywords"]
    assert ps["maria"]["cfg"]["max_age_days"] == ps["pedro"]["cfg"]["max_age_days"]   # herdado
    assert ps["maria"]["cfg"]["years"]["max"] == 5 and ps["pedro"]["admin"] and not ps["maria"]["admin"]


def test_no_repeats_on_next_run(tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    send = lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"]
    for _ in range(2):                                  # amanhã: as mesmas vagas continuam no LinkedIn
        run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert sorted(sent) == ["maria", "pedro"]           # só na primeira vez


def test_new_profile_gets_jobs_already_seen_by_others(tmp_path):
    sp, dd = paths(tmp_path)
    only_pedro = cfg2()
    only_pedro["profiles"] = only_pedro["profiles"][:1]
    sent = []
    send = lambda t, b, c, p=None: sent.append((p["id"], b)) or ["ok"]
    run(only_pedro, {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert [p for p, _ in sent] == ["pedro"]
    sent.clear()
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)   # entra a Maria
    assert [p for p, _ in sent] == ["maria"] and "Policy Analyst" in sent[0][1]
    sent.clear()
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert sent == []


def test_page_entry_has_matched_themes(tmp_path):
    _, _, docs = go(cfg2(), [OPS], tmp_path)
    th = docs["matches"][0]["profiles"]["pedro"]["themes"]
    assert th and all("|" not in t for t in th) and len(th) <= 3


def test_reevaluate_applies_new_rules_without_new_alerts(tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    send = lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"]
    old = cfg2()
    old["profiles"] = old["profiles"][:1]
    old["min_score"] = 99                               # regras antigas: a vaga de operações não passou
    run(old, {}, WED, sp, dd, fetch=lambda s: [OPS], detail=lambda j: j, send=send)
    assert sent == [] and json.load(open(f"{dd}/jobs.json"))["matches"] == []
    from datetime import date
    later = date(2026, 11, 19)                        # quinta-feira (à sexta há o resumo semanal)
    only_pedro = cfg2()
    only_pedro["profiles"] = only_pedro["profiles"][:1]
    run(only_pedro, {"REEVALUATE": "true"}, later, sp, dd, fetch=lambda s: [OPS], detail=lambda j: j, send=send)
    docs = json.load(open(f"{dd}/jobs.json"))
    assert [m["title"] for m in docs["matches"]] == ["Operations Excellence Manager"]   # passa com as regras novas
    assert docs["matches"][0]["first_seen"] == WED.isoformat()                         # mantém a data original
    assert sent == []                                                                  # e não volta a avisar
    run(only_pedro, {}, later, sp, dd, fetch=lambda s: [OPS], detail=lambda j: j, send=send)
    assert sent == [] and len(json.load(open(f"{dd}/jobs.json"))["matches"]) == 1      # não duplica depois


def test_renamed_profile_keeps_what_the_old_id_had_seen(tmp_path):
    sp, dd = paths(tmp_path)
    old = cfg2()
    old["profiles"][1] = {**old["profiles"][1], "id": "marta", "name": "Marta"}
    old["profiles"][1].pop("former_ids", None)
    sent = []
    send = lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"]
    run(old, {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)
    assert sorted(sent) == ["marta", "pedro"]
    sent.clear()
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [OPS, POLICY], detail=lambda j: j, send=send)   # agora Maria
    assert sent == []                                   # a Maria não volta a receber o que a Marta já tinha visto
    docs = json.load(open(f"{dd}/jobs.json", encoding="utf-8"))
    assert {"maria"} <= set(p for m in docs["matches"] for p in m["profiles"])
    assert "marta" not in json.dumps(docs)


def test_target_companies_get_bonus_only_for_pedro():
    from datetime import date
    from jobtracker.scoring import evaluate, target_company
    ps = {p["id"]: p["cfg"] for p in build_profiles(cfg2())}
    j = mk("Process Manager", "Melhoria de processos.", company="Turbogás - Produtora Energética, S.A.")
    assert target_company(j, ps["pedro"]) == "Turbogás"
    assert target_company(j, ps["maria"]) == ""
    assert any("empresa-alvo" in r for r in evaluate(j, ps["pedro"], date(2026, 11, 18)).reasons)


def test_company_source_rotates_and_keeps_only_target_companies():
    from datetime import date
    from jobtracker.__main__ import all_target_companies, company_queries
    c = cfg2()
    total = len(all_target_companies(c))               # empresas-alvo do Pedro + Big4 da Maria
    assert total > 90
    src = {"companies_from": "target_companies", "per_run": 24}
    seen, d = [], date(2026, 11, 18)
    runs = -(-total // 24)
    for i in range(runs):
        q, note = company_queries(src, c, date.fromordinal(d.toordinal() + i // 2), i % 2 == 0)
        assert q and f"de {total}" in note
        seen += q
    assert len(set(seen)) == total                      # passa por todas antes da janela de 7 dias acabar
    assert runs / 2 < 7


def test_big4_and_gps_count_for_maria_not_pedro():
    from datetime import date
    from jobtracker.scoring import evaluate, target_company
    ps = {p["id"]: p["cfg"] for p in build_profiles(cfg2())}
    j = mk("Consultora – Government & Public Services (GPS)", "2 anos de experiência. Consultoria ao setor público: "
           "modernização da administração pública e fundos europeus para ministérios.", company="Deloitte")
    assert target_company(j, ps["maria"]) == "Deloitte" and target_company(j, ps["pedro"]) == ""
    v = evaluate(j, ps["maria"], date(2026, 11, 18))
    assert v.include and "Consultoria ao setor público" in v.themes


def test_pedro_skips_big4_but_gets_other_consultancies():
    from datetime import date
    from jobtracker.scoring import evaluate, target_company
    ps = {p["id"]: p["cfg"] for p in build_profiles(cfg2())}
    t = date(2026, 11, 18)
    desc = ("4 years of experience. Public sector consulting: operating model, process improvement and EU funds "
            "for ministries; sustainability strategy.")
    big4 = mk("Senior Consultant – Government & Public Services", desc, company="KPMG Portugal")
    assert evaluate(big4, ps["pedro"], t).excluded_by == "empresa excluída deste perfil"
    other = mk("Senior Consultant – Public Sector", desc, company="Sia Partners")
    v = evaluate(other, ps["pedro"], t)
    assert v.include and target_company(other, ps["pedro"]) == "Sia Partners"
    assert evaluate(big4, ps["maria"], t).excluded_by != "empresa excluída deste perfil"


def test_pedro_has_public_sector_consulting_theme():
    from datetime import date
    from jobtracker.scoring import evaluate
    ps = {p["id"]: p["cfg"] for p in build_profiles(cfg2())}
    j = mk("Consultor Sénior – Setor Público", "4 anos de experiência. Consultoria ao setor público: modelo operativo "
           "e melhoria de processos para ministérios e câmaras.", company="Sia Partners")
    v = evaluate(j, ps["pedro"], date(2026, 11, 18))
    assert v.include and "Consultoria ao setor público" in v.themes


def test_ongd_source_filter_and_maria_gets_relevant_ngo_jobs(tmp_path):
    from jobtracker import sources
    c = cfg2()
    ongd = next(s for s in yaml.safe_load(open("config.yaml", encoding="utf-8"))["sources"] if s["type"] == "ongd")
    c["sources"] = [ongd]
    jobs = sources.parse_ongd(open("tests/fixtures/ongd.html", encoding="utf-8").read())
    sp, dd = tmp_path / "s.json", tmp_path / "docs"
    r = run(c, {}, WED, str(sp), str(dd), fetch=lambda s: jobs, detail=lambda j: j, send=lambda *a, **k: ["ok"])
    docs = json.load(open(f"{dd}/jobs.json", encoding="utf-8"))
    titles = {m["title"]: m["profiles"] for m in docs["matches"]}
    assert "Farmacêuticos/as" not in titles                       # saiu pelo filtro da fonte
    assert "maria" in titles.get("Gestor/a de Projetos de Cooperação e Advocacy", {})


def test_rules_change_reevaluates_seen_jobs_without_alerts(tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    send = lambda t, b, c, p=None: sent.append(p["id"]) or ["ok"]
    nature = mk("Global Nature Strategy Coordinator", "4 years of experience. Nature-positive strategy and natural capital.",
                company="Continental")
    strict = cfg2()
    strict["min_score"] = 30                                  # regra antiga: nada passa
    run(strict, {}, WED, sp, dd, fetch=lambda s: [nature], detail=lambda j: j, send=send)
    assert json.load(open(f"{dd}/jobs.json", encoding="utf-8"))["matches"] == []
    run(cfg2(), {}, WED, sp, dd, fetch=lambda s: [nature], detail=lambda j: j, send=send)   # regras novas
    docs = json.load(open(f"{dd}/jobs.json", encoding="utf-8"))
    assert [m["title"] for m in docs["matches"]] == ["Global Nature Strategy Coordinator"]
    assert sent == []                                         # já tinha sido vista: entra na página sem novo aviso
