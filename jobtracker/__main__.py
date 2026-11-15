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
from . import logos, notify, sources
from . import state as st
from .models import Job
from .scoring import cheap_filter, evaluate, label_of, pat_of, target_companies


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


PUBLIC_PROFILE_KEYS = ("id", "name", "headline", "tags", "languages", "cv_url", "linkedin")


def _human(pattern):
    """Primeira alternativa legível de um padrão (para mostrar na página): 'csrd|esg|...' -> 'csrd'."""
    first = re.split(r"(?<!\\)\|", pattern)[0]
    first = re.sub(r"\\b|\(\?:|[\^$?*+()\[\]{}]|\\", "", first)
    return re.sub(r"\s+", " ", first).strip()


def rules_summary(pcfg):
    """Resumo das regras de um perfil, sem nada pessoal, para a página de definições."""
    kw = pcfg.get("keywords", {})
    return {"require_theme": bool(pcfg.get("require_theme")), "min_score": pcfg.get("min_score"), "years": pcfg.get("years"), "max_age_days": pcfg.get("max_age_days"),
            "min_salary_eur": pcfg.get("min_salary_eur"),
            "strong": [x for x in (label_of(p) or _human(pat_of(p)) for p in kw.get("strong", [])) if x],
            "medium": [x for x in (label_of(p) or _human(pat_of(p)) for p in kw.get("medium", [])) if x],
            "strong_detail": [{"label": label_of(p) or _human(pat_of(p)),
                               "examples": (p.get("examples") if isinstance(p, dict) else None) or []}
                              for p in kw.get("strong", [])],
            "bonus": [b for b in ("associações e clusters" if (pcfg.get("org_bonus") or {}).get("pattern") else "",
                                  "utilities e energia" if (pcfg.get("sector_bonus") or {}).get("pattern") else "") if b],
            "companies": [{"sector": g.get("sector", ""),
                           "names": [c["name"] if isinstance(c, dict) else c for c in g.get("companies") or []]}
                          for g in pcfg.get("target_companies") or []]}


def build_profiles(cfg):
    """Perfis definidos em config.yaml (`profiles:`). Cada um herda as regras do topo do ficheiro e
    pode substituir qualquer uma em `rules:`. Sem `profiles:`, há um único perfil com as regras do topo."""
    base = {k: v for k, v in cfg.items() if k not in ("profiles", "sources")}
    raw = cfg.get("profiles") or [{"id": "eu", "admin": True}]
    out = []
    for i, p in enumerate(raw):
        pcfg = {**base, **(p.get("rules") or {})}
        out.append({"id": p["id"], "name": p.get("name", ""), "cfg": pcfg,
                    "admin": bool(p.get("admin", i == 0)),
                    "ntfy_env": p.get("ntfy_env", "NTFY_TOPIC"), "email_env": p.get("email_env", "EMAIL_TO"),
                    "profile_env": p.get("profile_env", "CANDIDATE_PROFILE"),
                    "public": {**{k: p[k] for k in PUBLIC_PROFILE_KEYS if p.get(k)}, "rules": rules_summary(pcfg),
                               "channel": bool(p.get("ntfy_env"))}})
    return out


def rename_profiles(state, cfg):
    """Um perfil que mudou de id (`former_ids`) herda tudo o que o id antigo já tinha avaliado,
    para não voltar a receber avisos de vagas que já tinha visto."""
    ren = {old: p["id"] for p in cfg.get("profiles") or [] for old in p.get("former_ids") or []}
    if not ren:
        return
    for book in (state.get("seen", {}), state.get("silent", {})):
        for m in book.values():
            if m.get("ev"):
                m["ev"] = sorted({ren.get(x, x) for x in m["ev"]})
    for m in state.get("matches", []):
        if isinstance(m.get("profiles"), dict):
            m["profiles"] = {ren.get(k, k): v for k, v in m["profiles"].items()}


def all_target_companies(cfg):
    """Empresas-alvo de todos os perfis, sem repetir (cada perfil só tem bónus pelas suas)."""
    out, seen = [], set()
    for p in build_profiles(cfg):
        for t in target_companies(p["cfg"]):
            if t[0] not in seen:
                seen.add(t[0])
                out.append(t)
    return out


