# Tracker de vagas

Corre sozinho **duas vezes por dia, todos os dias** (≈07:15 e ≈16:15 UTC) no GitHub, procura vagas em **todo o Portugal continental**, filtra pelo teu perfil e:

- manda uma **notificação push** (ntfy) com as melhores vagas novas;
- atualiza uma **página com todas as vagas** (filtros por fonte e data, marcar "candidatei-me"/"esconder").

Não depende do Claude nem do teu portátil ligado.

## Vários perfis (ex.: Pedro e Marta)

A recolha é feita uma vez; cada perfil avalia as vagas com as suas regras (`profiles:` no `config.yaml`) e:
- recebe **só as suas** notificações, no seu tópico ntfy (`NTFY_TOPIC` para o Pedro, `NTFY_TOPIC_MARTA` para a Marta);
- tem a sua vista na página: a barra lateral troca de perfil (ou abre diretamente `…/JobTracker/#marta`).

Uma vaga só é notificada uma vez por perfil. Se acrescentares um perfil novo, ele avalia as vagas que ainda
estão na janela de recolha (últimas 72 h), sem os outros perfis voltarem a receber nada.

**Acrescentar uma pessoa:** copia o bloco da Marta em `profiles:`, muda `id`, `name`, as regras e o nome dos
segredos (`ntfy_env`), cria esse segredo no Environment `GERAL` e acrescenta-o ao `env:` do workflow.
Nunca ponhas contactos ou o CV no `config.yaml` (o repositório é público); usa `cv_url` com uma ligação privada.

## De onde vêm as vagas

| Fonte | O que cobre | Precisas de escolher empresas? |
|---|---|---|
| **LinkedIn** (pesquisa pública, sem login) | quase todas as empresas que recrutam em Portugal | Não |
| **Indeed Portugal** (biblioteca JobSpy) | muitas vagas que não estão no LinkedIn | Não |
| **BEP – Bolsa de Emprego Público** | governo central, CCDR, institutos públicos, câmaras | Não |
| Sites de empresas (EDP, Galp, Greenvolt, …) | vagas que às vezes não vão para o LinkedIn | Sim (opcional) |
| Remotive | remoto para a Europa | Não |

O LinkedIn é lido pelas páginas públicas que o LinkedIn mostra a quem não tem sessão: **não usa a tua conta**, por isso não há risco de restrição. Se o LinkedIn limitar pedidos vindos do GitHub, o tracker fica com o que já recolheu e avalia o resto só pelo título (avisa "descrição parcial").

Madeira e Açores ficam de fora (`locations.exclude` no `config.yaml`). Na BEP só passam carreiras de técnico superior e cargos de direção, e ficam fora mobilidades e concursos internos (exigem vínculo à função pública). Para mudar isto, edita o bloco `bep` no `config.yaml`.

## Como funciona

1. **Recolher** de todas as fontes. Uma fonte avariada nunca derruba as outras.
2. **Deduplicar**: pelo endereço e pela dupla empresa + título (a mesma vaga no LinkedIn e no Indeed conta uma vez).
3. **Filtro barato**: título (sem estágios, júnior, engenharia de software…), local, idade (≤ 14 dias).
4. **Detalhe**: descrição completa das mais prometedoras primeiro (até 120 por execução; o resto fica para a seguinte).
5. **Pontuação** (`config.yaml`): palavras-chave do teu perfil, nível, anos pedidos, línguas, salário.
6. **Avisar**: até 6 vagas na notificação; as restantes ficam na página.

## Página com todas as vagas (GitHub Pages)

Uma vez só: no repositório, *Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs` → Save*. A página fica em `https://pedropmatos2001-lgtm.github.io/JobTracker/` (já configurada em `click_url`: tocar na notificação abre-a). No iPhone, abre no Safari → Partilhar → "Adicionar ao ecrã principal".

A página só mostra dados públicos das vagas (título, empresa, local, ligação). O teu perfil, salário e notas do juiz nunca vão para o repositório. As marcações "candidatei-me"/"esconder" ficam guardadas só no teu telemóvel.

## Instalar (15 minutos)

1. Cria um repositório **privado** no GitHub e carrega esta pasta toda (inclui `.github/`).
2. **iPhone**: instala a app **ntfy** (App Store) e subscreve um tópico com nome impossível de adivinhar, por exemplo `vagas-pedro-7f3k9x2q`. Quem souber o nome lê as mensagens; trata-o como uma palavra-passe.
3. No repositório: *Settings → Secrets and variables → Actions → New repository secret*:
   - `NTFY_TOPIC` = o nome do tópico (obrigatório para o push).
   - Email, se quiseres: `SMTP_HOST`, `SMTP_PORT` (465), `SMTP_USER`, `SMTP_PASS` (no Gmail, uma palavra-passe de aplicação), `EMAIL_TO`.
4. Separador *Actions* → "Vagas diárias" → **Run workflow**. Vê os registos: cada fonte diz `ok, N vagas` ou o erro.
5. Página com todas as vagas: ver secção acima.

## Acrescentar empresas

Procura a página de carreiras da empresa e vê o endereço:

| Endereço contém | Tipo | Campo |
|---|---|---|
| `apply.workable.com/NOME/` | `workable` | `account: NOME` |
| `boards.greenhouse.io/NOME` ou `job-boards.eu.greenhouse.io/NOME` | `greenhouse` | `token: NOME` |
| `jobs.lever.co/NOME` | `lever` | `company: NOME` |
| `jobs.smartrecruiters.com/NOME` | `smartrecruiters` | `company: NOME` |
| `xxx.wdN.myworkdayjobs.com/SITE` | `workday` | `tenant: xxx`, `wd: wdN`, `site: SITE` |
| `xxx.jobs.personio.de` | `personio` | `account: xxx` |
| `xxx.teamtailor.com` (ou domínio próprio com /jobs.rss) | `teamtailor` | `host: xxx.teamtailor.com` |
| `xxx.recruitee.com` | `recruitee` | `company: xxx` |
| outra (SuccessFactors, página própria) | `page_watch` | `url` + `link_regex` |

