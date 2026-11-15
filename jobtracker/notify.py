"""Notificações: ntfy (push no iPhone) e email. Ambos opcionais, ligados por variáveis de ambiente."""
import smtplib
from email.message import EmailMessage

import requests


def build_message(matches, health_alerts, stats, total=None, page_url=None):
    """matches: lista de dicts {job, verdict} (as melhores). total: quantas passaram ao todo.
    Devolve (título, corpo) em português."""
    total = len(matches) if total is None else total
    if matches:
        title = f"{total} vaga(s) nova(s) para ti"
    else:
        title = "Tracker de vagas: sem novidades"
    lines = []
    for m in matches:
        j, v = m["job"], m["verdict"]
        where = f" ({j.location})" if j.location else ""
        src = j.source.split(":")[0]
        jd = m.get("judge")
        tag = f"fit {jd['fit']}/100" if jd else f"{v.score} pts"
        lines.append(f"• {j.title} — {j.company}{where} [{tag} · {src}]")
        if jd:
            lines.append(f"  {jd['porque']}")
            if jd["vale_sair"]:
                lines.append(f"  ★ Vale mudar de emprego: {jd['porque_sair']}")
            for a in jd["alertas"][:2]:
                lines.append(f"  ⚠ {a}")
        if m.get("kit"):
            lines.append(f"  Kit de candidatura: {m['kit']}")
        if not jd and v.flags:
            lines.append("  ⚠ " + "; ".join(v.flags[:3]))
        lines.append(f"  {j.url}")
    if total > len(matches):
        lines.append("")
        lines.append(f"+ {total - len(matches)} outra(s) na página" + (f": {page_url}" if page_url else "."))
    elif matches and page_url:
        lines.append("")
        lines.append(f"Todas as vagas: {page_url}")
    if health_alerts:
        lines.append("")
        lines.append("Fontes com problemas:")
        lines += [f"- {a}" for a in health_alerts]
    lines.append("")
    lines.append(f"Vistas: {stats['seen']} | novas: {stats['new']} | passaram: {stats['passed']}")
    return title, "\n".join(lines)


def send_ntfy(topic, title, body, click=None, priority=3, server="https://ntfy.sh", session=None):
    """Publica em JSON (suporta acentos no título)."""
    payload = {"topic": topic, "title": title, "message": body[:3800], "priority": priority,
               "tags": ["briefcase"]}
    if click:
        payload["click"] = click
    r = (session or requests).post(server, json=payload, timeout=20)
    r.raise_for_status()


def send_email(env, title, body, to=None):
    host, user, pw = env.get("SMTP_HOST"), env.get("SMTP_USER"), env.get("SMTP_PASS")
    to = env.get("EMAIL_TO") if to is None else to     # "" = este perfil não tem email
    if not all([host, user, pw, to]):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, user, to
    msg.set_content(body)
    with smtplib.SMTP_SSL(host, int(env.get("SMTP_PORT", 465)), timeout=30) as s:
        s.login(user, pw)
        s.send_message(msg)
    return True


def dispatch(env, title, body, click=None, topic=None, email_to=None):
    """Envia por todos os canais configurados (de um perfil). Devolve a lista de canais usados / erros."""
    results = []
    topic = topic if topic is not None else env.get("NTFY_TOPIC")
    if topic:
        try:
            send_ntfy(topic, title, body, click=click)
            results.append("ntfy: ok")
        except Exception as e:  # um canal avariado não impede os outros
            results.append(f"ntfy: ERRO {e}")
    try:
        if send_email(env, title, body, to=email_to):
            results.append("email: ok")
    except Exception as e:
        results.append(f"email: ERRO {e}")
    if not results:
        results.append("nenhum canal configurado (define NTFY_TOPIC e/ou SMTP_*)")
    return results


def build_radar(matches, today):
    """Resumo semanal (sexta-feira): o que apareceu nos últimos 7 dias. Devolve (título, corpo) ou (_, "")."""
    from datetime import date, timedelta
    lo = today - timedelta(days=7)
    week = []
    for m in matches:
        try:
            d = date.fromisoformat(m.get("first_seen", ""))
        except ValueError:
            continue
        if d > lo:
            week.append(m)
    if not week:
        return "Radar semanal", ""
    week.sort(key=lambda m: -((m.get("judge") or {}).get("fit", m.get("score", 0) * 5)))
    lines = [f"Esta semana: {len(week)} vaga(s) passaram os filtros."]
    cos = {}
    for m in week:
        cos[m["company"]] = cos.get(m["company"], 0) + 1
    lines.append("Empresas a contratar: " + ", ".join(f"{c} ({n})" for c, n in sorted(cos.items(), key=lambda x: -x[1])[:6]))
    leave = [m for m in week if (m.get("judge") or {}).get("vale_sair")]
    lines.append(f"Vagas que valem mudar de emprego: {len(leave)}" if leave else "Nenhuma vaga justificou mudar de emprego.")
    lines.append("")
    for m in week[:5]:
        jd = m.get("judge")
        lines.append(f"• {m['title']} — {m['company']}" + (f" [fit {jd['fit']}]" if jd else ""))
        lines.append(f"  {m['url']}")
    return "Radar semanal de vagas", "\n".join(lines)