def company_queries(src, cfg, today, main_run):
    """Pesquisas por nome de empresa-alvo, em rotação: `per_run` empresas em cada recolha."""
    names = [n for n, _, _ in all_target_companies(cfg)]
    per = int(src.get("per_run") or len(names) or 1)
    slices = max(1, -(-len(names) // per))
    k = (today.toordinal() * 2 + (0 if main_run else 1)) % slices
    return names[k * per:(k + 1) * per], f"empresas {k * per + 1} a {min(len(names), (k + 1) * per)} de {len(names)}"


def run(cfg, env, today, state_path, docs_dir, session=None, fetch=None, detail=None,
        send=None, dry_run=False, judge_fn=None, kit_fn=None, candidate_text=None, main_run=True):
    """main_run=False nas execuções extra do dia (ex.: à tarde): não repete "estou vivo" nem o radar."""
    session = session or requests.Session()
    fetch = fetch or (lambda src: sources.fetch_source(src, session, env))
    detail = detail or (lambda job: sources.fetch_detail(job, session))
    send = send or (lambda title, body, click, p=None: notify.dispatch(
        env, title, body, click, topic=env.get((p or {}).get("ntfy_env", "NTFY_TOPIC")) or "",
        email_to=env.get((p or {}).get("email_env", "EMAIL_TO")) or ""))   # sem canal próprio = nada (nunca o de outro)

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
    model = jcfg.get("model", judge_mod.DEFAULT_MODEL)
    profiles = build_profiles(cfg)
    for p in profiles:
        text = env.get(p["profile_env"]) or (candidate_text if p["admin"] else "")
        p["judge_fn"] = p["kit_fn"] = None
        if judge_on and (text or judge_fn or p["admin"]):
            p["judge_fn"] = judge_fn or (lambda j, t=text: judge_mod.judge(j, t, api_key, model, session))
            if kit_fn or api_key:
                p["kit_fn"] = kit_fn or (lambda j, v, t=text: kit_mod.build_kit(j, v, t, api_key, model, session))
    judge_budget = [jcfg.get("max_calls", 40)]
    min_fit = jcfg.get("min_fit", 55)

    state = st.load(state_path)
    report, health_alerts, all_jobs = [], [], {}
    rename_profiles(state, cfg)
    # Reavaliar: volta a avaliar com as regras atuais tudo o que ainda está na janela de recolha.
    # Mantém a data em que cada vaga foi vista e não volta a avisar quem já tinha avaliado a vaga.
    silent = state.setdefault("silent", {})
    if str(env.get("REEVALUATE", "")).lower() in ("1", "true", "yes", "sim"):
        first_pid = profiles[0]["id"]
        for u, m in state["seen"].items():
            silent[u] = {"ev": m.get("ev") or [first_pid], "first_seen": m.get("first_seen", today.isoformat())}
        state["seen"], state["matches"] = {}, []
        report.append(f"reavaliação: {len(silent)} vagas vistas voltam a ser avaliadas com as regras atuais, sem novos avisos")
    cutoff = (today.toordinal() - 30)
    for u in [u for u, m in silent.items() if date.fromisoformat(m["first_seen"]).toordinal() < cutoff]:
        silent.pop(u)

    # 1) recolher — uma fonte avariada nunca derruba as outras
    for src in cfg.get("sources", []):
        if src.get("enabled") is False:
            continue
        name = src.get("name") or src.get("type")
        h = state["health"].setdefault(name, {"fail_streak": 0, "last_ok": ""})
        ctx = {**src, "_known": state["seen"].keys(), "_cursor": state["cursors"].get(name)}
        rot = ""
        if src.get("companies_from") == "target_companies":
            ctx["queries"], rot = company_queries(src, cfg, today, main_run)
            ctx["_company_rx"] = "|".join(f"(?:{rx})" for _, _, rx in all_target_companies(cfg))
            if not ctx["queries"]:
                continue
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
        note = "; ".join(x for x in (rot, getattr(jobs, "note", "")) if x)
        report.append(f"{name}: ok, {len(jobs)} vagas" + (f" ({note})" if note else ""))
        for j in jobs:
            all_jobs.setdefault(j.uid, j)

    # 2) só o que ainda não vimos (pelo endereço e pela dupla empresa+título, entre fontes)
    # Cada vaga guarda que perfis já a avaliaram ("ev"). Um perfil acrescentado depois avalia as vagas que
    # ainda estão na janela de recolha, sem os outros perfis voltarem a ser avisados delas.
    all_ids = [p["id"] for p in profiles]
    first_id = all_ids[0]
    def evaluated(meta):
        return set(meta.get("ev") or [first_id])
    seen_keys = {m["k"] for m in state["seen"].values() if m.get("k")}
    new_jobs, todo = [], {}
    for u, j in all_jobs.items():
        if u in state["seen"]:
            old = state["seen"][u]
            missing = [pid for pid in all_ids if pid not in evaluated(old)]
            if missing and old.get("why") not in ("fechada", "duplicada noutra fonte"):
                todo[u] = missing
                new_jobs.append(j)
            continue
        k = dedupe_key(j)
        if k and k in seen_keys:
            state["seen"][u] = {"first_seen": today.isoformat(), "why": "duplicada noutra fonte", "k": k, "ev": all_ids}
            continue
        if k:
            seen_keys.add(k)
        todo[u] = all_ids
        new_jobs.append(j)
    # as mais prometedoras pelo título primeiro: o limite de páginas de detalhe gasta-se onde vale a pena
    title_rx = []
    for prof in profiles:                       # palavras-chave de todos os perfis
        kw = prof["cfg"].get("keywords", {})
        title_rx += [re.compile(pat_of(x), re.I) for x in kw.get("strong", []) + kw.get("medium", [])]
    new_jobs.sort(key=lambda j: -sum(1 for rx in title_rx if rx.search(j.title or "")))
    stats = {"seen": len(all_jobs), "new": sum(1 for u in todo if u not in state["seen"] and u not in silent), "passed": 0}
    li_fail = 0                                # falhas seguidas no detalhe do LinkedIn

    # 3) filtro barato -> detalhe (descrição completa, confirma que está aberta) -> pontuação por perfil
    matches, budget = [], cfg.get("max_detail_fetches", 60)
    for j in new_jobs:
        old = state["seen"].get(j.uid)
        mine = [p for p in profiles if p["id"] in todo[j.uid]]
        first_seen = (old or {}).get("first_seen") or (silent.get(j.uid) or {}).get("first_seen") or today.isoformat()
        meta = {**(old or {}), "first_seen": first_seen, "k": dedupe_key(j),
                "ev": sorted(evaluated(old) | set(todo[j.uid])) if old else list(todo[j.uid])}
        whys = [cheap_filter(j, p["cfg"], today) for p in mine]
        if all(whys):                          # não interessa a nenhum perfil (dos que faltam)
            state["seen"][j.uid] = {**meta, "why": meta.get("why") if old else whys[0]}
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
        hits, seen_meta = {}, {**meta}
        if old:
            seen_meta.pop("score", None)
        for p in mine:
            v = evaluate(j, p["cfg"], today)
            seen_meta.setdefault("score", v.score)
            seen_meta.setdefault("why", v.excluded_by)
            if len(profiles) > 1:
                seen_meta.setdefault("per", {})[p["id"]] = v.score if v.include else v.excluded_by
            if not v.include:
                continue
            jd = None
            if p["judge_fn"] and judge_budget[0] > 0:
                judge_budget[0] -= 1
                try:
                    jd = p["judge_fn"](j)
                except Exception as e:           # falha aberta: sem juiz, vale a pontuação
                    report.append(f"juiz falhou ({j.company}): {e}")
            if jd and (jd["decisao"] == "ignorar" or jd["fit"] < min_fit):
                seen_meta.setdefault("judge_low", []).append(p["id"])
                continue
            hits[p["id"]] = (v, jd)
        if hits:
            seen_meta["why"] = ""
        elif old:
            seen_meta["why"] = old.get("why", "")
        elif seen_meta.get("judge_low"):
            seen_meta["why"] = "juiz: baixo encaixe"
        seen_meta.pop("judge_low", None)
        state["seen"][j.uid] = seen_meta
        if not hits:
            continue
        matches.append({"job": j, "hits": hits, "quiet": (silent.get(j.uid) or {}).get("ev", [])})
        # a página só leva dados públicos da vaga; as notas do juiz ficam fora se o repositório for público
        first_v, first_jd = next(iter(hits.values()))
        prev = next((x for x in state["matches"] if x.get("url") == j.url), None)
        if prev is not None:                   # já está na página (outro perfil): acrescenta este perfil
            prev.setdefault("profiles", {first_id: {"score": prev.get("score"), "flags": prev.get("flags", [])}})
            for pid, (v, jd) in hits.items():
                prev["profiles"][pid] = {"score": v.score, "flags": v.flags[:4], "themes": v.themes[:3],
                                         **({"fit": jd["fit"]} if jd else {})}
            continue
        entry = {**j.to_dict(), "score": first_v.score, "flags": first_v.flags[:4], "first_seen": meta["first_seen"],
                 "profiles": {pid: {"score": v.score, "flags": v.flags[:4], "themes": v.themes[:3],
                                    **({"fit": jd["fit"]} if jd else {})} for pid, (v, jd) in hits.items()}}
        if not pub:
            entry.update({"reasons": first_v.reasons[:6], "judge": first_jd})
        elif first_jd:
            entry["fit"] = first_jd["fit"]
        state["matches"].append(entry)
    stats["passed"] = len(matches)
    for u in [u for u in silent if u in state["seen"]]:    # já reavaliadas: deixam de estar em espera
        silent.pop(u)

    # 4) avisar — cada perfil recebe só as suas vagas no seu canal
    report_sent, title, body, channels = {}, "", "", []
    page = cfg.get("click_url") or ""
    for p in profiles:
        mine = [{"job": m["job"], "verdict": m["hits"][p["id"]][0], "judge": m["hits"][p["id"]][1]}
                for m in matches if p["id"] in m["hits"] and p["id"] not in (m.get("quiet") or ())]
        mine.sort(key=lambda m: -(m["judge"]["fit"] if m.get("judge") else m["verdict"].score * 5))
        top = mine[: cfg.get("max_per_alert", 5)]
        click = (page + ("#" + p["id"] if len(profiles) > 1 and page else "")) or None

        # kits de candidatura só para os melhores (tu revês e submetes; nada é enviado por mim)
        kit_min, kit_max = jcfg.get("kit_min_fit", 70), jcfg.get("max_kits", 3)
        made = 0
        if p["kit_fn"] and not dry_run:
            for m in top:
                jd = m.get("judge")
                if made >= kit_max or not jd or jd["fit"] < kit_min:
                    continue
                try:
                    md = p["kit_fn"](m["job"], jd)
                except Exception as e:
                    report.append(f"kit falhou ({m['job'].company}): {e}")
                    continue
                name = f"{today.isoformat()}-{kit_mod.slug(m['job'].company + ' ' + m['job'].title)}.md"
                if pub:
                    try:
                        ok = notify.send_email(env, f"Kit de candidatura: {m['job'].title} — {m['job'].company}", md,
                                               to=env.get(p["email_env"]) or "")
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

        alerts = health_alerts if p["admin"] else []
        heartbeat = p["admin"] and main_run and not mine and today.weekday() == cfg.get("heartbeat_weekday", 0)
        t, b = notify.build_message(top, alerts, stats, total=len(mine), page_url=click)
        if len(profiles) > 1 and p["name"]:
            t = f"{p['name']}: {t}" if mine else t
        if heartbeat or alerts:
            b += "\n\nEstado das fontes:\n" + "\n".join(f"- {r}" for r in report)
        if p["admin"] or not title:
            title, body = t, b
        if (mine or alerts or heartbeat) and not dry_run:
            res = send(t, b, click, p)
            report_sent[p["id"]] = res
            if p["admin"] or not channels:
                channels = res
        if main_run and not dry_run and today.weekday() == cfg.get("radar_weekday", 4):
            week = [m for m in state["matches"] if p["id"] in (m.get("profiles") or {p["id"]: 1})]
            rt, rb = notify.build_radar(week, today)
            if rb:
                send(rt, rb, click, p)

    logos.apply(state, state["matches"])
    if not dry_run:
        st.save(state_path, state, today)
        os.makedirs(docs_dir, exist_ok=True)
        with open(os.path.join(docs_dir, "jobs.json"), "w", encoding="utf-8") as f:
            tracker = {"sources": [{"name": s.get("name") or s.get("type"), "type": s.get("type"),
                                    "queries": len(s.get("queries") or []), "status": next(
                                        (r.split(": ", 1)[1] for r in report if r.startswith((s.get("name") or "") + ": ")), "")}
                                   for s in cfg.get("sources", []) if s.get("enabled") is not False],
                       "schedule": cfg.get("schedule_text", "Todos os dias, por volta das 7h15 e das 16h15"),
                       "stats": stats}
            json.dump({"updated": today.isoformat(), "profiles": [p["public"] for p in profiles], "tracker": tracker,
                       "matches": state["matches"]}, f, ensure_ascii=False)

    return {"stats": stats, "report": report, "title": title, "body": body,
            "channels": channels, "matches": matches, "sent": report_sent}


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