Em `page_watch`, cada ligação nova é aberta e lida pela marcação JSON-LD `JobPosting` (a mesma que o Google usa): se existir, o tracker fica com data, local e salário (mensal conta x14) e descarta a vaga se `validThrough` já passou. Se a página não tiver essa marcação, a vaga passa como "por confirmar".

## Limites (a ler)

- **Os formatos das APIs foram escritos pela documentação pública e não foram chamados ao vivo** (o ambiente onde isto foi construído não tinha acesso a essas páginas). Os testes usam respostas simuladas. A primeira execução real é o teste verdadeiro; se uma fonte vier com erro, o registo diz qual e porquê.
- **LinkedIn e Indeed**: são lidos sem conta, mas os termos de utilização destes sites não preveem recolha automática e podem mudar o formato ou limitar pedidos sem aviso. O registo de cada execução diz quantas vagas veio de cada um; se uma fonte falhar 3 vezes seguidas, recebes aviso.
- **BEP**: não tem API; o tracker lê cada oferta pelo número sequencial. Se a BEP mudar o endereço das ofertas, a fonte começa a falhar e recebes aviso.
- Muitas empresas grandes (Big 4, Siemens, EDP) usam sistemas sem API pública. Para essas, `page_watch` funciona só se a página não depender de JavaScript; caso contrário avisa que devolveu 0 ligações.
- O Adzuna não cobre Portugal e está desligado (o adaptador fica no código).
- Quase nenhuma vaga publica salário. Vagas sem salário passam com o aviso "sem salário indicado".

## Exemplos de fontes novas (copia para `sources:` do `config.yaml` só depois de confirmares o endereço)

```yaml
  - {type: workday, name: "Empresa X", tenant: xxx, wd: wd3, site: SITE, search: "sustainability"}
  - {type: personio, name: "Empresa Y", account: yyy}
  - {type: teamtailor, name: "Empresa Z", host: zzz.teamtailor.com}
  - {type: recruitee, name: "Empresa W", company: www}
```

O Workday usa um endereço não documentado e pode mudar sem aviso.

## Testar localmente

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m jobtracker --offline tests/fixtures/sample_jobs.json --dry-run
```

## Empresas do Norte já ligadas (verificado em nov. 2026)

| Empresa | Como é vigiada |
|---|---|
| EDP (Porto, Lisboa) | `page_watch` na pesquisa do portal jobs.edp.com |
| Porto Ambiente | `page_watch` na página /recrutamento (vagas em PDF) |
| LIPOR | `page_watch` na página de procedimentos concursais |
| Quadrante, Indaqua, Águas do Douro e Paiva, grupo AdP | sem lista pública; só candidatura espontânea / LinkedIn. Cobertas pela tarefa diária Claude |
| Galp, BP, REN, Efacec, Águas do Norte, Floene | páginas de carreiras ainda por confirmar |

Para acrescentar mais, copia um bloco `page_watch` do `config.yaml`.

## Juiz de IA, kits de candidatura e radar semanal (v3)

**Juiz de IA.** Cada vaga que passa nos filtros é comparada com o teu perfil (segredo `CANDIDATE_PROFILE`; `candidate.example.md` é só o modelo) por um modelo da Anthropic. Devolve um *fit* de 0 a 100, a decisão (candidatar / talvez / ignorar), os riscos concretos e se **vale mudar de emprego**. Abaixo de `min_fit` (55) não recebes aviso. Se a API falhar ou não houver chave, o tracker cai para as palavras-chave e continua a funcionar.

**Repositório público.** Se o repositório for público, NUNCA ponhas dados pessoais em ficheiros. Guarda o teu perfil no segredo `CANDIDATE_PROFILE` (e, se quiseres, o salário mínimo em `MIN_SALARY_EUR`). Com `public_repo: true` (por defeito) o tracker não grava vagas, notas do juiz nem kits no git; os kits seguem só por email (precisa de `SMTP_*`) e o radar semanal fica desligado.

Para ligar: cria uma chave em console.anthropic.com e guarda-a no GitHub em *Settings → Secrets and variables → Actions* com o nome `ANTHROPIC_API_KEY`. O custo é limitado por `max_calls` (40 avaliações por dia, normalmente poucos cêntimos). **Edita o `candidate.md`** com o teu CV real: quanto mais concreto, melhor o juiz.

**Kits de candidatura.** Para as vagas com fit ≥ 70 (máx. 3 por dia) o tracker grava um rascunho de carta, 5 pontos do CV a destacar, perguntas prováveis de entrevista e o que confirmar. O prompt proíbe inventar experiência. Tu revês e submetes.

**Radar semanal.** À sexta-feira recebes um resumo: quantas vagas passaram, que empresas estão a contratar e se alguma justificou mudar de emprego.

### Candidaturas automáticas: porque não
Tecnicamente seria possível, mas não o recomendo nem o ligo:
- O LinkedIn proíbe automação de candidaturas e restringe contas por isso. Perder a conta no meio de uma procura é um risco real.
- Uma candidatura enviada sem ti lê-se como lixo ao nível de recrutador, e para consultoria e setor público a qualidade pesa mais do que a quantidade.
- Cada formulário tem perguntas sobre salário, disponibilidade e línguas que devem ser respondidas por ti.

O meio-termo que o tracker faz: tudo o que antecede o clique (encontrar, avaliar, redigir carta e CV adaptados). O clique final é teu e demora 2 minutos.
