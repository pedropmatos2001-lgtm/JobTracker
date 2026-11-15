"""Orquestração: recolher -> deduplicar -> filtrar -> ir buscar detalhe -> pontuar -> avisar."""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timezone

import requests
import yaml

from . import judge as judge_mod
from . import kit as kit_mod
from . import notify, sources
from . import state as st
from .models import Job
from .scoring import cheap_filter, evaluate


def dedupe_key(job):
    """Empresa + título normalizados: a mesma vaga no LinkedIn, no Indeed e no site da empresa conta uma vez."""
    def norm(x):
        x = unicodedata.normalize("NFKD", x or "").encode("ascii", "ignore").decode().lower()
        x = re.sub(r"\(.*?\)", " ", x)                    # "EDP (Porto)" -> "edp"
        x = re.sub(r"\b(m/f|h/m|f/m|m/w|all genders?)\b", " ", x)
        x = re.sub(r"\b(s\.?a|lda|unipessoal|sgps|group|grupo|portugal)\b", " ", x)
        return " ".join(re.sub(r"[^a-z0-9]+", " ", x).split())
    c, t = norm(job.company), norm(job.title)
    return f"{c}|{t}" if c and t else ""


def run(cfg, env, today, state_path, docs_dir, session=None, fetch=None, detail=None,
        send=None, dry_run=False, judge_fn=None, kit_fn=None, candidate_text=None, main_run=True):
    """main_run=False nas execuções extra do dia (ex.: à tarde): não repete "estou vivo" nem o radar."""
    session = session or requests.Session()
    fetch = fetch or (lambda src: sources.fetch_source(src, session, env))
    detail = detail or (lambda job: sources.fetch_detail(job, session))
    send = send or (lambda title, body, click: notify.dispatch(env, title, body, click))

    jcfg = cfg.get("judge", {}) or {}
    api_key = env.get("ANTHROPIC_API_KEY")
    judge_on = bool(jcfg.get("enabled", True)) and (judge_fn is not None or bool(api_key))
    pub = bool(cfg.get("public_repo", True))        # repositório público: nada pessoal vai para o git
    if candidate_text is None and env.get("CANDIDATE_PROFILE"):
        candidate_text = env["CANDIDATE_PROFILE"]
    if env.get("MIN_SALARY_EUR"):
        cfg["min_salary_eur"] = int(env["MIN_SALARY_EUR"])
    if candidate_text is None:
        try:
            with open(jcfg.get("candidate_file", "candidate.md"), encoding="utf-8") as f:
                candidate_text = f.read()
        except OSError:
            candidate_text = ""
    if judge_on and judge_fn is None:
        model = jcfg.get("model", judge_mod.DEFAULT_MODEL)
        judge_fn = lambda j: judge_mod.judge(j, candidate_text, api_key, model, session)
    if judge_on and kit_fn is None and api_key:
        model = jcfg.get("model", judge_mod.DEFAULT_MODEL)
        kit_fn = lambda j, v: kit_mod.build_kit(j, v, candidate_text, api_key, model, session)
    judge_budget = jcfg.get("max_calls", 40)
    min_fit = jcfg.get("min_fit", 55)

    state = st.load(state_path)
    report, health_alerts, all_jobs = [], [], {}

    # 1) recolher — uma fonte avariada nunca derruba as outras
    for src in cfg.get("sources", []):
        name = src.get("name") or src.get("type")
        h = state["health"].setdefault(name, {"fail_streak": 0, "last_ok": ""})
        ctx = {**src, "_known": state["seen"].keys(), "_cursor": state["cursors"].get(name)}
        try:
            jobs = fetch(ctx)
        except sources.SourceSkipped as e:
            report.append(f"{name}: ignorada ({e})")
            continue
        except Exception as e:
            h["fail_streak"] += 1
            report.append(f"{name}: ERRO {e}")
            if h["fail_streak"] >= cfg.get("alert_after_failures", 3):
                health_alerts.append(f"{name}: {h['fail_streak']} falhas seguidas ({e})")
            continue
        h["fail_streak"], h["last_ok"] = 0, today.isoformat()
        if getattr(jobs, "cursor", None) is not None:
            state["cursors"][name] = jobs.cursor
        note = getattr(jobs, "note", "")
        report.append(f"{name}: ok, {len(jobs)} vagas" + (f" ({note})" if note else ""))
        for j in jobs:
            all_jobs.setdefault(j.uid, j)

    # 2) só o que ainda não vimos (pelo endereço e pela dupla empresa+título, entre fontes)
    seen_keys = {m["k"] for m in state["seen"].values() if m.get("k")}
    new_jobs = []
    for u, j in all_jobs.items():
        if u in state["seen"]:
            continue
        k = dedupe_key(j)
        if k and k in seen_keys:
            state["seen"][u] = {"first_seen": today.isoformat(), "why": "duplicada noutra fonte", "k": k}
            continue
        if k:
            seen_keys.add(k)
        new_jobs.append(j)
    # as mais prometedoras pelo título primeiro: o limite de páginas de detalhe gasta-se onde vale a pena
    kw = cfg.get("keywords", {})
    title_rx = [re.compile(p, re.I) for p in kw.get("strong", []) + kw.get("medium", [])]
    title_rx.append(re.compile(cfg.get("senior_title", "senior|manager|lead"), re.I))
    new_jobs.sort(key=lambda j: -sum(1 for rx in title_rx if rx.search(j.title or "")))
    stats = {"seen": len(all_jobs), "new": len(new_jobs), "passed": 0}
    li_fail = 0                                # falhas seguidas no detalhe do LinkedIn

    # 3) filtro barato -> detalhe (descrição completa, confirma que está aberta) -> pontuação
    matches, budget = [], cfg.get("max_detail_fetches", 60)
    for j in new_jobs:
        meta = {"first_seen": today.isoformat(), "k": dedupe_key(j)}
        why = cheap_filter(j, cfg, today)
        if why:
            state["seen"][j.uid] = {**meta, "why": why}
            continue
        is_li = "linkedin_id" in (j.extra or {})
        if sources.needs_detail(j) and is_li and li_fail >= 3:
            j.partial = True                  # LinkedIn está a limitar: avalia só pelo cartão (título)
        elif sources.needs_detail(j):
            if budget <= 0:
                continue                      # fica por ver; tenta na próxima execução
            budget -= 1
            orig = j
            try:
                j = detail(orig)
            except Exception as e:
                if not is_li:
                    report.append(f"detalhe falhou ({orig.company}): {e}")
                    continue                  # tenta na próxima execução
                li_fail += 1
                if li_fail == 3:
                    report.append(f"LinkedIn a limitar o detalhe ({e}): restantes avaliadas só pelo título")
                j = orig
                j.partial = True
            else:
                if is_li:
                    li_fail = 0
            if j is None:                     # a fonte diz que já fechou
                state["seen"][orig.uid] = {**meta, "why": "fechada"}
                continue
        v = evaluate(j, cfg, today)
        state["seen"][j.uid] = {**meta, "score": v.score, "why": v.excluded_by}
        if v.include:
            jd = None
            if judge_on and judge_budget > 0:
                judge_budget -= 1
                try:
                    jd = judge_fn(j)
                except Exception as e:           # falha aberta: sem juiz, vale a pontuação
                    report.append(f"juiz falhou ({j.company}): {e}")
            if jd and (jd["decisao"] == "ignorar" or jd["fit"] < min_fit):
                state["seen"][j.uid]["why"] = "juiz: baixo encaixe"
                state["seen"][j.uid]["fit"] = jd["fit"]
                continue
            if jd:
                state["seen"][j.uid]["fit"] = jd["fit"]
            matches.append({"job": j, "verdict": v, "judge": jd})
            # a página só leva dados públicos da vaga; as notas do juiz ficam fora se o repositório for público
            entry = {**j.to_dict(), "score": v.score, "flags": v.flags[:4], "first_seen": today.isoformat()}
            if not pub:
                entry.update({"reasons": v.reasons[:6], "judge": jd})
            elif jd:
                entry["fit"] = jd["fit"]
            state["matches"].append(entry)
    stats["passed"] = len(matches)

    # 4) avisar
    def rank(m):
        return -(m["judge"]["fit"] if m.get("judge") else m["verdict"].score * 5)
    top = sorted(matches, key=rank)[: cfg.get("max_per_alert", 5)]

    # kits de candidatura só para os melhores (tu revês e submetes; nada é enviado por mim)
    kit_min, kit_max = jcfg.get("kit_min_fit", 70), jcfg.get("max_kits", 3)
    made = 0
    if kit_fn and not dry_run:
        for m in top:
            jd = m.get("judge")
            if made >= kit_max or not jd or jd["fit"] < kit_min:
                continue
            try:
                md = kit_fn(m["job"], jd)
            except Exception as e:
                report.append(f"kit falhou ({m['job'].company}): {e}")
                continue
            name = f"{today.isoformat()}-{kit_mod.slug(m['job'].company + ' ' + m['job'].title)}.md"
            if pub:
                try:
                    ok = notify.send_email(env, f"Kit de candidatura: {m['job'].title} — {m['job'].company}", md)
                except Exception as e:
                    ok = False
                    report.append(f"kit por email falhou: {e}")
                if not ok:
                    continue
                m["kit"] = "enviado por email"
            else:
                os.makedirs(os.path.join(docs_dir, "kits"), exist_ok=True)
                with open(os.path.join(docs_dir, "kits", name), "w", encoding="utf-8") as f:
                    f.write(md)
                m["kit"] = f"docs/kits/{name}"
            made += 1

    heartbeat = main_run and not matches and today.weekday() == cfg.get("heartbeat_weekday", 0)
    title, body = notify.build_message(top, health_alerts, stats, total=len(matches),
                                       page_url=cfg.get("click_url"))
    if heartbeat or health_alerts:
        body += "\n\nEstado das fontes:\n" + "\n".join(f"- {r}" for r in report)
    channels = []
    if (matches or health_alerts or heartbeat) and not dry_run:
        channels = send(title, body, cfg.get("click_url"))
    if main_run and not dry_run and today.weekday() == cfg.get("radar_weekday", 4):
        rt, rb = notify.build_radar(state["matches"], today)
        if rb:
            send(rt, rb, cfg.get("click_url"))

    if not dry_run:
        st.save(state_path, state, today)
        os.makedirs(docs_dir, exist_ok=True)
        with open(os.path.join(docs_dir, "jobs.json"), "w", encoding="utf-8") as f:
            json.dump({"updated": today.isoformat(), "matches": state["matches"]}, f, ensure_ascii=False)

    return {"stats": stats, "report": report, "title": title, "body": body,
            "channels": channels, "matches": matches}


def load_fixture(path):
    with open(path, encoding="utf-8") as f:
        return [Job(**d) for d in json.load(f)]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jobtracker")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--state", default="state/state.json")
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--dry-run", action="store_true", help="não envia nem grava nada")
    ap.add_argument("--offline", metavar="FIXTURE.json", help="usa vagas de ficheiro em vez da rede")
    a = ap.parse_args(argv)

    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    kw = {}
    if a.offline:
        jobs = load_fixture(a.offline)
        first = cfg["sources"][0]["name"]
        kw = {"fetch": lambda src: jobs if src.get("name") == first else [], "detail": lambda j: j}
    # a primeira execução do dia (manhã) é a principal; a da tarde só avisa de vagas novas
    main_run = datetime.now(timezone.utc).hour < 12 or bool(os.environ.get("FORCE_MAIN_RUN"))
    res = run(cfg, os.environ, date.today(), a.state, a.docs, dry_run=a.dry_run, main_run=main_run, **kw)

    print("\n".join(res["report"]))
    print(f"\n{res['stats']}")
    print(f"\n=== {res['title']} ===\n{res['body']}")
    print("\ncanais:", res["channels"] or "(nada enviado)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
