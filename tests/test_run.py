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
    return c


def good(url="https://a/1", **kw):
    base = dict(source="t", company="Siemens", title="AI Transformation Consultant", url=url,
                location="Lisbon, Portugal", posted="2026-11-16",
                description="3+ years of experience. Process re-engineering and agentic AI. "
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

    def send(title, body, click):
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
    assert "AI Transformation Consultant" in sent[0][1]
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
    jobs = [good(url=f"https://a/{i}") for i in range(8)]
    r = run(cfg, {}, WED, sp, dd, **harness({"A": jobs, "B": []}, sent))
    assert r["stats"]["passed"] == 8 and sent[0][1].count("https://a/") == cfg["max_per_alert"]
