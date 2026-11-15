import json
from datetime import date

import pytest
import yaml

from jobtracker import sources
from jobtracker.__main__ import run
from jobtracker.models import Job

WED = date(2026, 11, 18)
MON = date(2026, 11, 16)


@pytest.fixture
def cfg():
    with open("config.yaml", encoding="utf-8") as f:
        c = yaml.safe_load(f)
    c["sources"] = [{"type": "x", "name": "A"}, {"type": "x", "name": "B"}]
    c["public_repo"] = False
    c.pop("profiles", None)          # testes de um perfil; os de vários perfis estão em test_profiles.py
    return c


def good(url="https://a/1", **kw):
    base = dict(source="t", company="Siemens", title="Sustainability Transformation Consultant", url=url,
                location="Lisbon, Portugal", posted="2026-11-16",
                description="3+ years of experience. Process re-engineering and agentic AI. Operating model design for a sustainability programme. "
                            "Stakeholder management, requirements.")
    base.update(kw)
    return Job(**base)


def harness(jobs_by_source, sent, closed=()):
    def fetch(src):
        out = jobs_by_source[src["name"]]
        if isinstance(out, Exception):
            raise out
        return out

    def detail(j):
        return None if j.url in closed else j

    def send(title, body, click, profile=None):
        sent.append((title, body))
        return ["fake: ok"]
    return dict(fetch=fetch, detail=detail, send=send)


def paths(tmp_path):
    return str(tmp_path / "state.json"), str(tmp_path / "docs")


def test_new_match_is_sent_once(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    h = harness({"A": [good()], "B": []}, sent)
    r1 = run(cfg, {}, WED, sp, dd, **h)
    assert r1["stats"] == {"seen": 1, "new": 1, "passed": 1} and len(sent) == 1
    assert "Sustainability Transformation Consultant" in sent[0][1]
    r2 = run(cfg, {}, WED, sp, dd, **h)                 # mesmo dia seguinte: nada de novo
    assert r2["stats"]["new"] == 0 and len(sent) == 1
    docs = json.load(open(f"{dd}/jobs.json", encoding="utf-8"))
    assert docs["matches"][0]["company"] == "Siemens"


def test_duplicate_across_sources_counts_once(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    r = run(cfg, {}, WED, sp, dd, **harness({"A": [good()], "B": [good()]}, sent))
    assert r["stats"]["seen"] == 1 and r["stats"]["passed"] == 1


def test_one_failing_source_does_not_stop_the_others(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    r = run(cfg, {}, WED, sp, dd, **harness({"A": sources.SourceError("down"), "B": [good()]}, sent))
    assert r["stats"]["passed"] == 1 and any("A: ERRO" in x for x in r["report"])


def test_failure_streak_triggers_health_alert(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    h = harness({"A": sources.SourceError("down"), "B": []}, sent)
    for day in (16, 17):
        run(cfg, {}, date(2026, 11, day + 1), sp, dd, **h)       # ter, qua
    assert not sent
    run(cfg, {}, date(2026, 11, 19), sp, dd, **h)                # 3.ª falha seguida
    assert len(sent) == 1 and "3 falhas seguidas" in sent[0][1]


def test_skipped_source_is_not_a_failure(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    r = run(cfg, {}, WED, sp, dd, **harness({"A": sources.SourceSkipped("sem chave"), "B": []}, sent))
    assert any("ignorada" in x for x in r["report"]) and not sent


def test_heartbeat_only_on_monday(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    h = harness({"A": [], "B": []}, sent)
    run(cfg, {}, WED, sp, dd, **h)
    assert not sent
    run(cfg, {}, MON, sp, dd, **h)
    assert len(sent) == 1 and "Estado das fontes" in sent[0][1]


def test_closed_job_is_dropped_and_remembered(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    j = good(extra={"workable_account": "g", "shortcode": "A"}, description="")
    h = harness({"A": [j], "B": []}, sent, closed={j.url})
    r = run(cfg, {}, WED, sp, dd, **h)
    assert r["stats"]["passed"] == 0
    state = json.load(open(sp, encoding="utf-8"))
    assert state["seen"][j.url]["why"] == "fechada"


def test_detail_failure_is_retried_next_day(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    j = good(extra={"workable_account": "g", "shortcode": "A"}, description="")
    h = harness({"A": [j], "B": []}, sent)

    def boom(job):
        raise RuntimeError("timeout")
    run(cfg, {}, WED, sp, dd, **{**h, "detail": boom})
    assert j.url not in json.load(open(sp, encoding="utf-8"))["seen"]     # não ficou marcada como vista
    j2 = good(extra={"workable_account": "g", "shortcode": "A"}, description="")
    j2.description = good().description
    r = run(cfg, {}, date(2026, 11, 19), sp, dd, **harness({"A": [j2], "B": []}, sent))
    assert r["stats"]["passed"] == 1


def test_dry_run_writes_and_sends_nothing(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    r = run(cfg, {}, WED, sp, dd, dry_run=True, **harness({"A": [good()], "B": []}, sent))
    assert r["stats"]["passed"] == 1 and not sent
    assert not (tmp_path / "state.json").exists()


def test_max_per_alert(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    jobs = [good(url=f"https://a/{i}", company=f"Empresa {i}") for i in range(8)]
    r = run(cfg, {}, WED, sp, dd, **harness({"A": jobs, "B": []}, sent))
    assert r["stats"]["passed"] == 8 and sent[0][1].count("https://a/") == cfg["max_per_alert"]


def test_same_job_on_two_boards_counts_once(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    a = good(url="https://www.linkedin.com/jobs/view/1", company="Siemens S.A.")
    b = good(url="https://pt.indeed.com/viewjob?jk=1", company="SIEMENS", title="Sustainability Transformation Consultant (m/f)")
    r = run(cfg, {}, WED, sp, dd, **harness({"A": [a], "B": [b]}, sent))
    assert r["stats"]["new"] == 1 and r["stats"]["passed"] == 1
    r2 = run(cfg, {}, WED, sp, dd, **harness({"A": [], "B": [good(url="https://x/2", company="Siemens")]}, sent))
    assert r2["stats"]["new"] == 0                     # também entre execuções


def test_linkedin_detail_throttled_falls_back_to_title(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    jobs = [Job(source="linkedin", company=f"Co {i}", title="Sustainability Transformation Manager",
                url=f"https://www.linkedin.com/jobs/view/{i}", location="Lisbon, Portugal",
                posted="2026-11-17", extra={"linkedin_id": str(i)}) for i in range(5)]
    calls = []
    def detail(j):
        calls.append(j.url)
        raise sources.SourceError("HTTP 429")
    h = harness({"A": jobs, "B": []}, sent)
    h["detail"] = detail
    r = run(cfg, {}, WED, sp, dd, **h)
    assert len(calls) == 3                             # depois de 3 falhas deixa de insistir
    assert r["stats"]["passed"] == 5                   # mas avalia pelo título e avisa na mesma
    assert "descrição parcial" in sent[0][1] or "parcial" in json.dumps(r["report"])


def test_message_points_to_page_when_more_than_max(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    cfg["click_url"] = "https://exemplo.github.io/JobTracker/"
    jobs = [good(url=f"https://a/{i}", company=f"Empresa {i}") for i in range(8)]
    run(cfg, {}, WED, sp, dd, **harness({"A": jobs, "B": []}, sent))
    assert sent[0][0].startswith("8 vaga") and f"+ {8 - cfg['max_per_alert']} outra(s) na página: https://exemplo.github.io" in sent[0][1]


def test_afternoon_run_skips_heartbeat(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    sent = []
    run(cfg, {}, MON, sp, dd, main_run=False, **harness({"A": [], "B": []}, sent))
    assert sent == []


def test_cursor_is_saved_and_passed_back(cfg, tmp_path):
    sp, dd = paths(tmp_path)
    got = []
    def fetch(src):
        got.append(src.get("_cursor"))
        out = sources.JobList([])
        out.cursor = {"last": 42}
        return out
    run(cfg, {}, WED, sp, dd, fetch=fetch, detail=lambda j: j, send=lambda *a: [])
    run(cfg, {}, WED, sp, dd, fetch=fetch, detail=lambda j: j, send=lambda *a: [])
    assert got == [None, None, {"last": 42}, {"last": 42}]
